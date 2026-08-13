import type {
  AuditRow,
  AuditSource,
  DashboardMetrics,
  DamagedReturnRow,
  InventoryAdjustmentInput,
  InventoryMovementRow,
  OpeningBalanceInput,
  InventoryRow,
  OrderCreateInput,
  OrderItem,
  OrderRow,
  OrderStatus,
  PackageDetails,
  PackageInput,
  ProductCreateInput,
  ProductRecord,
  ProductUpdateInput,
  ReceiptCreateInput,
  ReceiptItem,
  ReceiptItemInput,
  ReceiptItemUpdateInput,
  ReceiptRow,
  ReceiptStatus,
  Role,
  TeamMember,
  UserCreateInput,
  UserProfile,
  WarehouseRef
} from "../../types/wms";

const configuredBaseUrl = import.meta.env.VITE_WMS_API_BASE_URL ?? "/api/v1";
const apiBaseUrl = configuredBaseUrl.replace(/\/$/, "");
const ACCESS_TOKEN_KEY = "whitfield_wms_access_token";
const REFRESH_TOKEN_KEY = "whitfield_wms_refresh_token";
export const AUTH_EXPIRED_EVENT = "whitfield-wms-auth-expired";

interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  auth?: boolean;
  form?: URLSearchParams;
  idempotencyKey?: string;
  json?: unknown;
  retryAuth?: boolean;
}

export interface CursorPage<T> {
  items: T[];
  nextCursor: string | null;
}

export type CreatedAtSort = "created_at_desc" | "created_at_asc";
export type InventorySort = "name_asc" | "name_desc" | "available_asc" | "available_desc";

export interface CursorPageOptions<TSort extends string = string> {
  cursor?: string;
  limit?: number;
  sort?: TSort;
}

export interface WarehousePageOptions extends CursorPageOptions<CreatedAtSort> {
  is_active?: boolean;
}

export interface InventoryPageOptions extends CursorPageOptions<InventorySort> {
  q?: string;
}

export interface InventoryMovementPageOptions extends CursorPageOptions<CreatedAtSort> {
  movement_type?: string;
  created_from?: string;
  created_to?: string;
}

export interface UserPageOptions extends CursorPageOptions<CreatedAtSort> {
  q?: string;
  role?: Role;
  is_active?: boolean;
  warehouse_id?: string;
  created_from?: string;
  created_to?: string;
}

export interface ProductSearchPageOptions extends CursorPageOptions<CreatedAtSort> {
  q?: string;
  is_active?: boolean;
  created_from?: string;
  created_to?: string;
}

export interface ReceiptPageOptions extends CursorPageOptions<CreatedAtSort> {
  status?: ReceiptStatus;
  created_from?: string;
  created_to?: string;
}

export interface DamagedReturnPageOptions extends CursorPageOptions<CreatedAtSort> {
  status?: DamagedReturnRow["status"];
  created_from?: string;
  created_to?: string;
}

export interface OrderPageOptions extends CursorPageOptions<CreatedAtSort> {
  status?: OrderStatus;
  created_from?: string;
  created_to?: string;
}

export interface AuditPageOptions extends CursorPageOptions<CreatedAtSort> {
  table_name?: string;
  record_id?: string;
  action?: string;
  source?: AuditSource;
  created_from?: string;
  created_to?: string;
}

interface TokenResponse {
  accessToken: string;
  refreshToken?: string;
  user?: UserProfile;
}

