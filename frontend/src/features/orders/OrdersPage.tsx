import { Box, Check, LoaderCircle, PackagePlus, Play, Ruler, Tag, Trash2, Truck, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCursorResource } from "../../hooks/useCursorResource";
import { ApiError, createIdempotencyKey, wmsApi } from "../../lib/api/client";
import type { InventoryRow, OrderRow, OrderStatus, WarehouseRef } from "../../types/wms";

interface OrdersPageProps {
  onChanged: () => void;
  refreshVersion: number;
  warehouse: WarehouseRef;
}

const toneByStatus: Record<OrderStatus, "blue" | "green" | "amber" | "red" | "gray"> = {
  pending: "gray",
  allocated: "blue",
  picking: "blue",
  packed: "amber",
  label_created: "green",
  shipped: "green",
  cannot_fulfill: "red",
  cancelled: "gray"
};

interface DraftLine {
  product: InventoryRow;
  quantity: number;
}

export function OrdersPage({ onChanged, refreshVersion, warehouse }: OrdersPageProps) {
  const orders = useCursorResource(
    (cursor) => wmsApi.getOrdersPage(warehouse.id, { cursor }),
    [warehouse.id, refreshVersion]
  );
  const inventory = useCursorResource(
    (cursor) => wmsApi.getInventoryPage(warehouse.id, { cursor }),
    [warehouse.id, refreshVersion]
  );
  const [selectedOrderId, setSelectedOrderId] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [externalReference, setExternalReference] = useState("");
  const [draftProductId, setDraftProductId] = useState("");
  const [draftQuantity, setDraftQuantity] = useState("1");
  const [draftLines, setDraftLines] = useState<DraftLine[]>([]);
  const [measurements, setMeasurements] = useState({ weight: "", length: "", width: "", height: "" });
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "error" } | null>(null);
  const commandRetries = useRef<Record<string, { signature: string; key: string }>>({});
  const warehouseOrders = useMemo(
    () => (orders.data ?? []).filter((order) => order.warehouseId === warehouse.id),
    [orders.data, warehouse.id]
  );

  const commandKey = (operation: string, payload: unknown) => {
    const signature = JSON.stringify(payload);
    if (commandRetries.current[operation]?.signature !== signature) commandRetries.current[operation] = { signature, key: createIdempotencyKey(operation) };
    return commandRetries.current[operation].key;
  };

  useEffect(() => {
    setMeasurements({ weight: "", length: "", width: "", height: "" });
  }, [selectedOrderId, warehouse.id]);

  useEffect(() => {
    if (orders.status !== "success") return;
    if (warehouseOrders.some((order) => order.id === selectedOrderId)) return;
    const next = warehouseOrders.find((order) => ["allocated", "picking", "packed", "label_created"].includes(order.status));
    setSelectedOrderId(next?.id ?? "");
  }, [orders.status, selectedOrderId, warehouseOrders]);

  const selectedOrder = useMemo(() => warehouseOrders.find((order) => order.id === selectedOrderId) ?? null, [selectedOrderId, warehouseOrders]);
  const allItemsPicked = Boolean(selectedOrder?.items.length) && selectedOrder!.items.every((item) => item.pickedQuantity === item.quantity);

  const refreshOrders = () => {
    orders.reload();
    inventory.reload();
    onChanged();
  };

  const addDraftLine = () => {
    const product = (inventory.data ?? []).find((item) => item.productId === draftProductId);
    const quantity = Number(draftQuantity);
    if (!product || !Number.isInteger(quantity) || quantity <= 0) {
      setNotice({ message: "Choose an inventory product and enter a positive whole-unit quantity.", tone: "error" });
      return;
    }
    setDraftLines((lines) => {
      const existing = lines.find((line) => line.product.productId === product.productId);
      if (existing) return lines.map((line) => line.product.productId === product.productId ? { ...line, quantity: line.quantity + quantity } : line);
      return [...lines, { product, quantity }];
    });
    setDraftProductId("");
    setDraftQuantity("1");
    setNotice(null);
  };

  const handleCreateOrder = async (event: FormEvent) => {
    event.preventDefault();
    if (!externalReference.trim() || draftLines.length === 0) {
      setNotice({ message: "Enter an external reference and add at least one product line.", tone: "error" });
      return;
    }
    setSubmitting("create");
    try {
      const input = {
        external_reference: externalReference.trim(),
        warehouse_id: warehouse.id,
        items: draftLines.map((line) => ({ product_id: line.product.productId, quantity: line.quantity }))
      };
      const created = await wmsApi.createOrder(input, commandKey("order-create", input));
      delete commandRetries.current["order-create"];
      setExternalReference("");
      setDraftLines([]);
      setShowCreate(false);
      setSelectedOrderId(created.id);
      setNotice({
        message: created.status === "cannot_fulfill"
          ? `${created.displayId} was retained as cannot fulfill; no product line was reserved and no other warehouse was used.`
          : `${created.displayId} created and allocated as one warehouse transaction.`,
        tone: created.status === "cannot_fulfill" ? "error" : "success"
      });
      refreshOrders();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The order could not be created.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const runCommand = async (command: "pick" | "pack" | "label" | "ship") => {
    if (!selectedOrder) return;
    setSubmitting(command);
    setNotice(null);
    try {
      const operation = `order-${command}-${selectedOrder.id}`;
      if (command === "pick") await wmsApi.startPicking(selectedOrder.id, commandKey(operation, { id: selectedOrder.id }));
      if (command === "pack") {
        if (!allItemsPicked) {
          setNotice({ message: "Confirm the full ordered quantity for every line before packing.", tone: "error" });
          return;
        }
        const values = Object.values(measurements).map(Number);
        if (values.some((value) => !Number.isFinite(value) || value <= 0)) {
          setNotice({ message: "Weight and every package dimension must be greater than zero.", tone: "error" });
          return;
        }
        const input = {
          weight: Number(measurements.weight), weight_unit: "lb", length: Number(measurements.length), width: Number(measurements.width), height: Number(measurements.height), dimension_unit: "in"
        } as const;
        await wmsApi.packOrder(selectedOrder.id, input, commandKey(operation, { id: selectedOrder.id, ...input }));
      }
      if (command === "label") await wmsApi.createLabel(selectedOrder.id, "fake", "ground", commandKey(operation, { id: selectedOrder.id, carrier: "fake", service: "ground" }));
      if (command === "ship") {
        if (!window.confirm(`Ship ${selectedOrder.displayId}? This consumes its reservation and reduces on-hand stock.`)) return;
        await wmsApi.shipOrder(selectedOrder.id, commandKey(operation, { id: selectedOrder.id }));
      }
      delete commandRetries.current[operation];
      setNotice({ message: `${selectedOrder.displayId} advanced successfully.`, tone: "success" });
      refreshOrders();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The fulfillment step could not be completed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const confirmPickedItem = async (itemId: string, quantity: number) => {
    if (!selectedOrder || selectedOrder.status !== "picking") return;
    const operation = `order-item-pick-${selectedOrder.id}-${itemId}`;
    setSubmitting(operation);
    setNotice(null);
    try {
      await wmsApi.confirmPickedItem(
        selectedOrder.id,
        itemId,
        quantity,
        commandKey(operation, { orderId: selectedOrder.id, itemId, pickedQuantity: quantity })
      );
      delete commandRetries.current[operation];
      setNotice({ message: "Picked quantity confirmed for this order line.", tone: "success" });
      refreshOrders();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The picked quantity could not be confirmed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleCancel = async (order: OrderRow) => {
    const reason = window.prompt(`Why is ${order.displayId} being cancelled?`)?.trim();
    if (!reason) return;
    if (!window.confirm("Cancel this order and release its active reservations?")) return;
    setSubmitting(`cancel-${order.id}`);
    try {
      const operation = `order-cancel-${order.id}`;
      await wmsApi.cancelOrder(order.id, reason, commandKey(operation, { id: order.id, reason }));
      delete commandRetries.current[operation];
      setNotice({ message: `${order.displayId} cancelled; active reservations were released.`, tone: "success" });
      refreshOrders();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The order could not be cancelled.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="pageStack">
      <div className="pageHeader">
        <div>
          <p className="eyebrow">Single warehouse fulfillment</p>
          <h2>Orders and packing</h2>
          <p>Multi-product orders reserve all lines together. No split fulfillment and no automatic fallback.</p>
        </div>
        <button className="primaryButton" onClick={() => setShowCreate((visible) => !visible)} type="button"><PackagePlus size={18} /> {showCreate ? "Close form" : "New order"}</button>
      </div>

      {notice && <InlineNotice message={notice.message} tone={notice.tone} />}

      {showCreate && (
        <form className="surface formStack" onSubmit={handleCreateOrder}>
          <div className="sectionHeader"><div><p className="eyebrow">Atomic allocation</p><h3>Create multi-product order</h3></div><StatusBadge label={`${draftLines.length} lines`} /></div>
          <label><span>External reference</span><input onChange={(event) => setExternalReference(event.target.value)} placeholder="WEB-74821" value={externalReference} /></label>
          <div className="inlineFields">
            <label><span>Product</span><select onChange={(event) => setDraftProductId(event.target.value)} value={draftProductId}><option value="">Choose available product</option>{(inventory.data ?? []).map((item) => <option key={item.productId} value={item.productId}>{item.sku} · {item.name} ({item.available} available)</option>)}</select></label>
            <label><span>Quantity</span><input min="1" onChange={(event) => setDraftQuantity(event.target.value)} type="number" value={draftQuantity} /></label>
            <button className="secondaryButton" onClick={addDraftLine} type="button"><Check size={17} /> Add line</button>
          </div>
          {inventory.loadMoreError && <InlineNotice message={inventory.loadMoreError.message} tone="error" />}
          {inventory.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={inventory.loadingMore} onClick={() => void inventory.loadMore()} type="button">{inventory.loadingMore && <LoaderCircle className="spin" size={17} />} Load more inventory products</button></div>}
          {draftLines.map((line) => (
            <div className="draftLine" key={line.product.productId}><span><strong>{line.product.sku}</strong> · {line.product.name}</span><span>{line.quantity} units</span><button aria-label={`Remove ${line.product.sku}`} onClick={() => setDraftLines((lines) => lines.filter((candidate) => candidate.product.productId !== line.product.productId))} type="button"><Trash2 size={16} /></button></div>
          ))}
          <button className="primaryButton" disabled={submitting === "create" || draftLines.length === 0} type="submit">{submitting === "create" && <LoaderCircle className="spin" size={18} />} Create and allocate</button>
        </form>
      )}

      <DataState dataLength={warehouseOrders.length} emptyMessage="No orders exist for this warehouse." error={orders.error} loading={orders.status === "loading"} onRetry={orders.reload}>
        <>
          <section className="kanbanGrid">
            {warehouseOrders.map((order) => (
            <article className={selectedOrderId === order.id ? "orderCard selectedOrder" : "orderCard"} key={order.id}>
              <button className="cardSelect" onClick={() => setSelectedOrderId(order.id)} type="button">
                <div className="sectionHeader"><div><strong>{order.displayId}</strong><small>{order.reference}</small></div><StatusBadge label={order.status} tone={toneByStatus[order.status] ?? "gray"} /></div>
                <div className="orderStats"><span><Box size={16} /> {order.itemCount} items</span><span><Tag size={16} /> {order.units} units</span></div>
                <div className="packTrack"><span className={order.status !== "allocated" ? "trackDone" : ""}>Pick</span><span className={["packed", "label_created", "shipped"].includes(order.status) ? "trackDone" : ""}>Measure</span><span className={["label_created", "shipped"].includes(order.status) ? "trackDone" : ""}>Label</span><span className={order.status === "shipped" ? "trackDone" : ""}>Ship</span></div>
                <div className="packageNote"><Ruler size={16} /><p>{order.packageState}</p></div>
              </button>
              {!(["shipped", "cancelled"].includes(order.status)) && <button className="textButton dangerText" disabled={Boolean(submitting)} onClick={() => void handleCancel(order)} type="button"><XCircle size={15} /> Cancel order</button>}
              {order.package?.labelUrl && <a className="textButton" href={order.package.labelUrl} rel="noreferrer" target="_blank">Open label</a>}
            </article>
            ))}
          </section>
          {orders.loadMoreError && <InlineNotice message={orders.loadMoreError.message} tone="error" />}
          {orders.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={orders.loadingMore} onClick={() => void orders.loadMore()} type="button">{orders.loadingMore && <LoaderCircle className="spin" size={17} />} Load more orders</button></div>}
        </>
      </DataState>

      {selectedOrder && ["allocated", "picking", "packed", "label_created"].includes(selectedOrder.status) && (
        <section className="surface measurePanel">
          <div><p className="eyebrow">Next fulfillment step</p><h3>{selectedOrder.displayId} · {selectedOrder.status.replace(/_/g, " ")}</h3></div>
          {selectedOrder.status === "picking" ? (
            <div className="pickingWorkflow">
              <div className="pickChecklist" aria-label="Picking checklist">
                {selectedOrder.items.map((item) => {
                  const complete = item.pickedQuantity === item.quantity;
                  return (
                    <div className={complete ? "pickLine pickLineComplete" : "pickLine"} key={item.id}>
                      <div><strong>{item.sku}</strong><span>{item.name}</span></div>
                      <span><strong>{item.pickedQuantity}</strong> picked / {item.quantity} ordered</span>
                      <button className="secondaryButton compactButton" disabled={Boolean(submitting) || complete} onClick={() => void confirmPickedItem(item.id, item.quantity)} type="button">
                        <Check size={16} /> {complete ? "Picked" : `Confirm ${item.quantity} picked`}
                      </button>
                    </div>
                  );
                })}
              </div>
              <div className="measureFields">
                <label><span>Weight lb</span><input min="0.01" onChange={(event) => setMeasurements({ ...measurements, weight: event.target.value })} step="0.01" type="number" value={measurements.weight} /></label>
                <label><span>Length in</span><input min="0.01" onChange={(event) => setMeasurements({ ...measurements, length: event.target.value })} step="0.01" type="number" value={measurements.length} /></label>
                <label><span>Width in</span><input min="0.01" onChange={(event) => setMeasurements({ ...measurements, width: event.target.value })} step="0.01" type="number" value={measurements.width} /></label>
                <label><span>Height in</span><input min="0.01" onChange={(event) => setMeasurements({ ...measurements, height: event.target.value })} step="0.01" type="number" value={measurements.height} /></label>
              </div>
            </div>
          ) : <p className="workflowHint">Only the legal next state is enabled. The API also enforces every transition.</p>}
          {selectedOrder.status === "allocated" && <button className="primaryButton" disabled={Boolean(submitting)} onClick={() => void runCommand("pick")} type="button"><Play size={18} /> Start picking</button>}
          {selectedOrder.status === "picking" && <button className="primaryButton" disabled={Boolean(submitting) || !allItemsPicked} onClick={() => void runCommand("pack")} type="button"><Box size={18} /> Confirm packed</button>}
          {selectedOrder.status === "packed" && <button className="primaryButton" disabled={Boolean(submitting)} onClick={() => void runCommand("label")} type="button"><Tag size={18} /> Create label</button>}
          {selectedOrder.status === "label_created" && <button className="primaryButton" disabled={Boolean(submitting)} onClick={() => void runCommand("ship")} type="button"><Truck size={18} /> Confirm shipment</button>}
        </section>
      )}
    </div>
  );
}
