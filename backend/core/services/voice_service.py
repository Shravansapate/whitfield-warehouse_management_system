"""Voice assistant AI service — Gemini tool-calling agent over WMS data.

This service implements the five voice tools defined in the final implementation
plan and exposes a single entry-point ``run_voice_turn`` that processes one
spoken utterance from an authenticated user.

All reads use the actor's warehouse scope.  The ``add_receipt_item`` tool
returns a ``pending_confirmation`` payload that the WebSocket route must
hold for explicit verbal confirmation before committing.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser
from backend.core import logger
from backend.core.cruds.inventory_crud import InventoryCRUD
from backend.core.cruds.product_crud import ProductCRUD
from backend.core.cruds.receiving_crud import ReceivingCRUD
from backend.core.models.enums import AuditSource, ReceiptStatus
from backend.core.models.inventory import InventoryBalance
from backend.core.models.order import Order
from backend.core.models.product import Product
from backend.core.models.receiving import InboundReceipt, InboundReceiptItem

logging = logger(__name__)

# ---------------------------------------------------------------------------
# Gemini function declarations for the 5 voice tools
# ---------------------------------------------------------------------------

VOICE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_product",
        "description": (
            "Search for a product by UPC barcode, SKU code, or product name. "
            "Returns the first matching products with their IDs, SKUs, UPCs and names."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "UPC number, SKU code, or product name fragment to search for",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_inventory",
        "description": (
            "Check current stock levels for a product. Returns on-hand, reserved, "
            "and available (on_hand minus reserved) quantities for the user's warehouse."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "product_query": {
                    "type": "STRING",
                    "description": "UPC, SKU or product name to look up inventory for",
                }
            },
            "required": ["product_query"],
        },
    },
    {
        "name": "get_receipt_status",
        "description": (
            "Look up the status and items of an inbound receipt by its tracking number "
            "or ticket number."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tracking_or_ticket": {
                    "type": "STRING",
                    "description": "Carrier tracking number or drop-off ticket number",
                }
            },
            "required": ["tracking_or_ticket"],
        },
    },
    {
        "name": "add_receipt_item",
        "description": (
            "Add a scanned product line to an open inbound receipt. "
            "This is a write action — the assistant MUST read back the receipt, warehouse, "
            "product, accepted quantity, and damaged quantity to the user before calling this "
            "tool, and MUST only call it after the user explicitly confirms. "
            "Parameters: receipt_id (UUID), product_upc (UPC barcode), "
            "received (total units scanned), accepted (units accepted as good), "
            "damaged (units damaged). accepted + damaged must equal received."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receipt_id": {
                    "type": "STRING",
                    "description": "UUID of the open inbound receipt",
                },
                "product_upc": {
                    "type": "STRING",
                    "description": "UPC barcode of the scanned product",
                },
                "received": {
                    "type": "INTEGER",
                    "description": "Total units scanned from the shipment",
                },
                "accepted": {
                    "type": "INTEGER",
                    "description": "Units accepted as good sellable stock",
                },
                "damaged": {
                    "type": "INTEGER",
                    "description": "Units identified as damaged (not added to sellable inventory)",
                },
                "damage_notes": {
                    "type": "STRING",
                    "description": "Optional description of damage (required when damaged > 0)",
                },
            },
            "required": ["receipt_id", "product_upc", "received", "accepted", "damaged"],
        },
    },
    {
        "name": "get_order_status",
        "description": (
            "Look up the current status and items of an outbound order by its "
            "external reference number or order ID."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "order_reference": {
                    "type": "STRING",
                    "description": "External order reference or order UUID",
                }
            },
            "required": ["order_reference"],
        },
    },
]

SYSTEM_PROMPT = """You are the Whitfield WMS voice assistant. You help warehouse staff
with receiving, inventory checks, and order status queries using voice commands.

CRITICAL SAFETY RULES you must always follow:
1. For add_receipt_item: ALWAYS read back the product name, receipt tracking number, 
   warehouse, accepted quantity, and damaged quantity BEFORE calling the tool.
   Only call add_receipt_item after the user says "yes", "confirm", or "correct".