type UnknownRecord = Record<string, unknown>;

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly requestId?: string;

  constructor(message: string, status: number, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function getApiBaseUrl() {
  return apiBaseUrl;
}

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function storeTokens(accessToken: string, refreshToken?: string) {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  if (refreshToken) localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function createIdempotencyKey(operation: string) {
  const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${operation}-${suffix}`;
}

function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {};
}

function asString(value: unknown, fallback = "") {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return fallback;
}

function asNumber(value: unknown, fallback = 0) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function asBoolean(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

function dataRecord(value: unknown) {
  const root = asRecord(value);
  return root.data && !Array.isArray(root.data) ? asRecord(root.data) : root;
}

function listFrom(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const root = asRecord(value);
  if (Array.isArray(root.items)) return root.items;
  if (Array.isArray(root.results)) return root.results;
  if (Array.isArray(root.data)) return root.data;
  const data = asRecord(root.data);
  if (Array.isArray(data.items)) return data.items;
  if (Array.isArray(data.results)) return data.results;
  return [];
}

function firstDefined(record: UnknownRecord, keys: string[]) {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key];
  }
  return undefined;
}

function safeApiMessage(status: number, body: unknown) {
  const record = asRecord(body);
  const nestedDetail = asRecord(record.detail);
  const candidate = typeof record.detail === "string" ? record.detail : nestedDetail.detail;
  const detail = typeof candidate === "string" && candidate.length < 240 ? candidate : undefined;
  if (detail) return detail;
  if (status === 400 || status === 422) return "Please check the entered values and try again.";
  if (status === 401) return "Your session has expired. Please sign in again.";
  if (status === 403) return "You do not have permission to perform this action.";
  if (status === 404) return "The requested warehouse record was not found.";
  if (status === 409) return "This record changed or the action is not valid in its current state.";
  if (status >= 500) return "The WMS service is temporarily unavailable. Please try again.";
  return "The request could not be completed.";
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
    try {
      const response = await fetch(`${apiBaseUrl}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(refreshToken ? { refresh_token: refreshToken } : {})
      });
      const body = await parseResponse(response);
      if (!response.ok) return null;
      const tokens = normalizeTokens(body);
      storeTokens(tokens.accessToken, tokens.refreshToken ?? refreshToken ?? undefined);
      return tokens.accessToken;
    } catch {
      return null;
    }
  })();
  const token = await refreshPromise;
  refreshPromise = null;
  return token;
}

interface ApiResponse<T> {
  body: T;
  headers: Headers;
}

async function apiResponse<T>(path: string, options: ApiRequestOptions = {}): Promise<ApiResponse<T>> {
  const { auth = true, form, idempotencyKey, json, retryAuth = true, ...requestInit } = options;
  const headers = new Headers(requestInit.headers);
  headers.set("Accept", "application/json");
  if (auth) {
    const token = getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);

  let body: BodyInit | undefined;
  if (form) {
    headers.set("Content-Type", "application/x-www-form-urlencoded");
    body = form.toString();
  } else if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...requestInit,
      body,
      credentials: "include",
      headers
    });
  } catch {
    throw new ApiError("Cannot reach the WMS service. Check that the API is running.", 0, "NETWORK_ERROR");
  }

  if (response.status === 401 && auth && retryAuth) {
    const token = await refreshAccessToken();
    if (token) return apiResponse<T>(path, { ...options, retryAuth: false });
    clearTokens();
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }

  const responseBody = await parseResponse(response);
  if (!response.ok) {
    const error = asRecord(responseBody);
    const nestedError = asRecord(error.detail);
    throw new ApiError(
      safeApiMessage(response.status, responseBody),
      response.status,
      asString(error.code) || asString(nestedError.code) || undefined,
      asString(error.request_id) || asString(nestedError.request_id) || response.headers.get("X-Request-ID") || undefined
    );
  }
  return { body: responseBody as T, headers: response.headers };
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  return (await apiResponse<T>(path, options)).body;
}

export async function apiPageRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<CursorPage<T>> {
  const response = await apiResponse<unknown>(path, options);
  return {
    items: listFrom(response.body) as T[],
    nextCursor: response.headers.get("X-Next-Cursor")
  };
}

function normalizeWarehouse(value: unknown): WarehouseRef | null {
  const assignment = asRecord(value);
  const record = Object.keys(asRecord(assignment.warehouse)).length ? asRecord(assignment.warehouse) : assignment;
  const id = asString(firstDefined(record, ["id", "warehouse_id", "code"]));
  const name = asString(firstDefined(record, ["name", "warehouse_name", "code"]));
  if (!id && !name) return null;
  return { id: id || name.toLowerCase(), code: asString(record.code) || undefined, name: name || id };
}

function normalizeRole(value: unknown): Role {
  const role = asString(value).toLowerCase();
  return ["owner", "manager", "trusted", "staff"].includes(role) ? role as Role : "staff";
}

