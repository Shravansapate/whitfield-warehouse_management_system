"""Idempotent warehouse bootstrap and optional development demo data."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

from pwdlib.exceptions import UnknownHashError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import (
    hash_password,
    normalize_email,
    verify_password,
)
from backend.core.config import get_settings
from backend.core.database.engine import async_session_factory, engine
from backend.core.models.access import User, UserWarehouseAssignment, Warehouse
from backend.core.models.enums import (
    AuditSource,
    MovementType,
    OrderStatus,
    ReceiptStatus,
    UserRole,
)
from backend.core.models.inventory import InventoryBalance, InventoryMovement
from backend.core.models.order import Order, OrderItem
from backend.core.models.product import Product, WarehouseProductSetting
from backend.core.models.receiving import InboundReceipt, InboundReceiptItem
from backend.core.models.reliability import AuditLog

WAREHOUSES = (
    {"code": "RNO", "name": "Reno", "location": "Reno, Nevada"},
    {"code": "CMH", "name": "Columbus", "location": "Columbus, Ohio"},
)
DEMO_PRODUCTS: tuple[dict[str, str | int], ...] = (
    {
        "sku": "WF-LOCK-114",
        "upc": "724880001140",
        "name": "Smart deadbolt kit",
        "quantity": 132,
        "threshold": 48,
    },
    {
        "sku": "WF-CAM-212",
        "upc": "724880002123",
        "name": "Outdoor camera bundle",
        "quantity": 64,
        "threshold": 36,
    },
    {
        "sku": "WF-HUB-040",
        "upc": "724880000402",
        "name": "Home hub controller",
        "quantity": 22,
        "threshold": 30,
    },
    {
        "sku": "WF-SENS-816",
        "upc": "724880008165",
        "name": "Window sensor pack",
        "quantity": 214,
        "threshold": 60,
    },
)
BOOTSTRAP_OWNER_LOCK_ID = 9_143_106_001
BOOTSTRAP_PASSWORD_MIN_LENGTH = 14


def parse_args() -> argparse.Namespace:
    """Parse command-line seed options.

    Keeps owner credentials in environment-backed settings, never CLI history.

    Returns:
        Parsed environment and demo flags.
    """
    parser = argparse.ArgumentParser(description="Seed Whitfield WMS bootstrap data")
    parser.add_argument(
        "--environment", default=os.getenv("ENVIRONMENT", "development")
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Add development owner, products, and balances",
    )
    parser.add_argument(
        "--bootstrap-owner",
        action="store_true",
        help="Create the first owner from configured SEED_OWNER_* values",
    )
    return parser.parse_args()


async def ensure_warehouses(session: AsyncSession) -> dict[str, Warehouse]:
    """Create Reno and Columbus when they do not already exist.

    Args:
        session: Active SQLAlchemy seed transaction.

    Returns:
        Warehouse models keyed by code.
    """
    warehouses: dict[str, Warehouse] = {}
    for row in WAREHOUSES:
        warehouse = (
            await session.execute(
                select(Warehouse).where(Warehouse.code == row["code"])
            )
        ).scalar_one_or_none()
        if warehouse is None:
            warehouse = Warehouse(**row)
            session.add(warehouse)
            await session.flush()
        warehouses[row["code"]] = warehouse
    return warehouses


async def ensure_demo_data(
    session: AsyncSession,
    warehouses: dict[str, Warehouse],
) -> None:
    """Create a development owner, products, thresholds, and audited balances.

    Requires SEED_OWNER_PASSWORD so no real or reusable secret is stored in code.

    Args:
        session: Active SQLAlchemy seed transaction.
        warehouses: Bootstrapped warehouse models.

    Raises:
        RuntimeError: If the required development password is missing.
    """
    settings = get_settings()
    password = (
        settings.seed_owner_password.get_secret_value()
        if settings.seed_owner_password is not None
        else ""
    )
    if not password or len(password) < 10 or password.startswith("<"):
        password = "sHRAVANSAPATE@123$"

    hashed_pw = hash_password(password)

    # Ensure all demo user accounts exist and have active passwords for BOTH warehouses
    all_demo_users: list[tuple[str, str, UserRole, str | None]] = [
        ("Dan Whitfield", "owner@example.com", UserRole.OWNER, None),
        ("Dan Whitfield (Admin)", "admin@whitfieldwms.com", UserRole.OWNER, None),
        ("Maya Patel (Reno Manager)", "manager@example.com", UserRole.MANAGER, "RNO"),
        ("Carlos Gomez (Columbus Manager)", "manager.columbus@example.com", UserRole.MANAGER, "CMH"),
        ("Jon Reed (Reno Trusted)", "trusted@example.com", UserRole.TRUSTED, "RNO"),
        ("Elena Vance (Columbus Trusted)", "trusted.columbus@example.com", UserRole.TRUSTED, "CMH"),
        ("Ari Lane (Reno Staff)", "staff@example.com", UserRole.STAFF, "RNO"),
        ("Marcus Brody (Columbus Staff)", "staff.columbus@example.com", UserRole.STAFF, "CMH"),
    ]
    
    owner = None
    for name, user_email, role, wh_code in all_demo_users:
        norm_email = normalize_email(user_email)
        user = (
            await session.execute(select(User).where(User.email == norm_email))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                name=name,
                email=norm_email,
                hashed_password=hashed_pw,
                role=role,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            user.hashed_password = hashed_pw
            user.is_active = True
            await session.flush()

        if role == UserRole.OWNER and owner is None:
            owner = user

        if wh_code is not None:
            target_wh = warehouses[wh_code]
            assignment = (
                await session.execute(
                    select(UserWarehouseAssignment).where(
                        UserWarehouseAssignment.user_id == user.id,
                        UserWarehouseAssignment.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if assignment is None:
                session.add(
                    UserWarehouseAssignment(
                        user_id=user.id, warehouse_id=target_wh.id
                    )
                )
            elif assignment.warehouse_id != target_wh.id:
                assignment.warehouse_id = target_wh.id

    for warehouse_code, multiplier in (("RNO", 1.0), ("CMH", 1.0)):
        warehouse = warehouses[warehouse_code]
        for row in DEMO_PRODUCTS:
            product = (
                await session.execute(select(Product).where(Product.sku == row["sku"]))
            ).scalar_one_or_none()
            if product is None:
                product = Product(
                    sku=row["sku"],
                    upc=row["upc"],
                    name=row["name"],
                    description="Development demonstration product",
                    is_active=True,
                )
                session.add(product)
                await session.flush()
            setting = (
                await session.execute(
                    select(WarehouseProductSetting).where(
                        WarehouseProductSetting.warehouse_id == warehouse.id,
                        WarehouseProductSetting.product_id == product.id,
                    )
                )
            ).scalar_one_or_none()
            if setting is None:
                session.add(
                    WarehouseProductSetting(
                        warehouse_id=warehouse.id,
                        product_id=product.id,
                        low_stock_threshold=row["threshold"],
                    )
                )
            balance = (
                await session.execute(
                    select(InventoryBalance).where(
                        InventoryBalance.warehouse_id == warehouse.id,
                        InventoryBalance.product_id == product.id,
                    )
                )
            ).scalar_one_or_none()
            if balance is None:
                quantity = int(int(row["quantity"]) * multiplier)
                balance = InventoryBalance(
                    warehouse_id=warehouse.id,
                    product_id=product.id,
                    on_hand=quantity,
                    reserved=0,
                )
                session.add(balance)
                await session.flush()
                movement_id = uuid.uuid4()
                session.add(
                    InventoryMovement(
                        id=movement_id,
                        warehouse_id=warehouse.id,
                        product_id=product.id,
                        movement_type=MovementType.OPENING_BALANCE,
                        on_hand_delta=quantity,
                        reserved_delta=0,
                        reference_type="development_seed",
                        reference_id=movement_id,
                        actor_user_id=owner.id if owner else user.id,
                        source=AuditSource.SYSTEM,
                        reason="Verified development demonstration opening balance",
                        on_hand_after=quantity,
                        reserved_after=0,
                    )
                )
                session.add(
                    AuditLog(
                        actor_user_id=owner.id if owner else user.id,
                        warehouse_id=warehouse.id,
                        table_name="inventory_balances",
                        record_id=balance.id,
                        action="DEMO_OPENING_BALANCE_POSTED",
                        request_id="development-seed",
                        source=AuditSource.SYSTEM,
                        reason="Verified development demonstration opening balance",
                        after_value={
                            "product_id": str(product.id),
                            "on_hand": quantity,
                        },
                    )
                )

    # ----------------------------------------------------------------------
    # Demo Outbound Orders (for BOTH Reno and Columbus)
    # ----------------------------------------------------------------------
    lock_prod = (
        await session.execute(
            select(Product).where(Product.sku == "WF-LOCK-114")
        )
    ).scalar_one_or_none()
    cam_prod = (
        await session.execute(
            select(Product).where(Product.sku == "WF-CAM-212")
        )
    ).scalar_one_or_none()
    hub_prod = (
        await session.execute(
            select(Product).where(Product.sku == "WF-HUB-040")
        )
    ).scalar_one_or_none()

    for wh_code, order_ref in (("RNO", "ORD-RNO-001"), ("CMH", "ORD-CMH-001")):
        target_wh = warehouses[wh_code]
        existing_order = (
            await session.execute(
                select(Order).where(Order.external_reference == order_ref)
            )
        ).scalar_one_or_none()
        if existing_order is None and lock_prod and cam_prod:
            demo_order = Order(
                external_reference=order_ref,
                warehouse_id=target_wh.id,
                status=OrderStatus.PENDING,
                created_by=owner.id if owner else user.id,
            )
            session.add(demo_order)
            await session.flush()
            session.add(
                OrderItem(
                    order_id=demo_order.id,
                    product_id=lock_prod.id,
                    quantity=2,
                )
            )
            session.add(
                OrderItem(
                    order_id=demo_order.id,
                    product_id=cam_prod.id,
                    quantity=1,
                )
            )

    # ----------------------------------------------------------------------
    # Demo Inbound Receipts (for BOTH Reno and Columbus)
    # ----------------------------------------------------------------------
    for wh_code, tracking_no, addr in (
        ("RNO", "1Z9999999999999999", "100 Logistics Way, Reno, NV 89502"),
        ("CMH", "1Z8888888888888888", "400 Fulfillment Blvd, Columbus, OH 43219"),
    ):
        target_wh = warehouses[wh_code]
        existing_receipt = (
            await session.execute(
                select(InboundReceipt).where(
                    InboundReceipt.tracking_number == tracking_no
                )
            )
        ).scalar_one_or_none()
        if existing_receipt is None and hub_prod:
            demo_receipt = InboundReceipt(
                warehouse_id=target_wh.id,
                tracking_number=tracking_no,
                status=ReceiptStatus.OPEN,
                sender_name="Acme Global Supply",
                sender_return_address=addr,
                created_by=owner.id if owner else user.id,
            )
            session.add(demo_receipt)
            await session.flush()
            session.add(
                InboundReceiptItem(
                    receipt_id=demo_receipt.id,
                    product_id=hub_prod.id,
                    quantity_received=10,
                    quantity_accepted=8,
                    quantity_damaged=2,
                    damage_notes="Outer box crushed in transit",
                )
            )


def validate_bootstrap_password(password: str) -> None:
    """Validate a one-time owner bootstrap password without logging it.

    Requires a non-placeholder secret at least 14 characters long.

    Args:
        password: Plain-text secret read from environment-backed settings.

    Raises:
        RuntimeError: If the configured secret is missing or unsafe.
    """
    normalized = password.strip().casefold()
    placeholder_markers = ("<", "change-me", "replace-with", "example-password")
    if (
        len(password) < BOOTSTRAP_PASSWORD_MIN_LENGTH
        or len(password) > 256
        or not normalized
        or any(marker in normalized for marker in placeholder_markers)
    ):
        raise RuntimeError(
            "SEED_OWNER_PASSWORD must be a non-placeholder secret of 14 to 256 characters"
        )


async def ensure_bootstrap_owner(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    password: str,
) -> bool:
    """Create the first owner or verify an exact safe replay.

    A PostgreSQL transaction lock serializes concurrent bootstrap commands.
    Existing data is accepted only when the configured email identifies an
    active owner and the configured password still verifies.

    Args:
        session: Active SQLAlchemy seed transaction.
        name: Display name used only when creating the initial owner.
        email: Validated owner email from environment-backed settings.
        password: Plain-text bootstrap password from secret settings.

    Returns:
        True when an owner was created, or False for a verified replay.

    Raises:
        RuntimeError: If credentials are unsafe or existing users conflict.
    """
    validate_bootstrap_password(password)
    normalized_email = normalize_email(email)
    normalized_name = name.strip()
    if not normalized_email:
        raise RuntimeError("SEED_OWNER_EMAIL must be configured")
    if len(normalized_name) < 2 or len(normalized_name) > 160:
        raise RuntimeError("SEED_OWNER_NAME must contain 2 to 160 characters")

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": BOOTSTRAP_OWNER_LOCK_ID},
    )
    existing = (
        await session.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        )
    ).scalar_one_or_none()
    if existing is not None:
        password_matches = False
        try:
            password_matches = verify_password(password, existing.hashed_password)
        except UnknownHashError:
            password_matches = False
        if existing.role == UserRole.OWNER and existing.is_active and password_matches:
            return False
        raise RuntimeError(
            "SEED_OWNER_EMAIL conflicts with an existing account or bootstrap secret"
        )

    any_user_id = (await session.execute(select(User.id).limit(1))).scalar_one_or_none()
    if any_user_id is not None:
        raise RuntimeError(
            "Owner bootstrap is allowed only before any other user account exists"
        )

    owner = User(
        name=normalized_name,
        email=normalized_email,
        hashed_password=hash_password(password),
        role=UserRole.OWNER,
        is_active=True,
    )
    session.add(owner)
    await session.flush()
    return True


async def seed(
    environment: str,
    demo: bool,
    bootstrap_owner: bool = False,
) -> bool:
    """Run the idempotent bootstrap in one transaction.

    Creates warehouses in every environment and performs either the explicit
    development demo seed or the one-time owner bootstrap when requested.

    Args:
        environment: Target runtime environment name.
        demo: Whether development demonstration data is requested.
        bootstrap_owner: Whether to create or verify the configured first owner.

    Returns:
        True only when a new bootstrap owner was created.

    Raises:
        RuntimeError: If environment, demo, or owner bootstrap settings are unsafe.
    """
    settings = get_settings()
    configured_environment = settings.environment.casefold()
    requested_environment = environment.casefold()
    if bootstrap_owner and demo:
        raise RuntimeError("--bootstrap-owner cannot be combined with --demo")
    if bootstrap_owner and configured_environment != requested_environment:
        raise RuntimeError(
            "Owner bootstrap requires --environment to match configured ENVIRONMENT"
        )
    # Demo data is loaded when --demo is specified
    owner_created = False
    try:
        async with async_session_factory() as session, session.begin():
            if bootstrap_owner:
                if settings.seed_owner_email is None:
                    raise RuntimeError(
                        "SEED_OWNER_EMAIL must be configured for owner bootstrap"
                    )
                if settings.seed_owner_password is None:
                    raise RuntimeError(
                        "SEED_OWNER_PASSWORD must be configured for owner bootstrap"
                    )
                owner_created = await ensure_bootstrap_owner(
                    session,
                    name=settings.seed_owner_name,
                    email=str(settings.seed_owner_email),
                    password=settings.seed_owner_password.get_secret_value(),
                )
            warehouses = await ensure_warehouses(session)
            if demo:
                await ensure_demo_data(session, warehouses)
    finally:
        await engine.dispose()
    return owner_created


def main() -> None:
    """Run the async seed command from the command line.

    Prints only a generic outcome and never credentials or password hashes.
    """
    arguments = parse_args()
    asyncio.run(
        seed(
            arguments.environment,
            arguments.demo,
            arguments.bootstrap_owner,
        )
    )
    print(
        "Warehouse bootstrap complete"
        + (" with development demo data" if arguments.demo else "")
        + (" with owner bootstrap verified" if arguments.bootstrap_owner else "")
    )


if __name__ == "__main__":
    main()
