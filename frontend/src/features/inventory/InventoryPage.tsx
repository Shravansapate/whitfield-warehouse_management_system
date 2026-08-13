import { LoaderCircle, Search, SlidersHorizontal } from "lucide-react";
import { useMemo, useRef, useState, type FormEvent } from "react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCursorResource } from "../../hooks/useCursorResource";
import { ApiError, createIdempotencyKey, wmsApi } from "../../lib/api/client";
import type { Role, WarehouseRef } from "../../types/wms";

interface InventoryPageProps {
  onChanged: () => void;
  refreshVersion: number;
  role: Role;
  warehouse: WarehouseRef;
}

export function InventoryPage({ onChanged, refreshVersion, role, warehouse }: InventoryPageProps) {
  const resource = useCursorResource(
    (cursor) => wmsApi.getInventoryPage(warehouse.id, { cursor }),
    [warehouse.id, refreshVersion]
  );
  const [movementProductId, setMovementProductId] = useState("");
  const movements = useCursorResource(
    (cursor) => wmsApi.getInventoryMovementsPage(warehouse.id, movementProductId, { cursor }),
    [warehouse.id, movementProductId, refreshVersion],
    Boolean(movementProductId)
  );
  const [query, setQuery] = useState("");
  const [showAdjustment, setShowAdjustment] = useState(false);
  const [showOpening, setShowOpening] = useState(false);
  const [productId, setProductId] = useState("");
  const [quantityDelta, setQuantityDelta] = useState("");
  const [reason, setReason] = useState("");
  const [openingProductId, setOpeningProductId] = useState("");
  const [openingQuantity, setOpeningQuantity] = useState("");
  const [openingReason, setOpeningReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "error" } | null>(null);
  const retry = useRef<{ signature: string; key: string } | null>(null);
  const openingRetry = useRef<{ signature: string; key: string } | null>(null);
  const canAdjust = role === "owner" || role === "manager" || role === "trusted";
  const canSetThreshold = role === "owner" || role === "manager";

  const filteredInventory = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return resource.data ?? [];
    return (resource.data ?? []).filter((item) => [item.sku, item.upc, item.name].some((value) => value.toLowerCase().includes(normalized)));
  }, [query, resource.data]);

  const handleAdjustment = async (event: FormEvent) => {
    event.preventDefault();
    const delta = Number(quantityDelta);
    if (!productId || !Number.isInteger(delta) || delta === 0 || reason.trim().length < 3) {
      setNotice({ message: "Choose a product, enter a non-zero whole-unit change, and provide a clear reason.", tone: "error" });
      return;
    }
    if (!window.confirm(`Adjust inventory by ${delta > 0 ? "+" : ""}${delta} units? This will be audited.`)) return;
    setSubmitting(true);
    setNotice(null);
    try {
      const input = { warehouse_id: warehouse.id, product_id: productId, quantity_delta: delta, reason: reason.trim() };
      const signature = JSON.stringify(input);
      if (retry.current?.signature !== signature) retry.current = { signature, key: createIdempotencyKey("inventory-adjustment") };
      await wmsApi.adjustInventory(input, retry.current.key);
      retry.current = null;
      setNotice({ message: "Inventory adjustment posted with an audit record.", tone: "success" });
      setQuantityDelta("");
      setReason("");
      resource.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The adjustment could not be posted.", tone: "error" });
    } finally {
      setSubmitting(false);
    }
  };

  const handleThreshold = async (item: { productId: string; sku: string; threshold: number }) => {
    const candidate = window.prompt(`Set the ${warehouse.name} low-stock threshold for ${item.sku}.`, String(item.threshold));
    if (candidate === null) return;
    const threshold = Number(candidate);
    if (!Number.isInteger(threshold) || threshold < 0 || threshold > 1_000_000) {
      setNotice({ message: "Low-stock threshold must be a whole number from 0 to 1,000,000.", tone: "error" });
      return;
    }
    setSubmitting(true);
    setNotice(null);
    try {
      await wmsApi.setProductThreshold(warehouse.id, item.productId, threshold);
      setNotice({ message: `${warehouse.name} threshold for ${item.sku} is now ${threshold}.`, tone: "success" });
      resource.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The threshold could not be saved.", tone: "error" });
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpeningBalance = async (event: FormEvent) => {
    event.preventDefault();
    const quantity = Number(openingQuantity);
    if (!openingProductId || !Number.isInteger(quantity) || quantity <= 0 || openingReason.trim().length < 3) {
      setNotice({ message: "Choose a product, enter a positive whole-unit opening quantity, and provide a verified reason.", tone: "error" });
      return;
    }
    if (!window.confirm(`Post an opening balance of ${quantity} units? This one-time command cannot increment an existing balance.`)) return;
    const input = { warehouse_id: warehouse.id, product_id: openingProductId, quantity, reason: openingReason.trim() };
    const signature = JSON.stringify(input);
    if (openingRetry.current?.signature !== signature) openingRetry.current = { signature, key: createIdempotencyKey("opening-balance") };
    setSubmitting(true);
    setNotice(null);
    try {
      await wmsApi.postOpeningBalance(input, openingRetry.current.key);
      openingRetry.current = null;
      setOpeningQuantity("");
      setOpeningReason("");
      setNotice({ message: "Verified opening stock posted with movement and audit records.", tone: "success" });
      resource.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The opening balance could not be posted.", tone: "error" });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="pageStack">
      <div className="pageHeader">
        <div>
          <p className="eyebrow">Warehouse-owned stock</p>
          <h2>Inventory balances</h2>
          <p>Available quantity is on-hand minus reserved. Damaged stock is never included in these balances.</p>
        </div>
        <div className="buttonRow">
          {role === "owner" && <button className="secondaryButton" onClick={() => { setShowOpening((visible) => !visible); setShowAdjustment(false); }} type="button">{showOpening ? "Close opening balance" : "Set opening balance"}</button>}
          {canAdjust && (
            <button className="secondaryButton" onClick={() => { setShowAdjustment((visible) => !visible); setShowOpening(false); }} type="button">
              <SlidersHorizontal size={18} />
              {showAdjustment ? "Close adjustment" : "Adjust stock"}
            </button>
          )}
        </div>
      </div>

      {notice && <InlineNotice message={notice.message} tone={notice.tone} />}

      {showAdjustment && canAdjust && (
        <form className="surface formGrid adjustmentForm" onSubmit={handleAdjustment}>
          <div className="formIntro">
            <SlidersHorizontal size={22} />
            <div><strong>Reasoned stock adjustment</strong><span>Trusted, manager, and owner changes are written to movement and audit history.</span></div>
          </div>
          <label>
            <span>Product</span>
            <select onChange={(event) => setProductId(event.target.value)} required value={productId}>
              <option value="">Choose product</option>
              {(resource.data ?? []).map((item) => <option key={item.productId} value={item.productId}>{item.sku} · {item.name}</option>)}
            </select>
          </label>
          <label>
            <span>Quantity change</span>
            <input inputMode="numeric" onChange={(event) => setQuantityDelta(event.target.value)} placeholder="Use -3 or 5" type="number" value={quantityDelta} />
          </label>
          <label className="wideField">
            <span>Reason</span>
            <input onChange={(event) => setReason(event.target.value)} placeholder="Verified count discrepancy" value={reason} />
          </label>
          <button className="primaryButton" disabled={submitting} type="submit">
            {submitting && <LoaderCircle className="spin" size={18} />} Post adjustment
          </button>
        </form>
      )}

      {showOpening && role === "owner" && (
        <form className="surface formGrid adjustmentForm" onSubmit={handleOpeningBalance}>
          <div className="formIntro"><SlidersHorizontal size={22} /><div><strong>Verified opening balance</strong><span>Use once for a product with no existing warehouse balance. The movement and audit history begin here.</span></div></div>
          <label><span>Product</span><select onChange={(event) => setOpeningProductId(event.target.value)} required value={openingProductId}><option value="">Choose product</option>{(resource.data ?? []).map((item) => <option key={item.productId} value={item.productId}>{item.sku} · {item.name}</option>)}</select></label>
          <label><span>Opening quantity</span><input min="1" onChange={(event) => setOpeningQuantity(event.target.value)} step="1" type="number" value={openingQuantity} /></label>
          <label className="wideField"><span>Verification reason</span><input onChange={(event) => setOpeningReason(event.target.value)} placeholder="Verified against warehouse count sheet" value={openingReason} /></label>
          <button className="primaryButton" disabled={submitting} type="submit">{submitting && <LoaderCircle className="spin" size={18} />} Post opening balance</button>
        </form>
      )}

      <section className="surface">
        <label className="filterInput">
          <Search size={17} />
          <span className="srOnly">Filter inventory</span>
          <input onChange={(event) => setQuery(event.target.value)} placeholder="Filter SKU, UPC, or product name" value={query} />
        </label>
        <DataState
          dataLength={filteredInventory.length + (resource.nextCursor ? 1 : 0)}
          emptyMessage={query ? "No inventory matches this filter." : "No inventory balances exist for this warehouse yet."}
          error={resource.error}
          loading={resource.status === "loading"}
          onRetry={resource.reload}
        >
          <>
            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th>SKU</th><th>Product</th><th>UPC</th><th>On hand</th><th>Reserved</th><th>Available</th><th>Threshold</th><th>Status</th><th>History</th>{canSetThreshold && <th>Replenishment</th>}
                  </tr>
                </thead>
                <tbody>
                  {filteredInventory.map((item) => {
                    const low = item.available <= item.threshold;
                    return (
                      <tr key={item.productId}>
                        <td>{item.sku}</td><td>{item.name}</td><td>{item.upc}</td><td>{item.onHand}</td><td>{item.reserved}</td>
                        <td><strong>{item.available}</strong></td><td>{item.threshold}</td>
                        <td><StatusBadge label={low ? "low stock" : "healthy"} tone={low ? "amber" : "green"} /></td>
                        <td><button className="textButton" onClick={() => setMovementProductId(item.productId)} type="button">View movements for {item.sku}</button></td>
                        {canSetThreshold && <td><button className="textButton" disabled={submitting} onClick={() => void handleThreshold(item)} type="button">Set threshold for {item.sku}</button></td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {resource.loadMoreError && <InlineNotice message={resource.loadMoreError.message} tone="error" />}
            {resource.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={resource.loadingMore} onClick={() => void resource.loadMore()} type="button">{resource.loadingMore && <LoaderCircle className="spin" size={17} />} Load more inventory</button></div>}
          </>
        </DataState>
      </section>

      {movementProductId && (
        <section className="surface">
          <div className="sectionHeader"><div><p className="eyebrow">Immutable ledger</p><h3>Inventory movement history</h3></div><button className="secondaryButton" onClick={() => setMovementProductId("")} type="button">Close history</button></div>
          <DataState dataLength={movements.data?.length} emptyMessage="No movements exist for this product in the selected warehouse." error={movements.error} loading={movements.status === "loading"} onRetry={movements.reload}>
            <>
              <div className="tableWrap"><table><thead><tr><th>When</th><th>Type</th><th>On-hand change</th><th>Reserved change</th><th>Balance after</th><th>Reason</th></tr></thead><tbody>{(movements.data ?? []).map((movement) => <tr key={movement.id}><td>{new Date(movement.createdAt).toLocaleString()}</td><td>{movement.movementType}</td><td>{movement.onHandDelta > 0 ? "+" : ""}{movement.onHandDelta}</td><td>{movement.reservedDelta > 0 ? "+" : ""}{movement.reservedDelta}</td><td>{movement.onHandAfter} on hand / {movement.reservedAfter} reserved</td><td>{movement.reason ?? "Operational workflow"}</td></tr>)}</tbody></table></div>
              {movements.loadMoreError && <InlineNotice message={movements.loadMoreError.message} tone="error" />}
              {movements.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={movements.loadingMore} onClick={() => void movements.loadMore()} type="button">{movements.loadingMore && <LoaderCircle className="spin" size={17} />} Load more movements</button></div>}
            </>
          </DataState>
        </section>
      )}
    </div>
  );
}