function normalizeUser(value: unknown): UserProfile {
  const record = dataRecord(value);
  const warehouseValues = [
    ...listFrom(record.warehouses),
    ...listFrom(record.warehouse_assignments)
  ];
  if (record.assigned_warehouse) warehouseValues.push(record.assigned_warehouse);
  if (record.warehouse) warehouseValues.push(record.warehouse);
  if (!warehouseValues.length && record.warehouse_id) {
    warehouseValues.push({ id: record.warehouse_id, name: record.warehouse_name ?? record.warehouse_code ?? "Assigned warehouse" });
  }
  const warehouses = warehouseValues
    .map(normalizeWarehouse)
    .filter((warehouse): warehouse is WarehouseRef => warehouse !== null)
    .filter((warehouse, index, values) => values.findIndex((candidate) => candidate.id === warehouse.id) === index);
  const role = normalizeRole(record.role);
  if (role === "owner" && warehouses.length === 0) {
    warehouses.push({ id: "reno", code: "RENO", name: "Reno" }, { id: "columbus", code: "CMH", name: "Columbus" });
  }
  return {
    id: asString(record.id),
    name: asString(firstDefined(record, ["name", "full_name"]), "WMS user"),
    email: asString(record.email),
    role,
    isActive: record.is_active === undefined ? true : asBoolean(record.is_active),
    warehouses
  };
}

function normalizeTokens(value: unknown): TokenResponse {
  const root = dataRecord(value);
  const accessToken = asString(firstDefined(root, ["access_token", "accessToken", "token"]));
  if (!accessToken) throw new ApiError("The sign-in response did not include an access token.", 500, "INVALID_AUTH_RESPONSE");
  const rawUser = root.user ?? root.profile;
  return {
    accessToken,
    refreshToken: asString(firstDefined(root, ["refresh_token", "refreshToken"])) || undefined,
    user: rawUser ? normalizeUser(rawUser) : undefined
  };
}

