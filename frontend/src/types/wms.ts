import type { LucideIcon } from "lucide-react";

export type Role = "owner" | "manager" | "trusted" | "staff";
export type WarehouseName = "Reno" | "Columbus" | string;
export type NavigationId = "dashboard" | "receiving" | "inventory" | "orders" | "audit" | "owner";
export type AsyncStatus = "idle" | "loading" | "success" | "error";

export interface NavigationItem {
  id: NavigationId;
  label: string;
  icon: LucideIcon;
}

export interface WarehouseRef {
  id: string;
  code?: string;
  name: WarehouseName;
}

export interface UserProfile {
  id: string;
  name: string;
  email: string;
  role: Role;
  isActive: boolean;
  warehouses: WarehouseRef[];
}

export interface DashboardMetrics {
  availableUnits: number;
  reservedUnits: number;
  receivingBacklog: number;
  ordersToShip: number;
  damagedReturns: number;
  auditEvents: number;
}

export interface InventoryRow {
  id: string;
  productId: string;
  warehouseId?: string;
  sku: string;
  upc: string;
  name: string;
  onHand: number;
  reserved: number;
  available: number;
  threshold: number;
}

export type ReceiptStatus = "open" | "receiving" | "received" | "cancelled";

export interface ReceiptItem {
  id: string;
  productId: string;
  sku: string;
  name: string;
  quantityReceived: number;
  quantityAccepted: number;
  quantityDamaged: number;
  damageNotes?: string;
}

export interface ReceiptRow {
  id: string;
  displayId: string;
  sender: string;
  reference: string;
  status: ReceiptStatus;
  accepted: number;
  damaged: number;
  lines: number;
  warehouseId?: string;
  items: ReceiptItem[];
}

export interface DamagedReturnRow {
  id: string;
  receiptId: string;
  productName: string;
  quantity: number;
  status: "pending_return" | "returned_to_sender" | "cancelled";
  returnTrackingNumber?: string;
}

export type OrderStatus =
  | "pending"
  | "allocated"
  | "picking"
  | "packed"
  | "label_created"
  | "shipped"
  | "cannot_fulfill"
  | "cancelled";

export interface OrderItem {
  id: string;
  productId: string;
  sku: string;
  name: string;
  quantity: number;
  pickedQuantity: number;
}

export interface PackageDetails {
  id?: string;
  weight?: number;
  weightUnit?: string;
  length?: number;
  width?: number;
  height?: number;
  dimensionUnit?: string;
  carrier?: string;
  serviceLevel?: string;
  trackingNumber?: string;
  labelUrl?: string;
}

export interface OrderRow {
  id: string;
  displayId: string;
  reference: string;
  status: OrderStatus;
  itemCount: number;
  units: number;
  packageState: string;
  warehouseId?: string;
  items: OrderItem[];
  package?: PackageDetails;
}

export type AuditSource = "web" | "scanner" | "voice" | "automation" | "api" | "system";

export interface AuditRow {
  id: string;
  actor: string;
  action: string;
  source: AuditSource;
  target: string;
  time: string;
  reason?: string;
}

export interface TeamMember {
  id: string;
  name: string;
  email: string;
  role: Role;
  warehouse: "Both" | WarehouseName;
  warehouseId?: string;
  state: "Active" | "Disabled";
}

export interface ProductSearchResult {
  id: string;
  sku: string;
  upc: string;
  name: string;
}

export interface ProductRecord extends ProductSearchResult {
  description?: string;
  isActive: boolean;
}

export interface ProductCreateInput {
  sku: string;
  upc: string;
  name: string;
  description?: string;
}

export interface ProductUpdateInput {
  sku?: string;
  upc?: string;
  name?: string;
  description?: string;
  is_active?: boolean;
}

export interface ReceiptCreateInput {
  warehouse_id?: string;
  tracking_number?: string;
  ticket_number?: string;
  sender_name: string;
  sender_contact?: string;
  sender_return_address: string;
}

export interface ReceiptItemInput {
  product_id: string;
  quantity_received: number;
  quantity_accepted: number;
  quantity_damaged: number;
  damage_notes?: string;
}

export interface ReceiptItemUpdateInput {
  quantity_received: number;
  quantity_accepted: number;
  quantity_damaged: number;
  damage_notes?: string;
}

export interface InventoryAdjustmentInput {
  warehouse_id?: string;
  product_id: string;
  quantity_delta: number;
  reason: string;
}

export interface InventoryMovementRow {
  id: string;
  movementType: string;
  onHandDelta: number;
  reservedDelta: number;
  onHandAfter: number;
  reservedAfter: number;
  reason?: string;
  createdAt: string;
}

export interface OpeningBalanceInput {
  warehouse_id: string;
  product_id: string;
  quantity: number;
  reason: string;
}

export interface PackageInput {
  weight: number;
  weight_unit: "lb" | "kg";
  length: number;
  width: number;
  height: number;
  dimension_unit: "in" | "cm";
  carrier?: string;
  service_level?: string;
}

export interface OrderCreateInput {
  external_reference: string;
  warehouse_id?: string;
  items: Array<{ product_id: string; quantity: number }>;
}

export interface UserCreateInput {
  name: string;
  email: string;
  password: string;
  role: Role;
  warehouse_id?: string;
}

export interface WmsSnapshot {
  warehouse: WarehouseName;
  metrics: DashboardMetrics;
  inventory: InventoryRow[];
  receipts: ReceiptRow[];
  orders: OrderRow[];
  audit: AuditRow[];
  team: TeamMember[];
}

export const emptyMetrics: DashboardMetrics = {
  availableUnits: 0,
  reservedUnits: 0,
  receivingBacklog: 0,
  ordersToShip: 0,
  damagedReturns: 0,
  auditEvents: 0
};