2. Keep responses short and clear — staff are working in a warehouse.
3. Never make up data — only report what the tools return.
4. Always confirm the user's warehouse context when doing write operations.
5. If you cannot find something, say so clearly and ask for clarification.
"""


@dataclass
class PendingConfirmation:
    """A write action waiting for verbal confirmation."""

    tool_name: str
    tool_args: dict[str, Any]
    readback_text: str
    idempotency_key: str = field(default_factory=lambda: secrets.token_urlsafe(16))


@dataclass
class VoiceTurnResult:
    """Result of processing one voice turn."""

    response_text: str
    pending_confirmation: PendingConfirmation | None = None
    committed: bool = False


class VoiceService:
    """Gemini-based voice agent that executes WMS tools for authenticated staff."""

    def __init__(self) -> None:
        """Initialize tool collaborators."""
        self.products = ProductCRUD()
        self.inventory = InventoryCRUD()
        self.receiving = ReceivingCRUD()

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    async def run_voice_turn(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        transcript: str,
        conversation_history: list[dict[str, Any]],
        pending: PendingConfirmation | None = None,
    ) -> VoiceTurnResult:
        """Process one spoken utterance and return an agent response.

        If a write action is pending confirmation and the user says yes/confirm,
        the write is committed.  Otherwise Gemini handles the turn normally.

        Args:
            session: Active database session for tool execution.
            user: Authenticated actor whose warehouse scope is enforced.
            transcript: User's recognized speech text.
            conversation_history: Running message history for multi-turn context.
            pending: Outstanding write action waiting for confirmation.

        Returns:
            VoiceTurnResult with response text and optional new pending action.
        """
        logging.info(
            "Executing VoiceService.run_voice_turn user_id=%s transcript_len=%d",
            user.id,
            len(transcript),
        )

        # Handle confirmation flow for pending writes
        if pending is not None:
            confirmed = _is_confirmation(transcript)
            cancelled = _is_cancellation(transcript)
            if confirmed:
                result_text = await self._execute_write(
                    session, user=user, pending=pending
                )
                return VoiceTurnResult(
                    response_text=result_text, pending_confirmation=None, committed=True
                )
            if cancelled:
                return VoiceTurnResult(
                    response_text="Understood. The receipt item was not added. What else can I help you with?",
                    pending_confirmation=None,
                )
            # User said something else — re-ask
            return VoiceTurnResult(
                response_text=(
                    f"I still need your confirmation. {pending.readback_text} "
                    "Say 'confirm' to save this item or 'cancel' to discard it."
                ),
                pending_confirmation=pending,
            )

        try:
            import google.generativeai as genai  # type: ignore[import]
            from backend.core.config import get_settings

            settings = get_settings()
            if not settings.gemini_api_key or not settings.gemini_api_key.startswith("AIzaSy"):
                return await self._fallback_handle_utterance(session, user=user, transcript=transcript)

            genai.configure(api_key=settings.gemini_api_key)
            model_name = settings.gemini_model or "gemini-2.0-flash"
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT,
                tools=VOICE_TOOLS,  # type: ignore[arg-type]
            )

            history_contents = [
                {"role": msg["role"], "parts": [{"text": msg["content"]}]}
                for msg in conversation_history
            ]
            chat = model.start_chat(history=history_contents)
            response = await chat.send_message_async(transcript)

            # Check if Gemini called a tool
            for part in response.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fn = part.function_call
                    tool_name = fn.name
                    tool_args = dict(fn.args)

                    logging.info("Voice agent calling tool=%s args=%s", tool_name, tool_args)

                    if tool_name == "add_receipt_item":
                        # Safety: queue for confirmation, never auto-commit
                        readback = await self._build_receipt_item_readback(
                            session, user=user, args=tool_args
                        )
                        pending_confirmation = PendingConfirmation(
                            tool_name=tool_name,
                            tool_args=tool_args,
                            readback_text=readback,
                        )
                        return VoiceTurnResult(
                            response_text=f"{readback} Say 'confirm' to save this item or 'cancel' to discard it.",
                            pending_confirmation=pending_confirmation,
                        )

                    # Execute read-only tools directly
                    tool_result = await self._execute_read_tool(
                        session, user=user, tool_name=tool_name, args=tool_args
                    )
                    # Send tool result back to Gemini for final text response
                    follow_up = await chat.send_message_async(
                        {
                            "parts": [
                                {
                                    "function_response": {
                                        "name": tool_name,
                                        "response": tool_result,
                                    }
                                }
                            ]
                        }
                    )
                    return VoiceTurnResult(response_text=follow_up.text or "Done.")

            # No tool call — plain text response
            return VoiceTurnResult(response_text=response.text or "I'm not sure how to help with that.")

        except ImportError:
            return VoiceTurnResult(
                response_text=(
                    "Voice AI requires the google-generativeai package. "
                    "Run: pip install google-generativeai"
                )
            )
        except Exception as error:
            error_str = str(error)
            logging.warning("Gemini AI API returned error (%s). Falling back to local WMS processor.", error_str)
            # Automatic fallback to local pattern matcher so queries work seamlessly!
            return await self._fallback_handle_utterance(session, user=user, transcript=transcript)

    async def _fallback_handle_utterance(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        transcript: str,
    ) -> VoiceTurnResult:
        """Local rule-based fallback handler when Gemini API is unavailable or invalid."""
        raw = transcript.strip()
        clean = raw.lower()

        if any(w in clean for w in ["hello", "hi", "hey", "help", "who are you"]):
            return VoiceTurnResult(
                response_text=(
                    "Hello! I am your WMS voice assistant. You can ask me: "
                    "'check inventory for WF-LOCK-114', 'search product camera', "
                    "'check stock for camera', or 'order status ORD-DEMO-001'."
                )
            )

        # Inventory check
        if "inventory" in clean or "stock" in clean or "how many" in clean:
            query = clean
            for prefix in [
                "check inventory for", "check inventory of", "check inventory",
                "check the stock for", "check stock for", "check stock of", "check stock",
                "stock of", "stock for", "how many", "inventory of", "inventory for", "inventory"
            ]:
                if prefix in query:
                    query = query.replace(prefix, " ")
            # Remove filler words
            tokens = [w for w in query.split() if w not in ["the", "a", "an", "product", "products", "item", "items", "do", "we", "have", "in", "warehouse", "stock", "at", "for", "please"]]
            clean_query = " ".join(tokens).strip(" ?:.,;\"'!") or clean
            
            res = await self._tool_check_inventory(session, user=user, product_query=clean_query)
            if not res.get("found"):
                return VoiceTurnResult(response_text=res.get("message", f"No inventory found for {clean_query}."))
            prod = res["product"]
            inv = res.get("inventory", [])
            if not inv:
                return VoiceTurnResult(response_text=f"Product {prod['name']} ({prod['sku']}) has 0 inventory balances.")
            total_avail = sum(item["available"] for item in inv)
            total_on_hand = sum(item["on_hand"] for item in inv)
            total_res = sum(item["reserved"] for item in inv)
            return VoiceTurnResult(
                response_text=(
                    f"Product {prod['name']} ({prod['sku']}): {total_on_hand} units on hand, "
                    f"{total_res} reserved, {total_avail} available for picking."
                )
            )

        # Product search
        if "search" in clean or "find" in clean or "product" in clean or "show" in clean:
            query = clean
            for prefix in [
                "search for the product", "search the product", "search product for",
                "search product", "search for", "search", "find the product",
                "find product for", "find product", "find", "show me the product",
                "show me product", "show me", "show"
            ]:
                if prefix in query:
                    query = query.replace(prefix, " ")
            tokens = [w for w in query.split() if w not in ["the", "a", "an", "product", "products", "item", "items", "details", "info", "please"]]
            clean_query = " ".join(tokens).strip(" ?:.,;\"'!") or clean

            res = await self._tool_search_product(session, query=clean_query)
            if not res.get("found"):
                return VoiceTurnResult(response_text=res.get("message", f"No product found matching '{clean_query}'."))
            items = res.get("products", [])
            names = [f"{p['name']} (SKU: {p['sku']})" for p in items[:3]]
            return VoiceTurnResult(
                response_text=f"Found {len(items)} product(s): {', '.join(names)}."
            )

        # Receipt status
        if "receipt" in clean or "tracking" in clean:
            query = clean
            for prefix in ["receipt status for", "receipt status", "receipt", "tracking status for", "tracking status", "tracking"]:
                if prefix in query:
                    query = query.replace(prefix, " ")
            tokens = [w for w in query.split() if w not in ["the", "a", "an", "number", "code", "status", "for", "please"]]
            clean_query = " ".join(tokens).strip(" ?:.,;\"'!") or clean

            res = await self._tool_get_receipt_status(session, user=user, tracking_or_ticket=clean_query)
            if not res.get("found"):
                return VoiceTurnResult(response_text=res.get("message", f"Receipt {clean_query} not found."))
            rec = res["receipt"]
            return VoiceTurnResult(
                response_text=(
                    f"Receipt {rec['id'][:8]} status is {rec['status']}, "
                    f"with {rec['item_count']} item line(s)."
                )
            )

        # Order status
        if "order" in clean:
            query = clean
            for prefix in ["order status for", "order status of", "order status", "order", "status of order", "status for order"]:
                if prefix in query:
                    query = query.replace(prefix, " ")
            tokens = [w for w in query.split() if w not in ["the", "a", "an", "number", "code", "status", "for", "of", "please"]]
            clean_query = " ".join(tokens).strip(" ?:.,;\"'!") or clean

            res = await self._tool_get_order_status(session, user=user, order_reference=clean_query)
            if not res.get("found"):
                return VoiceTurnResult(response_text=res.get("message", f"No order found matching '{clean_query}'."))
            ord_data = res["order"]
            return VoiceTurnResult(
                response_text=(
                    f"Order {ord_data['reference_number']} is currently {ord_data['status']} "
                    f"with {len(ord_data.get('items', []))} item(s)."
                )
            )

        return VoiceTurnResult(
            response_text=(
                f"I heard: '{transcript}'. You can ask me to check inventory, "
                "search for products, check receipt status, or look up orders."
            )
        )

    # ------------------------------------------------------------------
    # Read tool executors
    # ------------------------------------------------------------------

    async def _execute_read_tool(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a read-only tool call and return structured data."""
        if tool_name == "search_product":
            return await self._tool_search_product(session, query=str(args.get("query", "")))
        if tool_name == "check_inventory":
            return await self._tool_check_inventory(
                session, user=user, product_query=str(args.get("product_query", ""))
            )
        if tool_name == "get_receipt_status":
            return await self._tool_get_receipt_status(
                session, user=user, tracking_or_ticket=str(args.get("tracking_or_ticket", ""))
            )
        if tool_name == "get_order_status":
            return await self._tool_get_order_status(
                session, user=user, order_reference=str(args.get("order_reference", ""))
            )
        return {"error": f"Unknown tool: {tool_name}"}

    async def _tool_search_product(
        self, session: AsyncSession, *, query: str
    ) -> dict[str, Any]:
        """Search products by UPC/SKU/name."""
        logging.info("Voice tool search_product query=%s", query)
        products = await self.products.search(session, query=query, limit=5)
        if not products:
            return {"found": False, "message": f"No active product found matching '{query}'"}
        return {
            "found": True,
            "count": len(products),
            "products": [
                {
                    "id": str(p.id),
                    "sku": p.sku,
                    "upc": p.upc,
                    "name": p.name,
                    "description": p.description or "",
                }
                for p in products
            ],
        }

    async def _tool_check_inventory(
        self, session: AsyncSession, *, user: CurrentUser, product_query: str
    ) -> dict[str, Any]:
        """Check inventory balance for a product in the user's warehouse."""
        logging.info("Voice tool check_inventory query=%s user=%s", product_query, user.id)
        products = await self.products.search(session, query=product_query, limit=3)
        if not products:
            return {"found": False, "message": f"No product found matching '{product_query}'"}

        product = products[0]
        warehouse_id = user.warehouse_id

        if warehouse_id is None:
            # Owner — report across both warehouses
            statement = select(InventoryBalance).where(
                InventoryBalance.product_id == product.id
            )
        else:
            statement = select(InventoryBalance).where(
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.product_id == product.id,
            )

        balances = list((await session.scalars(statement)).all())
        if not balances:
            return {
                "found": True,
                "product": {"sku": product.sku, "name": product.name, "upc": product.upc},
                "inventory": [],
                "message": "No inventory balance exists for this product yet.",
            }

        return {
            "found": True,
            "product": {"sku": product.sku, "name": product.name, "upc": product.upc},
            "inventory": [
                {
                    "warehouse_id": str(b.warehouse_id),
                    "on_hand": b.on_hand,
                    "reserved": b.reserved,
                    "available": b.on_hand - b.reserved,
                }
                for b in balances
            ],
        }

    async def _tool_get_receipt_status(
        self, session: AsyncSession, *, user: CurrentUser, tracking_or_ticket: str
    ) -> dict[str, Any]:
        """Get receipt status by tracking or ticket number."""
        logging.info("Voice tool get_receipt_status ref=%s", tracking_or_ticket)
        ref = tracking_or_ticket.strip()
        statement = select(InboundReceipt).where(
            or_(
                InboundReceipt.tracking_number == ref,
                InboundReceipt.ticket_number == ref,
            )
        )
        if user.warehouse_id is not None:
            statement = statement.where(InboundReceipt.warehouse_id == user.warehouse_id)
        statement = statement.limit(1)
        receipt = (await session.execute(statement)).scalar_one_or_none()

        if receipt is None:
            return {"found": False, "message": f"No receipt found for '{ref}'"}

        # Load items
        items_statement = select(InboundReceiptItem, Product).join(
            Product, InboundReceiptItem.product_id == Product.id
        ).where(InboundReceiptItem.receipt_id == receipt.id)
        rows = list((await session.execute(items_statement)).all())

        return {
            "found": True,
            "receipt_id": str(receipt.id),
            "tracking_number": receipt.tracking_number,
            "ticket_number": receipt.ticket_number,
            "status": str(receipt.status),
            "sender_name": receipt.sender_name,
            "item_count": len(rows),
            "items": [
                {
                    "product_sku": product.sku,
                    "product_name": product.name,
                    "received": item.quantity_received,
                    "accepted": item.quantity_accepted,
                    "damaged": item.quantity_damaged,
                }
                for item, product in rows
            ],
        }

    async def _tool_get_order_status(
        self, session: AsyncSession, *, user: CurrentUser, order_reference: str
    ) -> dict[str, Any]:
        """Get order status by external reference or UUID."""
        logging.info("Voice tool get_order_status ref=%s", order_reference)
        ref = order_reference.strip()
        # Try UUID parse first, then external_reference
        try:
            order_id = uuid.UUID(ref)
            statement = select(Order).where(Order.id == order_id)
        except ValueError:
            statement = select(Order).where(Order.external_reference == ref)

        if user.warehouse_id is not None:
            statement = statement.where(Order.warehouse_id == user.warehouse_id)
        statement = statement.limit(1)
        order = (await session.execute(statement)).scalar_one_or_none()

        if order is None:
            return {"found": False, "message": f"No order found matching '{order_reference}'"}

        return {
            "found": True,
            "order_id": str(order.id),
            "external_reference": order.external_reference,
            "status": str(order.status),
            "warehouse_id": str(order.warehouse_id),
        }

    # ------------------------------------------------------------------
    # Confirmation flow helpers
    # ------------------------------------------------------------------

    async def _build_receipt_item_readback(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        args: dict[str, Any],
    ) -> str:
        """Build a verbal readback string before a receipt item write."""
        receipt_id_str = str(args.get("receipt_id", ""))
        upc = str(args.get("product_upc", ""))
        received = int(args.get("received", 0))
        accepted = int(args.get("accepted", 0))
        damaged = int(args.get("damaged", 0))

        # Resolve receipt
        try:
            receipt_uuid = uuid.UUID(receipt_id_str)
            receipt = await self.receiving.get_receipt(session, receipt_uuid)
        except (ValueError, Exception):
            receipt = None

        receipt_label = receipt.tracking_number or receipt.ticket_number if receipt else receipt_id_str

        # Resolve product name by UPC
        products = await self.products.search(session, query=upc, limit=1)
        product_name = products[0].name if products else upc

        warehouse_label = user.warehouse_name or str(user.warehouse_id or "your warehouse")

        parts = [
            f"I'm about to add {received} units of {product_name} (UPC {upc}) "
            f"to receipt {receipt_label} at {warehouse_label}. "
            f"Accepted: {accepted}. Damaged: {damaged}."
        ]
        if damaged > 0:
            notes = str(args.get("damage_notes", ""))
            parts.append(f"Damage notes: {notes or 'none provided'}.")

        return " ".join(parts)

    async def _execute_write(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        pending: PendingConfirmation,
    ) -> str:
        """Execute a confirmed write action via the WMS API route internally."""
        logging.info(
            "Voice confirmed write tool=%s user=%s", pending.tool_name, user.id
        )
        if pending.tool_name == "add_receipt_item":
            return await self._commit_add_receipt_item(session, user=user, args=pending.tool_args, key=pending.idempotency_key)
        return "Unknown write action."

    async def _commit_add_receipt_item(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        args: dict[str, Any],
        key: str,
    ) -> str:
        """Commit the add_receipt_item write after verbal confirmation."""
        from backend.core.apis.schemas.receiving import ReceiptItemRequest
        from backend.core.services.receiving_service import ReceivingService

        service = ReceivingService()
        try:
            receipt_uuid = uuid.UUID(str(args["receipt_id"]))
            upc = str(args["product_upc"])

            products = await self.products.search(session, query=upc, limit=1)
            if not products:
                return f"Could not find product with UPC {upc}. Receipt item was not saved."
            product = products[0]

            req = ReceiptItemRequest(
                product_id=product.id,
                quantity_received=int(args.get("received", 1)),
                quantity_accepted=int(args.get("accepted", 1)),
                quantity_damaged=int(args.get("damaged", 0)),
                damage_notes=str(args.get("damage_notes", "")) or None,
            )
            await service.add_item(
                session,
                receipt_id=receipt_uuid,
                request=req,
                idempotency_key=key,
                user=user,
                request_id=f"voice-{key}",
                source=AuditSource.VOICE,
            )
            return (
                f"Saved: {req.quantity_accepted} accepted, {req.quantity_damaged} damaged "
                f"for {product.name}. Inventory will not change until the receipt is completed."
            )
        except Exception as error:
            logging.error("Voice write failed error=%s", str(error))
            return f"Could not save the receipt item: {str(error)}"


# ---------------------------------------------------------------------------
# Confirmation helpers
# ---------------------------------------------------------------------------

_CONFIRM_WORDS = {"yes", "confirm", "correct", "confirmed", "save", "proceed", "go ahead", "affirmative", "ok", "okay", "yep", "yeah"}
_CANCEL_WORDS = {"no", "cancel", "stop", "discard", "abort", "never mind", "forget it", "nope"}


def _is_confirmation(text: str) -> bool:
    """Return True if the utterance is an explicit confirmation."""
    normalized = text.strip().lower()
    return any(word in normalized for word in _CONFIRM_WORDS)


def _is_cancellation(text: str) -> bool:
    """Return True if the utterance is an explicit cancellation."""
    normalized = text.strip().lower()
    return any(word in normalized for word in _CANCEL_WORDS)