function queryString(values: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

function normalizeMetrics(value: unknown): DashboardMetrics {
  const root = dataRecord(value);
  const record = Object.keys(asRecord(root.metrics)).length ? asRecord(root.metrics) : root;
  return {
    availableUnits: asNumber(firstDefined(record, ["available_units", "available", "sellable_units"])),
    reservedUnits: asNumber(firstDefined(record, ["reserved_units", "reserved"])),
    receivingBacklog: asNumber(firstDefined(record, ["receiving_backlog", "open_receipts", "receipts_to_receive"])),
    ordersToShip: asNumber(firstDefined(record, ["orders_to_ship", "fulfillment_backlog", "open_orders"])),
    damagedReturns: asNumber(firstDefined(record, ["damaged_returns", "pending_damaged_returns"])),
    auditEvents: asNumber(firstDefined(record, ["audit_events", "audit_count"]))
  };
}

function normalizeInventory(value: unknown): InventoryRow {
  const record = asRecord(value);
  const product = asRecord(record.product);
  const onHand = asNumber(firstDefined(record, ["on_hand", "quantity_on_hand"]));
  const reserved = asNumber(firstDefined(record, ["reserved", "quantity_reserved"]));
  return {
    id: asString(firstDefined(record, ["id", "balance_id", "product_id"])),
    productId: asString(firstDefined(record, ["product_id", "id"])) || asString(product.id),
    warehouseId: asString(record.warehouse_id) || undefined,
    sku: asString(record.sku) || asString(product.sku),
    upc: asString(record.upc) || asString(product.upc),
    name: asString(firstDefined(record, ["name", "product_name"])) || asString(product.name),
    onHand,
    reserved,
    available: asNumber(record.available, onHand - reserved),
    threshold: asNumber(firstDefined(record, ["low_stock_threshold", "threshold"]))
  };
}

function normalizeMovement(value: unknown): InventoryMovementRow {
  const record = asRecord(value);
  return {
    id: asString(record.id),
    movementType: asString(record.movement_type),
    onHandDelta: asNumber(record.on_hand_delta),
    reservedDelta: asNumber(record.reserved_delta),
    onHandAfter: asNumber(record.on_hand_after),
    reservedAfter: asNumber(record.reserved_after),
    reason: asString(record.reason) || undefined,
    createdAt: asString(record.created_at)
  };
}

function normalizeReceiptItem(value: unknown): ReceiptItem {
  const record = asRecord(value);
  const product = asRecord(record.product);
  return {
    id: asString(record.id),
    productId: asString(record.product_id) || asString(product.id),
    sku: asString(record.sku) || asString(product.sku),
    name: asString(firstDefined(record, ["product_name", "name"])) || asString(product.name, "Product"),
    quantityReceived: asNumber(firstDefined(record, ["quantity_received", "received"])),
    quantityAccepted: asNumber(firstDefined(record, ["quantity_accepted", "accepted"])),
    quantityDamaged: asNumber(firstDefined(record, ["quantity_damaged", "damaged"])),
    damageNotes: asString(record.damage_notes) || undefined
  };
}

function normalizeReceipt(value: unknown): ReceiptRow {
  const record = dataRecord(value);
  const items = listFrom(record.items).map(normalizeReceiptItem);
  const id = asString(record.id);
  const tracking = asString(record.tracking_number);
  const ticket = asString(record.ticket_number);
  return {
    id,
    displayId: asString(firstDefined(record, ["display_id", "receipt_number"])) || (id ? `INB-${id.slice(0, 8)}` : "Inbound receipt"),
    sender: asString(firstDefined(record, ["sender_name", "sender"]), "Unknown sender"),
    reference: tracking || ticket || "No reference",
    status: (asString(record.status, "open") as ReceiptStatus),
    accepted: asNumber(firstDefined(record, ["quantity_accepted", "accepted"]), items.reduce((sum, item) => sum + item.quantityAccepted, 0)),
    damaged: asNumber(firstDefined(record, ["quantity_damaged", "damaged"]), items.reduce((sum, item) => sum + item.quantityDamaged, 0)),
    lines: asNumber(firstDefined(record, ["line_count", "lines"]), items.length),
    warehouseId: asString(record.warehouse_id) || undefined,
    items
  };
}

function normalizeDamagedReturn(value: unknown): DamagedReturnRow {
  const record = asRecord(value);
  const product = asRecord(record.product);
  return {
    id: asString(record.id),
    receiptId: asString(record.receipt_id),
    productName: asString(firstDefined(record, ["product_name", "sku"])) || asString(firstDefined(product, ["name", "sku"]), "Product"),
    quantity: asNumber(record.quantity),
    status: (asString(record.status, "pending_return") as DamagedReturnRow["status"]),
    returnTrackingNumber: asString(record.return_tracking_number) || undefined
  };
}

function normalizeOrderItem(value: unknown): OrderItem {
  const record = asRecord(value);
  const product = asRecord(record.product);
  return {
    id: asString(record.id),
    productId: asString(record.product_id) || asString(product.id),
    sku: asString(record.sku) || asString(product.sku),
    name: asString(firstDefined(record, ["product_name", "name"])) || asString(product.name, "Product"),
    quantity: asNumber(record.quantity),
    pickedQuantity: asNumber(firstDefined(record, ["picked_quantity", "pickedQuantity"]))
  };
}

function normalizePackage(value: unknown): PackageDetails | undefined {
  const record = asRecord(value);
  if (Object.keys(record).length === 0) return undefined;
  return {
    id: asString(record.id) || undefined,
    weight: record.weight === undefined ? undefined : asNumber(record.weight),
    weightUnit: asString(record.weight_unit) || undefined,
    length: record.length === undefined ? undefined : asNumber(record.length),
    width: record.width === undefined ? undefined : asNumber(record.width),
    height: record.height === undefined ? undefined : asNumber(record.height),
    dimensionUnit: asString(record.dimension_unit) || undefined,
    carrier: asString(record.carrier) || undefined,
    serviceLevel: asString(record.service_level) || undefined,
    trackingNumber: asString(record.tracking_number) || undefined,
    labelUrl: asString(firstDefined(record, ["label_url", "label_url_or_key"])) || undefined
  };
}

function normalizeOrder(value: unknown): OrderRow {
  const record = dataRecord(value);
  const items = listFrom(record.items).map(normalizeOrderItem);
  const packages = listFrom(record.packages);
  const packageDetails = normalizePackage(record.package ?? packages[0]);
  const id = asString(record.id);
  const status = asString(record.status, "pending") as OrderStatus;
  const packageState = packageDetails?.trackingNumber
    ? `Tracking ${packageDetails.trackingNumber}`
    : status === "packed" ? "Ready for label" : status === "label_created" ? "Ready to ship" : status === "cancelled" ? "Reservation released" : "Awaiting next step";
  return {
    id,
    displayId: asString(firstDefined(record, ["display_id", "order_number"])) || (id ? `ORD-${id.slice(0, 8)}` : "Order"),
    reference: asString(firstDefined(record, ["external_reference", "reference"]), "No external reference"),
    status,
    itemCount: asNumber(firstDefined(record, ["item_count", "items_count", "items"]), items.length),
    units: asNumber(firstDefined(record, ["unit_count", "units"]), items.reduce((sum, item) => sum + item.quantity, 0)),
    packageState,
    warehouseId: asString(record.warehouse_id) || undefined,
    items,
    package: packageDetails
  };
}

function normalizeAudit(value: unknown): AuditRow {
  const record = asRecord(value);
  const actor = asRecord(record.actor);
  const time = asString(firstDefined(record, ["created_at", "time"]));
  return {
    id: asString(record.id) || `${asString(record.record_id)}-${time}`,
    actor: asString(firstDefined(record, ["actor_name", "actor"])) || asString(firstDefined(actor, ["name", "email"]), "System"),
    action: asString(record.action, "Updated record").replace(/_/g, " "),
    source: (asString(record.source, "system") as AuditRow["source"]),
    target: asString(firstDefined(record, ["target", "record_id"])) || asString(record.table_name, "record"),
    time,
    reason: asString(record.reason) || undefined
  };
}

function normalizeTeamMember(value: unknown): TeamMember {
  const user = normalizeUser(value);
  const warehouse = user.role === "owner" ? "Both" : user.warehouses[0]?.name ?? "Unassigned";
  return {
    id: user.id,
    name: user.name,
    email: user.email,
    role: user.role,
    warehouse,
    warehouseId: user.warehouses[0]?.id,
    state: user.isActive ? "Active" : "Disabled"
  };
}

function normalizeProduct(value: unknown): ProductRecord {
  const record = asRecord(value);
  return {
    id: asString(record.id),
    sku: asString(record.sku),
    upc: asString(record.upc),
    name: asString(record.name, "Product"),
    description: asString(record.description) || undefined,
    isActive: record.is_active === undefined ? true : asBoolean(record.is_active)
  };
}

function mapCursorPage<T, U>(page: CursorPage<T>, normalize: (item: T) => U): CursorPage<U> {
  return {
    items: page.items.map(normalize),
    nextCursor: page.nextCursor
  };
}

async function getWarehousesPage(options: WarehousePageOptions = {}): Promise<CursorPage<WarehouseRef>> {
  const page = await apiPageRequest<unknown>(`/warehouses${queryString({
    cursor: options.cursor,
    limit: options.limit,
    sort: options.sort,
    is_active: options.is_active
  })}`);
  return {
    items: page.items.map(normalizeWarehouse).filter((warehouse): warehouse is WarehouseRef => warehouse !== null),
    nextCursor: page.nextCursor
  };
}

async function getInventoryPage(
  warehouseId?: string,
  options: InventoryPageOptions = {}
): Promise<CursorPage<InventoryRow>> {
  const page = await apiPageRequest<unknown>(`/inventory${queryString({
    warehouse_id: warehouseId,
    q: options.q,
    cursor: options.cursor,
    limit: options.limit ?? 200,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeInventory);
}

async function getLowStockPage(
  warehouseId?: string,
  options: InventoryPageOptions = {}
): Promise<CursorPage<InventoryRow>> {
  const page = await apiPageRequest<unknown>(`/inventory/low-stock${queryString({
    warehouse_id: warehouseId,
    q: options.q,
    cursor: options.cursor,
    limit: options.limit ?? 100,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeInventory);
}

async function getInventoryMovementsPage(
  warehouseId: string,
  productId: string,
  options: InventoryMovementPageOptions = {}
): Promise<CursorPage<InventoryMovementRow>> {
  const page = await apiPageRequest<unknown>(`/inventory/${encodeURIComponent(productId)}/movements${queryString({
    warehouse_id: warehouseId,
    movement_type: options.movement_type,
    created_from: options.created_from,
    created_to: options.created_to,
    cursor: options.cursor,
    limit: options.limit ?? 200,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeMovement);
}

async function getUsersPage(options: UserPageOptions = {}): Promise<CursorPage<TeamMember>> {
  const page = await apiPageRequest<unknown>(`/users${queryString({
    q: options.q,
    role: options.role,
    is_active: options.is_active,
    warehouse_id: options.warehouse_id,
    created_from: options.created_from,
    created_to: options.created_to,
    cursor: options.cursor,
    limit: options.limit ?? 200,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeTeamMember);
}

async function searchProductsPage(
  queryOrOptions: string | ProductSearchPageOptions = "",
  options: Omit<ProductSearchPageOptions, "q"> = {}
): Promise<CursorPage<ProductRecord>> {
  const resolvedOptions: ProductSearchPageOptions = typeof queryOrOptions === "string"
    ? { ...options, q: queryOrOptions }
    : queryOrOptions;
  const page = await apiPageRequest<unknown>(`/products/search${queryString({
    q: resolvedOptions.q,
    is_active: resolvedOptions.is_active,
    created_from: resolvedOptions.created_from,
    created_to: resolvedOptions.created_to,
    cursor: resolvedOptions.cursor,
    limit: resolvedOptions.limit ?? 20,
    sort: resolvedOptions.sort
  })}`);
  return mapCursorPage(page, normalizeProduct);
}

async function getReceiptsPage(
  warehouseId?: string,
  options: ReceiptPageOptions = {}
): Promise<CursorPage<ReceiptRow>> {
  const page = await apiPageRequest<unknown>(`/inbound-receipts${queryString({
    warehouse_id: warehouseId,
    status: options.status,
    created_from: options.created_from,
    created_to: options.created_to,
    cursor: options.cursor,
    limit: options.limit ?? 100,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeReceipt);
}

async function getDamagedReturnsPage(
  warehouseId?: string,
  options: DamagedReturnPageOptions = {}
): Promise<CursorPage<DamagedReturnRow>> {
  const page = await apiPageRequest<unknown>(`/damaged-returns${queryString({
    warehouse_id: warehouseId,
    status: options.status,
    created_from: options.created_from,
    created_to: options.created_to,
    cursor: options.cursor,
    limit: options.limit ?? 100,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeDamagedReturn);
}

async function getOrdersPage(
  warehouseId?: string,
  options: OrderPageOptions = {}
): Promise<CursorPage<OrderRow>> {
  const page = await apiPageRequest<unknown>(`/orders${queryString({
    warehouse_id: warehouseId,
    status: options.status,
    created_from: options.created_from,
    created_to: options.created_to,
    cursor: options.cursor,
    limit: options.limit ?? 100,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeOrder);
}

async function getAuditLogsPage(
  warehouseId?: string,
  options: AuditPageOptions = {}
): Promise<CursorPage<AuditRow>> {
  const page = await apiPageRequest<unknown>(`/audit-logs${queryString({
    warehouse_id: warehouseId,
    table_name: options.table_name,
    record_id: options.record_id,
    action: options.action,
    source: options.source,
    created_from: options.created_from,
    created_to: options.created_to,
    cursor: options.cursor,
    limit: options.limit ?? 200,
    sort: options.sort
  })}`);
  return mapCursorPage(page, normalizeAudit);
}

export const authApi = {
  async login(email: string, password: string): Promise<TokenResponse> {
    const response = await apiRequest<unknown>("/auth/login", {
      method: "POST",
      auth: false,
      retryAuth: false,
      json: { email, password }
    });
    return normalizeTokens(response);
  },
  async me() {
    return normalizeUser(await apiRequest<unknown>("/auth/me"));
  },
  async logout() {
    try {
      const refreshToken = getRefreshToken();
      if (refreshToken) {
        await apiRequest<unknown>("/auth/logout", {
          method: "POST",
          retryAuth: false,
          json: { refresh_token: refreshToken }
        });
      }
    } finally {
      clearTokens();
    }
  }
};

export const wmsApi = {
  getWarehousesPage,
  async getWarehouses(options: WarehousePageOptions = {}) {
    return (await getWarehousesPage(options)).items;
  },
  async getDashboard(warehouseId?: string) {
    return normalizeMetrics(await apiRequest<unknown>(`/dashboard/summary${queryString({ warehouse_id: warehouseId })}`));
  },
  getInventoryPage,
  async getInventory(warehouseId?: string, options: InventoryPageOptions = {}) {
    return (await getInventoryPage(warehouseId, options)).items;
  },
  getInventoryMovementsPage,
  async getInventoryMovements(warehouseId: string, productId: string, options: InventoryMovementPageOptions = {}) {
    return (await getInventoryMovementsPage(warehouseId, productId, options)).items;
  },
  getLowStockPage,
  async getLowStock(warehouseId?: string, options: InventoryPageOptions = {}) {
    return (await getLowStockPage(warehouseId, options)).items;
  },
  async adjustInventory(input: InventoryAdjustmentInput, idempotencyKey = createIdempotencyKey("inventory-adjustment")) {
    return apiRequest<unknown>("/inventory/adjustments", {
      method: "POST",
      json: input,
      idempotencyKey
    });
  },
  async postOpeningBalance(input: OpeningBalanceInput, idempotencyKey = createIdempotencyKey("opening-balance")) {
    return apiRequest<unknown>("/inventory/opening-balances", {
      method: "POST",
      json: input,
      idempotencyKey
    });
  },
  getReceiptsPage,
  async getReceipts(warehouseId?: string, options: ReceiptPageOptions = {}) {
    return (await getReceiptsPage(warehouseId, options)).items;
  },
  async getReceipt(id: string) {
    return normalizeReceipt(await apiRequest<unknown>(`/inbound-receipts/${encodeURIComponent(id)}`));
  },
  async createReceipt(input: ReceiptCreateInput, idempotencyKey = createIdempotencyKey("receipt-create")) {
    return normalizeReceipt(await apiRequest<unknown>("/inbound-receipts", {
      method: "POST",
      json: input,
      idempotencyKey
    }));
  },
  async addReceiptItem(receiptId: string, input: ReceiptItemInput, idempotencyKey = createIdempotencyKey("receipt-item")) {
    return normalizeReceipt(await apiRequest<unknown>(`/inbound-receipts/${encodeURIComponent(receiptId)}/items`, {
      method: "POST",
      json: input,
      idempotencyKey
    }));
  },
  async updateReceiptItem(receiptId: string, itemId: string, input: ReceiptItemUpdateInput, idempotencyKey = createIdempotencyKey("receipt-item-update")) {
    return normalizeReceipt(await apiRequest<unknown>(`/inbound-receipts/${encodeURIComponent(receiptId)}/items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      json: input,
      idempotencyKey
    }));
  },
  async deleteReceiptItem(receiptId: string, itemId: string, idempotencyKey = createIdempotencyKey("receipt-item-delete")) {
    return normalizeReceipt(await apiRequest<unknown>(`/inbound-receipts/${encodeURIComponent(receiptId)}/items/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
      idempotencyKey
    }));
  },
  async receiveReceipt(receiptId: string, idempotencyKey = createIdempotencyKey("receipt-receive")) {
    return apiRequest<unknown>(`/inbound-receipts/${encodeURIComponent(receiptId)}/receive`, {
      method: "POST",
      json: {},
      idempotencyKey
    });
  },
  async cancelReceipt(receiptId: string, reason: string, idempotencyKey = createIdempotencyKey("receipt-cancel")) {
    return apiRequest<unknown>(`/inbound-receipts/${encodeURIComponent(receiptId)}/cancel`, {
      method: "POST",
      json: { reason },
      idempotencyKey
    });
  },
  getDamagedReturnsPage,
  async getDamagedReturns(warehouseId?: string, options: DamagedReturnPageOptions = {}) {
    return (await getDamagedReturnsPage(warehouseId, options)).items;
  },
  async completeDamagedReturn(id: string, returnTrackingNumber: string, idempotencyKey = createIdempotencyKey("damaged-return")) {
    return apiRequest<unknown>(`/damaged-returns/${encodeURIComponent(id)}/complete`, {
      method: "POST",
      json: { return_tracking_number: returnTrackingNumber },
      idempotencyKey
    });
  },
  getOrdersPage,
  async getOrders(warehouseId?: string, options: OrderPageOptions = {}) {
    return (await getOrdersPage(warehouseId, options)).items;
  },
  async getOrder(id: string) {
    return normalizeOrder(await apiRequest<unknown>(`/orders/${encodeURIComponent(id)}`));
  },
  async createOrder(input: OrderCreateInput, idempotencyKey = createIdempotencyKey("order-create")) {
    return normalizeOrder(await apiRequest<unknown>("/orders", {
      method: "POST",
      json: input,
      idempotencyKey
    }));
  },
  async startPicking(id: string, idempotencyKey = createIdempotencyKey("order-pick")) {
    return apiRequest<unknown>(`/orders/${encodeURIComponent(id)}/start-picking`, { method: "POST", json: {}, idempotencyKey });
  },
  async confirmPickedItem(orderId: string, itemId: string, pickedQuantity: number, idempotencyKey = createIdempotencyKey("order-item-pick")) {
    return apiRequest<unknown>(`/orders/${encodeURIComponent(orderId)}/items/${encodeURIComponent(itemId)}/pick`, {
      method: "POST",
      json: { picked_quantity: pickedQuantity },
      idempotencyKey
    });
  },
  async packOrder(id: string, input: PackageInput, idempotencyKey = createIdempotencyKey("order-pack")) {
    return apiRequest<unknown>(`/orders/${encodeURIComponent(id)}/pack`, { method: "POST", json: input, idempotencyKey });
  },
  async createLabel(id: string, carrier = "fake", serviceLevel = "ground", idempotencyKey = createIdempotencyKey("order-label")) {
    return apiRequest<unknown>(`/orders/${encodeURIComponent(id)}/create-label`, {
      method: "POST",
      json: { carrier, service_level: serviceLevel },
      idempotencyKey
    });
  },
  async shipOrder(id: string, idempotencyKey = createIdempotencyKey("order-ship")) {
    return apiRequest<unknown>(`/orders/${encodeURIComponent(id)}/ship`, {
      method: "POST",
      idempotencyKey
    });
  },
  async cancelOrder(id: string, reason: string, idempotencyKey = createIdempotencyKey("order-cancel")) {
    return apiRequest<unknown>(`/orders/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      json: { reason },
      idempotencyKey
    });
  },
  getAuditLogsPage,
  async getAuditLogs(warehouseId?: string, options: AuditPageOptions = {}) {
    return (await getAuditLogsPage(warehouseId, options)).items;
  },
  getUsersPage,
  async getUsers(options: UserPageOptions = {}) {
    return (await getUsersPage(options)).items;
  },
  async createUser(input: UserCreateInput) {
    return normalizeTeamMember(await apiRequest<unknown>("/users", { method: "POST", json: input }));
  },
  async updateUser(id: string, input: { is_active?: boolean; name?: string; role?: Role; warehouse_id?: string | null }) {
    return normalizeTeamMember(await apiRequest<unknown>(`/users/${encodeURIComponent(id)}`, { method: "PATCH", json: input }));
  },
  async assignUserWarehouse(id: string, warehouseId: string) {
    return normalizeTeamMember(await apiRequest<unknown>(`/users/${encodeURIComponent(id)}/warehouse-assignment`, {
      method: "PUT",
      json: { warehouse_id: warehouseId }
    }));
  },
  async resetUserPassword(id: string, password: string) {
    return apiRequest<{ detail: string }>(`/users/${encodeURIComponent(id)}/reset-password`, {
      method: "POST",
      json: { password }
    });
  },
  searchProductsPage,
  async searchProducts(query: string, options: Omit<ProductSearchPageOptions, "q"> = {}) {
    return (await searchProductsPage(query, options)).items;
  },
  async getProduct(id: string) {
    return normalizeProduct(await apiRequest<unknown>(`/products/${encodeURIComponent(id)}`));
  },
  async createProduct(input: ProductCreateInput) {
    return normalizeProduct(await apiRequest<unknown>("/products", { method: "POST", json: input }));
  },
  async updateProduct(id: string, input: ProductUpdateInput) {
    return normalizeProduct(await apiRequest<unknown>(`/products/${encodeURIComponent(id)}`, { method: "PATCH", json: input }));
  },
  async setProductThreshold(warehouseId: string, productId: string, lowStockThreshold: number) {
    return apiRequest<{ warehouse_id: string; product_id: string; low_stock_threshold: number }>(
      `/warehouses/${encodeURIComponent(warehouseId)}/products/${encodeURIComponent(productId)}/threshold`,
      { method: "PUT", json: { low_stock_threshold: lowStockThreshold } }
    );
  }
};
