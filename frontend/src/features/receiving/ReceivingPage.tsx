import { Barcode, CheckCircle2, LoaderCircle, Pencil, Plus, RotateCcw, ScanLine, Search, Trash2, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useApiResource } from "../../hooks/useApiResource";
import { useCursorResource } from "../../hooks/useCursorResource";
import { ApiError, createIdempotencyKey, wmsApi } from "../../lib/api/client";
import type { ProductSearchResult, ReceiptCreateInput, ReceiptItem, ReceiptItemUpdateInput, Role, WarehouseRef } from "../../types/wms";

interface ReceivingPageProps {
  onChanged: () => void;
  refreshVersion: number;
  role: Role;
  warehouse: WarehouseRef;
}

const initialReceiptForm: ReceiptCreateInput = {
  sender_name: "",
  tracking_number: "",
  ticket_number: "",
  sender_contact: "",
  sender_return_address: ""
};

interface ReceiptLineDraft {
  received: string;
  accepted: string;
  damaged: string;
  damageNotes: string;
}

export function ReceivingPage({ onChanged, refreshVersion, role, warehouse }: ReceivingPageProps) {
  const receipts = useCursorResource(
    (cursor) => wmsApi.getReceiptsPage(warehouse.id, { cursor }),
    [warehouse.id, refreshVersion]
  );
  const damagedReturns = useCursorResource(
    (cursor) => wmsApi.getDamagedReturnsPage(warehouse.id, { cursor, status: "pending_return" }),
    [warehouse.id, refreshVersion]
  );
  const [activeReceiptId, setActiveReceiptId] = useState("");
  const receiptDetail = useApiResource(() => wmsApi.getReceipt(activeReceiptId), [activeReceiptId, warehouse.id, refreshVersion], Boolean(activeReceiptId));
  const [showCreate, setShowCreate] = useState(false);
  const [receiptForm, setReceiptForm] = useState<ReceiptCreateInput>(initialReceiptForm);
  const [productQuery, setProductQuery] = useState("");
  const [productResults, setProductResults] = useState<ProductSearchResult[]>([]);
  const [productNextCursor, setProductNextCursor] = useState<string | null>(null);
  const [productLoadingMore, setProductLoadingMore] = useState(false);
  const [productLoadMoreError, setProductLoadMoreError] = useState<string | null>(null);
  const [selectedProduct, setSelectedProduct] = useState<ProductSearchResult | null>(null);
  const [received, setReceived] = useState("1");
  const [accepted, setAccepted] = useState("1");
  const [damaged, setDamaged] = useState("0");
  const [damageNotes, setDamageNotes] = useState("");
  const [editingItemId, setEditingItemId] = useState("");
  const [lineDraft, setLineDraft] = useState<ReceiptLineDraft | null>(null);
  const [returnTracking, setReturnTracking] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "error" | "info" } | null>(null);
  const itemRetry = useRef<{ signature: string; key: string } | null>(null);
  const commandRetries = useRef<Record<string, { signature: string; key: string }>>({});
  const productSearchGeneration = useRef(0);
  const canCompleteDamagedReturn = role === "owner" || role === "manager" || role === "trusted";

  const warehouseReceipts = useMemo(
    () => (receipts.data ?? []).filter((receipt) => receipt.warehouseId === warehouse.id),
    [receipts.data, warehouse.id]
  );
  const detailCandidate = receiptDetail.data;
  const detailedActiveReceipt = detailCandidate?.id === activeReceiptId && detailCandidate.warehouseId === warehouse.id ? detailCandidate : null;
  const activeReceipt = detailedActiveReceipt ?? warehouseReceipts.find((receipt) => receipt.id === activeReceiptId) ?? null;

  const commandKey = (operation: string, payload: unknown) => {
    const signature = JSON.stringify(payload);
    if (commandRetries.current[operation]?.signature !== signature) {
      commandRetries.current[operation] = { signature, key: createIdempotencyKey(operation) };
    }
    return commandRetries.current[operation].key;
  };

  useEffect(() => {
    setActiveReceiptId("");
    setShowCreate(false);
    setReceiptForm(initialReceiptForm);
    setProductQuery("");
    setProductResults([]);
    setProductNextCursor(null);
    setProductLoadingMore(false);
    setProductLoadMoreError(null);
    setSelectedProduct(null);
    setReceived("1");
    setAccepted("1");
    setDamaged("0");
    setDamageNotes("");
    setEditingItemId("");
    setLineDraft(null);
    setReturnTracking({});
    setSubmitting(null);
    setNotice(null);
    itemRetry.current = null;
    commandRetries.current = {};
    productSearchGeneration.current += 1;
  }, [warehouse.id]);

  useEffect(() => {
    if (receipts.status !== "success") return;
    const currentExists = warehouseReceipts.some((receipt) => receipt.id === activeReceiptId && ["open", "receiving"].includes(receipt.status));
    if (currentExists) return;
    const next = warehouseReceipts.find((receipt) => receipt.status === "receiving")
      ?? warehouseReceipts.find((receipt) => receipt.status === "open");
    setActiveReceiptId(next?.id ?? "");
  }, [activeReceiptId, receipts.status, warehouseReceipts]);

  const refreshReceiving = () => {
    receipts.reload();
    damagedReturns.reload();
    receiptDetail.reload();
    onChanged();
  };

  const handleCreateReceipt = async (event: FormEvent) => {
    event.preventDefault();
    if (!receiptForm.sender_name.trim() || (!receiptForm.tracking_number?.trim() && !receiptForm.ticket_number?.trim()) || !receiptForm.sender_return_address.trim()) {
      setNotice({ message: "Enter a sender, tracking or ticket number, and the sender return address.", tone: "error" });
      return;
    }
    setSubmitting("create");
    setNotice(null);
    try {
      const input = {
        ...receiptForm,
        warehouse_id: warehouse.id,
        sender_name: receiptForm.sender_name.trim(),
        tracking_number: receiptForm.tracking_number?.trim() || undefined,
        ticket_number: receiptForm.ticket_number?.trim() || undefined,
        sender_contact: receiptForm.sender_contact?.trim() || undefined,
        sender_return_address: receiptForm.sender_return_address.trim()
      };
      const created = await wmsApi.createReceipt(input, commandKey("receipt-create", input));
      delete commandRetries.current["receipt-create"];
      setActiveReceiptId(created.id);
      setReceiptForm(initialReceiptForm);
      setShowCreate(false);
      setNotice({ message: `${created.displayId} created. Scan its first product below.`, tone: "success" });
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The receipt could not be created.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleProductSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!productQuery.trim()) return;
    setSubmitting("search");
    setNotice(null);
    setProductLoadMoreError(null);
    const requestGeneration = ++productSearchGeneration.current;
    try {
      const page = await wmsApi.searchProductsPage(productQuery.trim());
      if (productSearchGeneration.current !== requestGeneration) return;
      setProductResults(page.items);
      setProductNextCursor(page.nextCursor);
      const exact = page.items.find((product) => product.upc === productQuery.trim() || product.sku.toLowerCase() === productQuery.trim().toLowerCase());
      if (exact) setSelectedProduct(exact);
      if (!page.items.length) setNotice({ message: "No active product matches that UPC, SKU, or name.", tone: "error" });
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "Product search failed.", tone: "error" });
      setProductNextCursor(null);
    } finally {
      setSubmitting(null);
    }
  };

  const loadMoreProducts = async () => {
    const query = productQuery.trim();
    if (!query || !productNextCursor || productLoadingMore) return;
    const requestGeneration = productSearchGeneration.current;
    setProductLoadingMore(true);
    setProductLoadMoreError(null);
    try {
      const page = await wmsApi.searchProductsPage(query, { cursor: productNextCursor });
      if (productSearchGeneration.current !== requestGeneration) return;
      setProductResults((current) => {
        const knownIds = new Set(current.map((product) => product.id));
        return [...current, ...page.items.filter((product) => !knownIds.has(product.id))];
      });
      setProductNextCursor(page.nextCursor);
    } catch (caught) {
      setProductLoadMoreError(caught instanceof ApiError ? caught.message : "More products could not be loaded.");
    } finally {
      setProductLoadingMore(false);
    }
  };

  const handleAddItem = async (event: FormEvent) => {
    event.preventDefault();
    const receivedCount = Number(received);
    const acceptedCount = Number(accepted);
    const damagedCount = Number(damaged);
    if (!activeReceipt || !selectedProduct || !Number.isInteger(receivedCount) || receivedCount <= 0 || acceptedCount < 0 || damagedCount < 0 || acceptedCount + damagedCount !== receivedCount) {
      setNotice({ message: "Select a product and make sure accepted plus damaged equals the positive received quantity.", tone: "error" });
      return;
    }
    if (damagedCount > 0 && damageNotes.trim().length < 3) {
      setNotice({ message: "Add damage notes so the return-to-sender record is traceable.", tone: "error" });
      return;
    }
    const input = {
      product_id: selectedProduct.id,
      quantity_received: receivedCount,
      quantity_accepted: acceptedCount,
      quantity_damaged: damagedCount,
      damage_notes: damageNotes.trim() || undefined
    };
    const signature = JSON.stringify({ receipt: activeReceipt.id, warehouse: warehouse.id, ...input });
    if (itemRetry.current?.signature !== signature) itemRetry.current = { signature, key: createIdempotencyKey("receipt-item") };
    setSubmitting("item");
    setNotice(null);
    try {
      await wmsApi.addReceiptItem(activeReceipt.id, input, itemRetry.current.key);
      itemRetry.current = null;
      setProductQuery("");
      setProductResults([]);
      setProductNextCursor(null);
      setProductLoadMoreError(null);
      setSelectedProduct(null);
      setReceived("1");
      setAccepted("1");
      setDamaged("0");
      setDamageNotes("");
      setNotice({ message: "Draft receipt line saved. Inventory will not change until completion.", tone: "success" });
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? `${caught.message} You can safely retry this same scan.` : "The receipt line could not be saved.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const beginLineEdit = (item: ReceiptItem) => {
    setEditingItemId(item.id);
    setLineDraft({
      received: String(item.quantityReceived),
      accepted: String(item.quantityAccepted),
      damaged: String(item.quantityDamaged),
      damageNotes: item.damageNotes ?? ""
    });
    setNotice(null);
  };

  const cancelLineEdit = () => {
    setEditingItemId("");
    setLineDraft(null);
  };

  const handleUpdateItem = async (event: FormEvent, item: ReceiptItem) => {
    event.preventDefault();
    if (!activeReceipt || !lineDraft || editingItemId !== item.id) return;
    const input: ReceiptItemUpdateInput = {
      quantity_received: Number(lineDraft.received),
      quantity_accepted: Number(lineDraft.accepted),
      quantity_damaged: Number(lineDraft.damaged),
      damage_notes: lineDraft.damageNotes.trim() || undefined
    };
    if (!Number.isInteger(input.quantity_received) || input.quantity_received <= 0 || !Number.isInteger(input.quantity_accepted) || input.quantity_accepted < 0 || !Number.isInteger(input.quantity_damaged) || input.quantity_damaged < 0 || input.quantity_accepted + input.quantity_damaged !== input.quantity_received) {
      setNotice({ message: "Correct the line so accepted plus damaged equals the positive received quantity.", tone: "error" });
      return;
    }
    if (input.quantity_damaged > 0 && (input.damage_notes?.length ?? 0) < 3) {
      setNotice({ message: "Add damage notes so the corrected damaged quantity is traceable.", tone: "error" });
      return;
    }
    const operation = `receipt-item-update-${item.id}`;
    const payload = { receiptId: activeReceipt.id, warehouseId: warehouse.id, itemId: item.id, ...input };
    setSubmitting(operation);
    setNotice(null);
    try {
      await wmsApi.updateReceiptItem(activeReceipt.id, item.id, input, commandKey(operation, payload));
      delete commandRetries.current[operation];
      cancelLineEdit();
      setNotice({ message: `${item.sku || item.name} draft line corrected. Inventory is still unchanged until completion.`, tone: "success" });
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The draft receipt line could not be corrected.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleDeleteItem = async (item: ReceiptItem) => {
    if (!activeReceipt) return;
    const receiptId = activeReceipt.id;
    const operation = `receipt-item-delete-${item.id}`;
    if (!window.confirm(`Delete ${item.sku || item.name} from ${activeReceipt.displayId}? This removes the draft line only; no inventory has posted yet.`)) return;
    setSubmitting(operation);
    setNotice(null);
    try {
      await wmsApi.deleteReceiptItem(receiptId, item.id, commandKey(operation, { receiptId, warehouseId: warehouse.id, itemId: item.id }));
      delete commandRetries.current[operation];
      if (editingItemId === item.id) cancelLineEdit();
      setNotice({ message: `${item.sku || item.name} removed from the draft receipt. Inventory remains unchanged.`, tone: "success" });
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The draft receipt line could not be deleted.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleComplete = async () => {
    const detail = activeReceipt;
    if (!detail || detail.lines < 1) {
      setNotice({ message: "Add at least one receipt line before completing receiving.", tone: "error" });
      return;
    }
    if (!window.confirm(`Complete ${detail.displayId}? This posts ${detail.accepted} accepted units and creates returns for ${detail.damaged} damaged units.`)) return;
    setSubmitting("receive");
    try {
      await wmsApi.receiveReceipt(detail.id, commandKey("receipt-receive", { receiptId: detail.id, warehouseId: warehouse.id }));
      delete commandRetries.current["receipt-receive"];
      setNotice({ message: `${detail.displayId} completed. Accepted stock is now available.`, tone: "success" });
      setActiveReceiptId("");
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "Receiving could not be completed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleCancel = async () => {
    if (!activeReceipt) return;
    const receiptId = activeReceipt.id;
    const reason = window.prompt("Why is this draft receipt being cancelled?")?.trim();
    if (!reason) return;
    if (!window.confirm("Cancel this receipt? Draft lines will not post to inventory.")) return;
    setSubmitting("cancel");
    try {
      await wmsApi.cancelReceipt(receiptId, reason, commandKey("receipt-cancel", { receiptId, warehouseId: warehouse.id, reason }));
      delete commandRetries.current["receipt-cancel"];
      setNotice({ message: "Receipt cancelled without changing inventory.", tone: "success" });
      setActiveReceiptId("");
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The receipt could not be cancelled.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleReturnComplete = async (id: string) => {
    if (!canCompleteDamagedReturn) return;
    const tracking = returnTracking[id]?.trim();
    if (!tracking) {
      setNotice({ message: "Enter return tracking before closing a damaged return.", tone: "error" });
      return;
    }
    setSubmitting(`return-${id}`);
    try {
      await wmsApi.completeDamagedReturn(id, tracking, commandKey(`damaged-return-${id}`, { id, tracking }));
      delete commandRetries.current[`damaged-return-${id}`];
      setNotice({ message: "Damaged return marked returned to sender.", tone: "success" });
      refreshReceiving();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The damaged return could not be completed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="pageStack">
      <div className="pageHeader">
        <div>
          <p className="eyebrow">Scanner-first receiving</p>
          <h2>Inbound shipments</h2>
          <p>Draft scans do not update stock. Accepted inventory posts only after an explicit completion.</p>
        </div>
        <button className="primaryButton" onClick={() => setShowCreate((visible) => !visible)} type="button">
          <ScanLine size={18} /> {showCreate ? "Close form" : "New receipt"}
        </button>
      </div>

      {notice && <InlineNotice message={notice.message} tone={notice.tone} />}

      {showCreate && (
        <form className="surface formGrid receiptCreateForm" onSubmit={handleCreateReceipt}>
          <div className="formIntro wideField"><Plus size={22} /><div><strong>Create inbound receipt</strong><span>Use either a carrier tracking number or a drop-off ticket.</span></div></div>
          <label><span>Sender name</span><input onChange={(event) => setReceiptForm({ ...receiptForm, sender_name: event.target.value })} required value={receiptForm.sender_name} /></label>
          <label><span>Tracking number</span><input onChange={(event) => setReceiptForm({ ...receiptForm, tracking_number: event.target.value })} value={receiptForm.tracking_number} /></label>
          <label><span>Drop-off ticket</span><input onChange={(event) => setReceiptForm({ ...receiptForm, ticket_number: event.target.value })} value={receiptForm.ticket_number} /></label>
          <label><span>Sender contact</span><input onChange={(event) => setReceiptForm({ ...receiptForm, sender_contact: event.target.value })} value={receiptForm.sender_contact} /></label>
          <label className="wideField"><span>Return address</span><input onChange={(event) => setReceiptForm({ ...receiptForm, sender_return_address: event.target.value })} required value={receiptForm.sender_return_address} /></label>
          <button className="primaryButton" disabled={submitting === "create"} type="submit">{submitting === "create" && <LoaderCircle className="spin" size={18} />} Create receipt</button>
        </form>
      )}

      <section className="receivingGrid">
        <article className="surface scannerConsole">
          <div className="sectionHeader">
            <div><p className="eyebrow">Active receipt</p><h3>{activeReceipt?.displayId ?? "Choose a receipt"}</h3></div>
            {activeReceipt && <StatusBadge label={activeReceipt.status} tone="blue" />}
          </div>
          {!activeReceipt ? (
            <div className="statePanel"><Barcode size={24} /><strong>Select an open receipt from the queue.</strong></div>
          ) : (
            <>
              <form className="searchForm" onSubmit={handleProductSearch}>
                <label className="scanInput">
                  <Barcode size={22} />
                  <input autoComplete="off" onChange={(event) => { productSearchGeneration.current += 1; setProductQuery(event.target.value); setSelectedProduct(null); setProductResults([]); setProductNextCursor(null); setProductLoadMoreError(null); }} placeholder="Scan UPC or type SKU" aria-label="Scan product UPC" value={productQuery} />
                </label>
                <button className="secondaryButton" disabled={submitting === "search"} type="submit"><Search size={18} /> Find</button>
              </form>
              {productResults.length > 0 && !selectedProduct && (
                <div className="selectionList" aria-label="Product search results">
                  {productResults.map((product) => (
                    <button key={product.id} onClick={() => { productSearchGeneration.current += 1; setSelectedProduct(product); setProductQuery(`${product.sku} · ${product.name}`); setProductNextCursor(null); setProductLoadMoreError(null); }} type="button">
                      <strong>{product.sku}</strong><span>{product.name}</span><small>{product.upc}</small>
                    </button>
                  ))}
                </div>
              )}
              {productLoadMoreError && <InlineNotice message={productLoadMoreError} tone="error" />}
              {productNextCursor && !selectedProduct && <div className="buttonRow"><button className="secondaryButton" disabled={productLoadingMore} onClick={() => void loadMoreProducts()} type="button">{productLoadingMore && <LoaderCircle className="spin" size={17} />} Load more products</button></div>}
              {selectedProduct && <InlineNotice message={`Selected ${selectedProduct.sku} · ${selectedProduct.name}`} />}
              <form className="formStack" onSubmit={handleAddItem}>
                <div className="quantityPad">
                  <label><span>Received</span><input min="1" onChange={(event) => setReceived(event.target.value)} type="number" value={received} /></label>
                  <label><span>Accepted</span><input min="0" onChange={(event) => setAccepted(event.target.value)} type="number" value={accepted} /></label>
                  <label><span>Damaged</span><input min="0" onChange={(event) => setDamaged(event.target.value)} type="number" value={damaged} /></label>
                </div>
                <textarea aria-label="Damage notes" onChange={(event) => setDamageNotes(event.target.value)} placeholder="Damage notes for return to sender" value={damageNotes} />
                <button className="secondaryButton fullWidth" disabled={submitting === "item" || !selectedProduct} type="submit">
                  {submitting === "item" && <LoaderCircle className="spin" size={18} />} Save draft line
                </button>
              </form>
              <div className="receiptLineSummary">
                {(activeReceipt.items ?? []).map((item) => editingItemId === item.id && lineDraft ? (
                  <form aria-label={`Edit ${item.sku || item.name} draft line`} className="draftLineEdit" key={item.id} onSubmit={(event) => void handleUpdateItem(event, item)}>
                    <div className="draftLineHeading"><strong>{item.sku || item.name}</strong><span>Correct draft quantities before posting</span></div>
                    <div className="quantityPad">
                      <label><span>Received for {item.sku}</span><input min="1" onChange={(event) => setLineDraft({ ...lineDraft, received: event.target.value })} type="number" value={lineDraft.received} /></label>
                      <label><span>Accepted for {item.sku}</span><input min="0" onChange={(event) => setLineDraft({ ...lineDraft, accepted: event.target.value })} type="number" value={lineDraft.accepted} /></label>
                      <label><span>Damaged for {item.sku}</span><input min="0" onChange={(event) => setLineDraft({ ...lineDraft, damaged: event.target.value })} type="number" value={lineDraft.damaged} /></label>
                    </div>
                    <label><span>Damage notes for {item.sku}</span><textarea onChange={(event) => setLineDraft({ ...lineDraft, damageNotes: event.target.value })} value={lineDraft.damageNotes} /></label>
                    <div className="buttonRow">
                      <button className="secondaryButton" disabled={Boolean(submitting)} onClick={cancelLineEdit} type="button">Cancel edit</button>
                      <button className="primaryButton" disabled={Boolean(submitting)} type="submit">{submitting === `receipt-item-update-${item.id}` && <LoaderCircle className="spin" size={18} />} Save correction</button>
                    </div>
                  </form>
                ) : (
                  <div className="draftLine" key={item.id}>
                    <span><strong>{item.sku || item.name}</strong><small>{item.quantityReceived} received · {item.quantityAccepted} accepted · {item.quantityDamaged} damaged</small></span>
                    <span className="draftLineActions">
                      <button aria-label={`Edit ${item.sku || item.name} draft line`} className="secondaryButton compactButton" disabled={Boolean(submitting)} onClick={() => beginLineEdit(item)} type="button"><Pencil size={15} /> Edit</button>
                      <button aria-label={`Delete ${item.sku || item.name} draft line`} className="secondaryButton dangerButton compactButton" disabled={Boolean(submitting)} onClick={() => void handleDeleteItem(item)} type="button">{submitting === `receipt-item-delete-${item.id}` ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete</button>
                    </span>
                  </div>
                ))}
              </div>
              <div className="confirmBand">
                <CheckCircle2 size={22} />
                <div><strong>Complete receiving posts {activeReceipt.accepted} accepted units</strong><span>{activeReceipt.damaged} damaged units become pending returns, never sellable stock.</span></div>
              </div>
              <div className="buttonRow">
                <button className="secondaryButton dangerButton" disabled={Boolean(submitting)} onClick={() => void handleCancel()} type="button"><XCircle size={17} /> Cancel draft</button>
                <button className="primaryButton" disabled={Boolean(submitting) || activeReceipt.lines < 1} onClick={() => void handleComplete()} type="button">
                  {submitting === "receive" && <LoaderCircle className="spin" size={18} />} Complete receiving
                </button>
              </div>
            </>
          )}
        </article>

        <article className="surface">
          <div className="sectionHeader"><div><p className="eyebrow">Queue</p><h3>Receipts</h3></div><StatusBadge label={`${receipts.data?.length ?? 0} records`} tone="gray" /></div>
          <DataState dataLength={warehouseReceipts.length} emptyMessage="No inbound receipts exist for this warehouse." error={receipts.error} loading={receipts.status === "loading"} onRetry={receipts.reload}>
            <>
              <div className="recordList">
                {warehouseReceipts.map((receipt) => (
                <button className={receipt.id === activeReceiptId ? "recordCard selectableCard selectedCard" : "recordCard selectableCard"} disabled={["received", "cancelled"].includes(receipt.status)} key={receipt.id} onClick={() => setActiveReceiptId(receipt.id)} type="button">
                  <div><strong>{receipt.displayId}</strong><span>{receipt.sender}</span><small>{receipt.reference}</small></div>
                  <div className="recordMeta"><StatusBadge label={receipt.status} tone={receipt.status === "received" ? "green" : receipt.status === "cancelled" ? "gray" : "blue"} /><small>{receipt.accepted} accepted / {receipt.damaged} damaged</small></div>
                </button>
                ))}
              </div>
              {receipts.loadMoreError && <InlineNotice message={receipts.loadMoreError.message} tone="error" />}
              {receipts.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={receipts.loadingMore} onClick={() => void receipts.loadMore()} type="button">{receipts.loadingMore && <LoaderCircle className="spin" size={17} />} Load more receipts</button></div>}
            </>
          </DataState>
        </article>

        <article className="surface returnPanel">
          <RotateCcw size={28} />
          <h3>Damaged return lane</h3>
          <p>Each damaged unit needs sender return tracking before the return is closed.</p>
          <DataState dataLength={damagedReturns.data?.filter((item) => item.status === "pending_return").length} emptyMessage="No damaged returns are pending." error={damagedReturns.error} loading={damagedReturns.status === "loading"} onRetry={damagedReturns.reload}>
            <>
              <div className="recordList">
                {(damagedReturns.data ?? []).filter((item) => item.status === "pending_return").map((item) => (
                <div className="returnRecord" key={item.id}>
                  <strong>{item.productName} · {item.quantity} units</strong>
                  {canCompleteDamagedReturn ? (
                    <>
                      <input aria-label={`Return tracking for ${item.productName}`} onChange={(event) => setReturnTracking({ ...returnTracking, [item.id]: event.target.value })} placeholder="Return tracking" value={returnTracking[item.id] ?? ""} />
                      <button className="secondaryButton" disabled={submitting === `return-${item.id}`} onClick={() => void handleReturnComplete(item.id)} type="button">Mark returned</button>
                    </>
                  ) : <small>Trusted, manager, or owner access is required to close this return.</small>}
                </div>
                ))}
              </div>
              {damagedReturns.loadMoreError && <InlineNotice message={damagedReturns.loadMoreError.message} tone="error" />}
              {damagedReturns.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={damagedReturns.loadingMore} onClick={() => void damagedReturns.loadMore()} type="button">{damagedReturns.loadingMore && <LoaderCircle className="spin" size={17} />} Load more damaged returns</button></div>}
            </>
          </DataState>
        </article>
      </section>
    </div>
  );
}
