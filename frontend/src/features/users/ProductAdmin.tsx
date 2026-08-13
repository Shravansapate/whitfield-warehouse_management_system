import { Box, LoaderCircle, PackagePlus, Pencil, Power, Save, Search } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCursorResource } from "../../hooks/useCursorResource";
import { ApiError, wmsApi } from "../../lib/api/client";
import type { ProductCreateInput, ProductRecord, WarehouseRef } from "../../types/wms";

interface ProductAdminProps {
  onChanged: () => void;
  warehouse: WarehouseRef;
}

const blankProduct: ProductCreateInput = { sku: "", upc: "", name: "", description: "" };

function productForm(product: ProductRecord): ProductCreateInput {
  return {
    sku: product.sku,
    upc: product.upc,
    name: product.name,
    description: product.description ?? ""
  };
}

function productValidation(form: ProductCreateInput) {
  if (!form.sku.trim() || form.name.trim().length < 2) return "Enter a SKU and a product name of at least two characters.";
  if (!/^[A-Za-z0-9-]{4,32}$/.test(form.upc.trim())) return "UPC must be 4-32 letters, numbers, or hyphens.";
  if ((form.description ?? "").length > 2000) return "Description must be 2,000 characters or fewer.";
  return null;
}

export function ProductAdmin({ onChanged, warehouse }: ProductAdminProps) {
  const inventory = useCursorResource(
    (cursor) => wmsApi.getInventoryPage(warehouse.id, { cursor }),
    [warehouse.id],
    true
  );
  const [filter, setFilter] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<ProductCreateInput>(blankProduct);
  const [selectedProduct, setSelectedProduct] = useState<ProductRecord | null>(null);
  const [editForm, setEditForm] = useState<ProductCreateInput>(blankProduct);
  const [lookupId, setLookupId] = useState("");
  const [thresholdDrafts, setThresholdDrafts] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "error" } | null>(null);

  useEffect(() => {
    if (!inventory.data) return;
    setThresholdDrafts((current) => {
      const next = { ...current };
      for (const row of inventory.data ?? []) {
        if (next[row.productId] === undefined) next[row.productId] = String(row.threshold);
      }
      return next;
    });
  }, [inventory.data, warehouse.id]);

  const filteredProducts = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return inventory.data ?? [];
    return (inventory.data ?? []).filter((row) => [row.sku, row.upc, row.name].some((value) => value.toLowerCase().includes(query)));
  }, [filter, inventory.data]);

  const selectProduct = async (productId: string) => {
    setSubmitting(`load-${productId}`);
    setNotice(null);
    try {
      const product = await wmsApi.getProduct(productId);
      setSelectedProduct(product);
      setEditForm(productForm(product));
      setLookupId(product.id);
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The product could not be loaded.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleLookup = (event: FormEvent) => {
    event.preventDefault();
    if (!lookupId.trim()) {
      setNotice({ message: "Enter the product ID from its audit record.", tone: "error" });
      return;
    }
    void selectProduct(lookupId.trim());
  };

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    const validation = productValidation(createForm);
    if (validation) {
      setNotice({ message: validation, tone: "error" });
      return;
    }
    setSubmitting("create-product");
    try {
      const product = await wmsApi.createProduct({
        sku: createForm.sku.trim(),
        upc: createForm.upc.trim(),
        name: createForm.name.trim(),
        description: createForm.description?.trim() || undefined
      });
      setCreateForm(blankProduct);
      setShowCreate(false);
      setSelectedProduct(product);
      setEditForm(productForm(product));
      setLookupId(product.id);
      setNotice({ message: `${product.sku} was added to the product master.`, tone: "success" });
      inventory.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The product could not be created.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleUpdate = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedProduct) return;
    const validation = productValidation(editForm);
    if (validation) {
      setNotice({ message: validation, tone: "error" });
      return;
    }
    setSubmitting("update-product");
    try {
      const product = await wmsApi.updateProduct(selectedProduct.id, {
        sku: editForm.sku.trim(),
        upc: editForm.upc.trim(),
        name: editForm.name.trim(),
        description: editForm.description?.trim() ?? ""
      });
      setSelectedProduct(product);
      setEditForm(productForm(product));
      setNotice({ message: `${product.sku} product details were updated.`, tone: "success" });
      inventory.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The product could not be updated.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const toggleProduct = async () => {
    if (!selectedProduct) return;
    const nextActive = !selectedProduct.isActive;
    if (!window.confirm(`${nextActive ? "Reactivate" : "Deactivate"} ${selectedProduct.sku}? Inventory and audit history will be retained.`)) return;
    setSubmitting("toggle-product");
    try {
      const product = await wmsApi.updateProduct(selectedProduct.id, { is_active: nextActive });
      setSelectedProduct(product);
      setEditForm(productForm(product));
      setNotice({
        message: nextActive ? `${product.sku} is active again.` : `${product.sku} is inactive and unavailable to warehouse workflows.`,
        tone: "success"
      });
      inventory.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The product state could not be changed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const saveThreshold = async (productId: string, sku: string) => {
    const threshold = Number(thresholdDrafts[productId]);
    if (!Number.isInteger(threshold) || threshold < 0 || threshold > 1_000_000) {
      setNotice({ message: "Low-stock threshold must be a whole number from 0 to 1,000,000.", tone: "error" });
      return;
    }
    setSubmitting(`threshold-${productId}`);
    try {
      await wmsApi.setProductThreshold(warehouse.id, productId, threshold);
      setNotice({ message: `${warehouse.name} threshold for ${sku} is now ${threshold}.`, tone: "success" });
      inventory.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The threshold could not be saved.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <section className="ownerSection pageStack" aria-labelledby="product-admin-title">
      <div className="sectionHeader">
        <div>
          <p className="eyebrow">Product administration</p>
          <h3 id="product-admin-title">Product master and {warehouse.name} thresholds</h3>
          <p>Create global product records, control activation, and tune replenishment signals for the selected warehouse.</p>
        </div>
        <button className="secondaryButton" onClick={() => setShowCreate((visible) => !visible)} type="button">
          <PackagePlus size={18} /> {showCreate ? "Close product form" : "Add product"}
        </button>
      </div>

      {notice && <InlineNotice message={notice.message} tone={notice.tone} />}

      {showCreate && (
        <form className="surface formGrid productForm" onSubmit={handleCreate}>
          <label><span>New product SKU</span><input autoFocus maxLength={80} onChange={(event) => setCreateForm({ ...createForm, sku: event.target.value })} value={createForm.sku} /></label>
          <label><span>New product UPC</span><input maxLength={32} onChange={(event) => setCreateForm({ ...createForm, upc: event.target.value })} value={createForm.upc} /></label>
          <label><span>New product name</span><input maxLength={200} onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })} value={createForm.name} /></label>
          <label className="wideField"><span>New product description</span><textarea maxLength={2000} onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })} rows={3} value={createForm.description} /></label>
          <button className="primaryButton" disabled={submitting === "create-product"} type="submit">
            {submitting === "create-product" && <LoaderCircle className="spin" size={18} />} Create product
          </button>
        </form>
      )}

      <div className="productAdminGrid">
        <article className="surface productCatalog">
          <div className="sectionHeader compactHeader">
            <div><h4>Active catalog</h4><p>Every active product, including products with zero stock.</p></div>
            <StatusBadge label={warehouse.name} tone="blue" />
          </div>
          <label className="filterInput productFilter"><Search size={17} /><span className="srOnly">Filter product catalog</span><input onChange={(event) => setFilter(event.target.value)} placeholder="Filter SKU, UPC, or name" value={filter} /></label>
          <DataState dataLength={filteredProducts.length + (inventory.nextCursor ? 1 : 0)} emptyMessage="No active products match this filter." error={inventory.error} loading={inventory.status === "loading"} onRetry={inventory.reload}>
            <>
              <div className="recordList productList">
              {filteredProducts.map((product) => (
                <div className="recordCard productAdminRow" key={product.productId}>
                  <div className="productIdentity"><Box size={20} /><div><strong>{product.sku}</strong><span>{product.name}</span><small>UPC {product.upc} · {product.available} available</small></div></div>
                  <div className="thresholdControl">
                    <label><span className="srOnly">Threshold for {product.sku}</span><input aria-label={`Threshold for ${product.sku}`} max={1_000_000} min={0} onChange={(event) => setThresholdDrafts({ ...thresholdDrafts, [product.productId]: event.target.value })} step={1} type="number" value={thresholdDrafts[product.productId] ?? String(product.threshold)} /></label>
                    <button className="textButton" disabled={Boolean(submitting)} onClick={() => void saveThreshold(product.productId, product.sku)} type="button"><Save size={15} /> Save threshold for {product.sku}</button>
                  </div>
                  <button className="secondaryButton compactButton" disabled={Boolean(submitting)} onClick={() => void selectProduct(product.productId)} type="button"><Pencil size={15} /> Edit {product.sku}</button>
                </div>
              ))}
              </div>
              {inventory.loadMoreError && <InlineNotice message={inventory.loadMoreError.message} tone="error" />}
              {inventory.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={inventory.loadingMore} onClick={() => void inventory.loadMore()} type="button">{inventory.loadingMore && <LoaderCircle className="spin" size={17} />} Load more products</button></div>}
            </>
          </DataState>
        </article>

        <article className="surface productEditor">
          <div>
            <h4>Edit or reactivate a product</h4>
            <p>Inactive products are hidden from warehouse workflows. Load one by its immutable audit record ID to reactivate it.</p>
          </div>
          <form className="lookupForm" onSubmit={handleLookup}>
            <label><span>Product ID lookup</span><input onChange={(event) => setLookupId(event.target.value)} placeholder="UUID from product or audit record" value={lookupId} /></label>
            <button className="secondaryButton" disabled={Boolean(submitting)} type="submit">Load product</button>
          </form>

          {selectedProduct ? (
            <form className="formStack productEditForm" onSubmit={handleUpdate}>
              <div className="editorTitle"><div><strong>{selectedProduct.sku}</strong><small>{selectedProduct.id}</small></div><StatusBadge label={selectedProduct.isActive ? "Active" : "Inactive"} tone={selectedProduct.isActive ? "green" : "red"} /></div>
              <label><span>Edit SKU</span><input maxLength={80} onChange={(event) => setEditForm({ ...editForm, sku: event.target.value })} value={editForm.sku} /></label>
              <label><span>Edit UPC</span><input maxLength={32} onChange={(event) => setEditForm({ ...editForm, upc: event.target.value })} value={editForm.upc} /></label>
              <label><span>Edit product name</span><input maxLength={200} onChange={(event) => setEditForm({ ...editForm, name: event.target.value })} value={editForm.name} /></label>
              <label><span>Edit product description</span><textarea maxLength={2000} onChange={(event) => setEditForm({ ...editForm, description: event.target.value })} rows={4} value={editForm.description} /></label>
              <div className="buttonRow">
                <button className="primaryButton" disabled={Boolean(submitting)} type="submit">{submitting === "update-product" && <LoaderCircle className="spin" size={18} />} Save product</button>
                <button className={selectedProduct.isActive ? "secondaryButton dangerButton" : "secondaryButton"} disabled={Boolean(submitting)} onClick={() => void toggleProduct()} type="button"><Power size={17} /> {selectedProduct.isActive ? "Deactivate product" : "Reactivate product"}</button>
              </div>
            </form>
          ) : (
            <div className="emptyEditor"><Box size={24} /><span>Choose Edit beside a catalog item or load a product ID.</span></div>
          )}
        </article>
      </div>
    </section>
  );
}
