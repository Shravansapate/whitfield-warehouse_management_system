"""Concurrency-safe outbound allocation and fulfillment workflows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.cruds.inventory_crud import InventoryCRUD
from backend.core.cruds.order_crud import OrderCRUD
from backend.core.cruds.reliability_crud import ReliabilityCRUD
from backend.core.models.enums import (
    AuditSource,
    MovementType,
    OrderStatus,
    PackageStatus,
    ReservationStatus,
    UserRole,
)
from backend.core.models.inventory import InventoryBalance
from backend.core.models.order import (
    InventoryReservation,
    Order,
    OrderItem,
    OutboundPackage,
)
from backend.core.models.product import Product
from backend.core.services.carriers.provider_factory import get_carrier_provider
from backend.core.services.transaction import command_transaction

logging = logger(__name__)


class AllocationFailed(Exception):
    """Signal one insufficient line so the allocation savepoint rolls back."""

    def __init__(self, product_id: uuid.UUID, quantity: int) -> None:
        """Capture the first failed allocation line.

        Args:
            product_id: Product lacking availability.
            quantity: Requested quantity.
        """
        super().__init__(str(product_id))
        self.product_id = product_id
        self.quantity = quantity


class OrderService:
    """Coordinate all-or-nothing allocation and legal fulfillment transitions."""

    def __init__(self) -> None:
        """Initialize order persistence and carrier collaborators."""
        self.orders = OrderCRUD()
        self.inventory = InventoryCRUD()
        self.reliability = ReliabilityCRUD()
        self.carrier = get_carrier_provider()

    @staticmethod
    def _assert_scope(user: CurrentUser, warehouse_id: uuid.UUID) -> None:
        """Enforce the actor's warehouse boundary for an existing order.

        Args:
            user: Authenticated actor.
            warehouse_id: Order warehouse identifier.

        Raises:
            HTTPException 403: If a non-owner accesses another warehouse.
        """
        if user.role != UserRole.OWNER and user.warehouse_id != warehouse_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "detail": "Cross-warehouse access is not allowed",
                    "code": "WAREHOUSE_FORBIDDEN",
                },
            )

    async def _locked_order(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        user: CurrentUser,
    ) -> Order:
        """Lock an order and enforce warehouse scope.

        Args:
            session: Active command transaction.
            order_id: Order identifier.
            user: Authenticated actor.

        Returns:
            Locked authorized order.

        Raises:
            HTTPException 404: If the order does not exist.
            HTTPException 403: If it belongs to another warehouse.
        """
        order = await self.orders.get_order(session, order_id, for_update=True)
        if order is None:
            raise HTTPException(
                status_code=404,
                detail={"detail": "Order not found", "code": "ORDER_NOT_FOUND"},
            )
        self._assert_scope(user, order.warehouse_id)
        return order

    async def create_order(
        self,
        session: AsyncSession,
        *,
        external_reference: str,
        warehouse_id: uuid.UUID | None,
        items: list[dict],
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Create and allocate a multi-line order within one warehouse.

        A failed line rolls every reservation back and leaves a traceable
        cannot-fulfill order. The other warehouse is never checked.

        Args:
            session: Request-scoped database session.
            external_reference: Unique upstream order reference.
            warehouse_id: Selected warehouse scope.
            items: Validated requested product lines.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Allocated or cannot-fulfill order response.
        """
        logging.info("Executing OrderService.create_order")
        resolved_warehouse = resolve_warehouse_id(user, warehouse_id)
        consolidated: dict[uuid.UUID, int] = {}
        for item in items:
            product_id = item["product_id"]
            consolidated[product_id] = consolidated.get(product_id, 0) + int(
                item["quantity"]
            )
        canonical_items = [
            {"product_id": str(product_id), "quantity": consolidated[product_id]}
            for product_id in sorted(consolidated, key=str)
        ]
        async with command_transaction(session):
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation="orders:create",
                key=idempotency_key,
                payload={
                    "external_reference": external_reference.strip(),
                    "warehouse_id": str(resolved_warehouse),
                    "items": canonical_items,
                },
            )
            if not is_new:
                return dict(record.response_body or {})
            normalized_reference = external_reference.strip()
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtextextended(:reference_key, 0))"
                ),
                {"reference_key": f"order-reference:{normalized_reference}"},
            )
            duplicate = (
                await session.execute(
                    select(Order.id).where(
                        Order.external_reference == normalized_reference
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "detail": "Order external reference already exists",
                        "code": "ORDER_REFERENCE_EXISTS",
                    },
                )
            products = {
                product.id: product
                for product in (
                    await session.scalars(
                        select(Product).where(Product.id.in_(list(consolidated)))
                    )
                ).all()
            }
            missing = [
                str(product_id)
                for product_id in consolidated
                if product_id not in products
            ]
            if missing:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "One or more products were not found",
                        "code": "PRODUCT_NOT_FOUND",
                        "product_ids": missing,
                    },
                )
            inactive = [
                str(product_id)
                for product_id, product in products.items()
                if not product.is_active
            ]
            if inactive:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "One or more products are inactive",
                        "code": "PRODUCT_INACTIVE",
                        "product_ids": inactive,
                    },
                )
            order = Order(
                external_reference=normalized_reference,
                warehouse_id=resolved_warehouse,
                status=OrderStatus.PENDING,
                created_by=user.id,
            )
            session.add(order)
            await session.flush()
            order_items: dict[uuid.UUID, OrderItem] = {}
            for product_id in sorted(consolidated, key=str):
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=product_id,
                    quantity=consolidated[product_id],
                )
                session.add(order_item)
                order_items[product_id] = order_item
            await session.flush()

            allocation_failure: AllocationFailed | None = None
            try:
                async with session.begin_nested():
                    for product_id in sorted(consolidated, key=str):
                        quantity = consolidated[product_id]
                        balance = await self.inventory.reserve(
                            session,
                            warehouse_id=resolved_warehouse,
                            product_id=product_id,
                            quantity=quantity,
                        )
                        if balance is None:
                            raise AllocationFailed(product_id, quantity)
                        on_hand, reserved = balance
                        reservation = InventoryReservation(
                            order_id=order.id,
                            order_item_id=order_items[product_id].id,
                            warehouse_id=resolved_warehouse,
                            product_id=product_id,
                            quantity=quantity,
                            status=ReservationStatus.ACTIVE,
                        )
                        session.add(reservation)
                        await session.flush()
                        await self.inventory.add_movement(
                            session,
                            warehouse_id=resolved_warehouse,
                            product_id=product_id,
                            movement_type=MovementType.RESERVE,
                            on_hand_delta=0,
                            reserved_delta=quantity,
                            reference_type="orders",
                            reference_id=order.id,
                            actor_user_id=user.id,
                            source=source,
                            on_hand_after=on_hand,
                            reserved_after=reserved,
                        )
            except AllocationFailed as error:
                allocation_failure = error

            shortages: list[dict] = []
            if allocation_failure is None:
                order.status = OrderStatus.ALLOCATED
                action = "ORDER_ALLOCATED"
            else:
                order.status = OrderStatus.CANNOT_FULFILL
                for product_id in sorted(consolidated, key=str):
                    row = (
                        await session.execute(
                            select(
                                InventoryBalance.on_hand, InventoryBalance.reserved
                            ).where(
                                InventoryBalance.warehouse_id == resolved_warehouse,
                                InventoryBalance.product_id == product_id,
                            )
                        )
                    ).one_or_none()
                    available = (int(row.on_hand) - int(row.reserved)) if row else 0
                    if available < consolidated[product_id]:
                        shortages.append(
                            {
                                "product_id": str(product_id),
                                "sku": products[product_id].sku,
                                "requested": consolidated[product_id],
                                "available": available,
                            }
                        )
                action = "ORDER_ALLOCATION_FAILED"
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=resolved_warehouse,
                table_name="orders",
                record_id=order.id,
                action=action,
                request_id=request_id,
                source=source,
                after_value={"status": order.status.value, "shortages": shortages},
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order, shortages=shortages)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=201,
                response_body=response,
                resource_type="orders",
                resource_id=order.id,
            )
        return response

    async def start_picking(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Transition an allocated order into physical picking.

        Args:
            session: Request-scoped database session.
            order_id: Allocated order identifier.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Updated order response or stored replay.
        """
        logging.info("Executing OrderService.start_picking")
        return await self._simple_transition(
            session,
            order_id=order_id,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id,
            source=source,
            expected=OrderStatus.ALLOCATED,
            target=OrderStatus.PICKING,
            action="ORDER_PICKING_STARTED",
        )

    async def _simple_transition(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
        expected: OrderStatus,
        target: OrderStatus,
        action: str,
    ) -> dict:
        """Apply one idempotent single-state order transition.

        Args:
            session: Request-scoped database session.
            order_id: Order identifier.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.
            expected: Required current state.
            target: New state.
            action: Audit action name.

        Returns:
            Updated order response or stored replay.
        """
        logging.info("Executing OrderService._simple_transition")
        async with command_transaction(session):
            order = await self._locked_order(session, order_id=order_id, user=user)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"order:{order_id}:{target.value}",
                key=idempotency_key,
                payload={"order_id": str(order_id), "target": target.value},
            )
            if not is_new:
                return dict(record.response_body or {})
            if order.status != expected:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": f"Order must be {expected.value} before becoming {target.value}",
                        "code": "ORDER_STATE_CONFLICT",
                    },
                )
            order.status = target
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=order.warehouse_id,
                table_name="orders",
                record_id=order.id,
                action=action,
                request_id=request_id,
                source=source,
                before_value={"status": expected.value},
                after_value={"status": target.value},
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="orders",
                resource_id=order.id,
            )
        return response

    async def pack(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        measurements: dict,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Record required package measurements and mark an order packed.

        Args:
            session: Request-scoped database session.
            order_id: Picking order identifier.
            measurements: Validated positive package measurements.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Packed order response or stored replay.
        """
        logging.info("Executing OrderService.pack")
        async with command_transaction(session):
            order = await self._locked_order(session, order_id=order_id, user=user)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"order:{order_id}:pack",
                key=idempotency_key,
                payload=measurements,
            )
            if not is_new:
                return dict(record.response_body or {})
            if order.status != OrderStatus.PICKING:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Order must be picking before it can be packed",
                        "code": "ORDER_STATE_CONFLICT",
                    },
                )
            order_items = list(
                (
                    await session.scalars(
                        select(OrderItem)
                        .where(OrderItem.order_id == order.id)
                        .order_by(OrderItem.product_id)
                        .with_for_update()
                    )
                ).all()
            )
            incomplete = [
                str(item.id)
                for item in order_items
                if item.picked_quantity != item.quantity
            ]
            if incomplete:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Every order line must be fully picked before packing",
                        "code": "PICKING_INCOMPLETE",
                        "order_item_ids": incomplete,
                    },
                )
            package = OutboundPackage(
                order_id=order.id, status=PackageStatus.PACKED, **measurements
            )
            session.add(package)
            order.status = OrderStatus.PACKED
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=order.warehouse_id,
                table_name="orders",
                record_id=order.id,
                action="ORDER_PACKED",
                request_id=request_id,
                source=source,
                before_value={"status": OrderStatus.PICKING.value},
                after_value={
                    "status": OrderStatus.PACKED.value,
                    "package_id": str(package.id),
                },
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="orders",
                resource_id=order.id,
            )
        return response

    async def confirm_picked_item(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        picked_quantity: int,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Persist a physical picked count for one picking order line.

        Args:
            session: Request-scoped database session.
            order_id: Picking order identifier.
            item_id: Order line being counted.
            picked_quantity: Confirmed physical quantity, up to the ordered amount.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Updated order checklist or stored replay.
        """
        logging.info("Executing OrderService.confirm_picked_item")
        async with command_transaction(session):
            order = await self._locked_order(session, order_id=order_id, user=user)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"order:{order_id}:item:{item_id}:pick",
                key=idempotency_key,
                payload={
                    "order_id": str(order_id),
                    "item_id": str(item_id),
                    "picked_quantity": picked_quantity,
                },
            )
            if not is_new:
                return dict(record.response_body or {})
            if order.status != OrderStatus.PICKING:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Order must be picking before item counts can change",
                        "code": "ORDER_STATE_CONFLICT",
                    },
                )
            item = (
                await session.execute(
                    select(OrderItem)
                    .where(OrderItem.id == item_id, OrderItem.order_id == order.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "Order item not found",
                        "code": "ORDER_ITEM_NOT_FOUND",
                    },
                )
            if picked_quantity > item.quantity:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Picked quantity cannot exceed ordered quantity",
                        "code": "PICK_QUANTITY_EXCEEDS_ORDER",
                    },
                )
            before = item.picked_quantity
            item.picked_quantity = picked_quantity
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=order.warehouse_id,
                table_name="order_items",
                record_id=item.id,
                action="ORDER_ITEM_PICK_CONFIRMED",
                request_id=request_id,
                source=source,
                before_value={"picked_quantity": before},
                after_value={
                    "picked_quantity": item.picked_quantity,
                    "ordered_quantity": item.quantity,
                },
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="order_items",
                resource_id=item.id,
            )
        return response

    async def create_label(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        carrier: str,
        service_level: str,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Create a carrier label without marking the order shipped.

        Args:
            session: Request-scoped database session.
            order_id: Packed order identifier.
            carrier: Requested carrier adapter name.
            service_level: Requested service tier.
            idempotency_key: Required command and provider retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Label-created order response or stored replay.
        """
        logging.info("Executing OrderService.create_label")
        async with command_transaction(session):
            order = await self._locked_order(session, order_id=order_id, user=user)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"order:{order_id}:create_label",
                key=idempotency_key,
                payload={"carrier": carrier, "service_level": service_level},
            )
            if not is_new:
                return dict(record.response_body or {})
            if order.status != OrderStatus.PACKED:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Order must be packed before label creation",
                        "code": "ORDER_STATE_CONFLICT",
                    },
                )
            package = await self.orders.get_package(session, order.id, for_update=True)
            if package is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Package measurements are required",
                        "code": "PACKAGE_REQUIRED",
                    },
                )
            label = await self.carrier.create_label(
                idempotency_key=f"order-{order.id}-{idempotency_key}",
                carrier=carrier,
                service_level=service_level,
                weight=Decimal(package.weight),
                weight_unit=package.weight_unit,
                length=Decimal(package.length),
                width=Decimal(package.width),
                height=Decimal(package.height),
                dimension_unit=package.dimension_unit,
            )
            package.carrier = label.carrier
            package.service_level = label.service_level
            package.provider_request_id = label.provider_request_id
            package.tracking_number = label.tracking_number
            package.label_url_or_key = label.label_url_or_key
            package.status = PackageStatus.LABEL_CREATED
            order.status = OrderStatus.LABEL_CREATED
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=order.warehouse_id,
                table_name="orders",
                record_id=order.id,
                action="SHIPPING_LABEL_CREATED",
                request_id=request_id,
                source=source,
                before_value={"status": OrderStatus.PACKED.value},
                after_value={
                    "status": OrderStatus.LABEL_CREATED.value,
                    "carrier": label.carrier,
                    "tracking_number": label.tracking_number,
                },
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="orders",
                resource_id=order.id,
            )
        return response

    async def ship(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Consume every reservation and atomically mark an order shipped.

        Args:
            session: Request-scoped database session.
            order_id: Label-created order identifier.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Shipped order response or stored replay.
        """
        logging.info("Executing OrderService.ship")
        async with command_transaction(session):
            order = await self._locked_order(session, order_id=order_id, user=user)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"order:{order_id}:ship",
                key=idempotency_key,
                payload={"order_id": str(order_id)},
            )
            if not is_new:
                return dict(record.response_body or {})
            if order.status != OrderStatus.LABEL_CREATED:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Order must have a label before shipping",
                        "code": "ORDER_STATE_CONFLICT",
                    },
                )
            package = await self.orders.get_package(session, order.id, for_update=True)
            if package is None or not package.tracking_number:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Tracking number is required before shipping",
                        "code": "TRACKING_REQUIRED",
                    },
                )
            reservations = await self.orders.list_reservations(
                session, order.id, for_update=True
            )
            active = [
                item for item in reservations if item.status == ReservationStatus.ACTIVE
            ]
            if not active:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Order has no active reservations",
                        "code": "RESERVATION_STATE_CONFLICT",
                    },
                )
            for reservation in active:
                balance = await self.inventory.consume(
                    session,
                    warehouse_id=reservation.warehouse_id,
                    product_id=reservation.product_id,
                    quantity=reservation.quantity,
                )
                if balance is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "Inventory reservation is inconsistent",
                            "code": "RESERVATION_STATE_CONFLICT",
                        },
                    )
                on_hand, reserved = balance
                reservation.status = ReservationStatus.CONSUMED
                await self.inventory.add_movement(
                    session,
                    warehouse_id=reservation.warehouse_id,
                    product_id=reservation.product_id,
                    movement_type=MovementType.SHIP,
                    on_hand_delta=-reservation.quantity,
                    reserved_delta=-reservation.quantity,
                    reference_type="orders",
                    reference_id=order.id,
                    actor_user_id=user.id,
                    source=source,
                    on_hand_after=on_hand,
                    reserved_after=reserved,
                )
            order.status = OrderStatus.SHIPPED
            package.status = PackageStatus.SHIPPED
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=order.warehouse_id,
                table_name="orders",
                record_id=order.id,
                action="ORDER_SHIPPED",
                request_id=request_id,
                source=source,
                before_value={"status": OrderStatus.LABEL_CREATED.value},
                after_value={
                    "status": OrderStatus.SHIPPED.value,
                    "tracking_number": package.tracking_number,
                },
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="orders",
                resource_id=order.id,
            )
        return response

    async def cancel(
        self,
        session: AsyncSession,
        *,
        order_id: uuid.UUID,
        reason: str,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Cancel a non-shipped order and release every active reservation.

        Args:
            session: Request-scoped database session.
            order_id: Order to retain as cancelled.
            reason: Mandatory cancellation explanation.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Cancelled order response or stored replay.
        """
        logging.info("Executing OrderService.cancel")
        async with command_transaction(session):
            order = await self._locked_order(session, order_id=order_id, user=user)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"order:{order_id}:cancel",
                key=idempotency_key,
                payload={"reason": reason},
            )
            if not is_new:
                return dict(record.response_body or {})
            if order.status in {OrderStatus.SHIPPED, OrderStatus.CANCELLED}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Order cannot be cancelled from its current state",
                        "code": "ORDER_STATE_CONFLICT",
                    },
                )
            previous_status = order.status
            reservations = await self.orders.list_reservations(
                session, order.id, for_update=True
            )
            for reservation in reservations:
                if reservation.status != ReservationStatus.ACTIVE:
                    continue
                balance = await self.inventory.release(
                    session,
                    warehouse_id=reservation.warehouse_id,
                    product_id=reservation.product_id,
                    quantity=reservation.quantity,
                )
                if balance is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "Inventory reservation is inconsistent",
                            "code": "RESERVATION_STATE_CONFLICT",
                        },
                    )
                on_hand, reserved = balance
                reservation.status = ReservationStatus.RELEASED
                await self.inventory.add_movement(
                    session,
                    warehouse_id=reservation.warehouse_id,
                    product_id=reservation.product_id,
                    movement_type=MovementType.RELEASE,
                    on_hand_delta=0,
                    reserved_delta=-reservation.quantity,
                    reference_type="orders",
                    reference_id=order.id,
                    actor_user_id=user.id,
                    source=source,
                    on_hand_after=on_hand,
                    reserved_after=reserved,
                    reason=reason,
                )
            order.status = OrderStatus.CANCELLED
            order.cancelled_by = user.id
            order.cancelled_at = datetime.now(UTC)
            order.cancel_reason = reason.strip()
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=order.warehouse_id,
                table_name="orders",
                record_id=order.id,
                action="ORDER_CANCELLED",
                request_id=request_id,
                source=source,
                before_value={"status": previous_status.value},
                after_value={"status": OrderStatus.CANCELLED.value},
                reason=reason,
            )
            response = jsonable_encoder(
                await self.orders.serialize_order(session, order)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="orders",
                resource_id=order.id,
            )
        return response
