import { useEffect, useMemo, useState } from "react";
import { Activity, Boxes, ClipboardCheck, Gauge, LoaderCircle, PackageCheck, ShieldCheck, Truck, UsersRound } from "lucide-react";
import { AppShell } from "../components/layout/AppShell";
import { DataState, InlineNotice } from "../components/ui/DataState";
import { AuditPage } from "../features/audit/AuditPage";
import { useAuth } from "../features/auth/AuthContext";
import { LoginPage } from "../features/auth/LoginPage";
import { LandingPage } from "../features/landing/LandingPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { InventoryPage } from "../features/inventory/InventoryPage";
import { OrdersPage } from "../features/orders/OrdersPage";
import { ReceivingPage } from "../features/receiving/ReceivingPage";
import { OwnerPage } from "../features/users/OwnerPage";
import { VoiceAssistant } from "../features/assistant/VoiceAssistant";
import { useApiResource } from "../hooks/useApiResource";
import { useCursorResource } from "../hooks/useCursorResource";
import { ApiError, getAccessToken, wmsApi } from "../lib/api/client";
import type { NavigationItem, WarehouseRef } from "../types/wms";

const navigation: NavigationItem[] = [
  { id: "dashboard", label: "Command", icon: Gauge },
  { id: "receiving", label: "Receiving", icon: ClipboardCheck },
  { id: "inventory", label: "Inventory", icon: Boxes },
  { id: "orders", label: "Orders", icon: PackageCheck },
  { id: "audit", label: "Audit", icon: ShieldCheck },
  { id: "owner", label: "Owner", icon: UsersRound }
];

const combinedWarehouse: WarehouseRef = { id: "all-warehouses", code: "ALL", name: "All warehouses" };

function AuthorizedApp() {
  const { logout, user } = useAuth();
  const [activePage, setActivePage] = useState<NavigationItem["id"]>("dashboard");
  const [selectedWarehouseId, setSelectedWarehouseId] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const accessToken = getAccessToken();
  const warehouseResource = useCursorResource(
    (cursor) => wmsApi.getWarehousesPage({ cursor }),
    [],
    true
  );

  const fallbackWarehouses = useMemo<WarehouseRef[]>(() => {
    if (!user) return [];
    if (user.warehouses.length) return user.warehouses;
    return user.role === "owner"
      ? [{ id: "reno", name: "Reno" }, { id: "columbus", name: "Columbus" }]
      : [{ id: "assigned", name: "Assigned warehouse" }];
  }, [user]);
  const warehouses = warehouseResource.data?.length ? warehouseResource.data : fallbackWarehouses;

  const combinedDashboardSelected = user?.role === "owner"
    && activePage === "dashboard"
    && selectedWarehouseId === combinedWarehouse.id;
  const warehouseOptions = combinedDashboardSelected || (user?.role === "owner" && activePage === "dashboard")
    ? [combinedWarehouse, ...warehouses]
    : warehouses;

  useEffect(() => {
    if (!warehouses.length) return;
    if (selectedWarehouseId === combinedWarehouse.id && user?.role === "owner" && activePage === "dashboard") return;
    if (!selectedWarehouseId || !warehouses.some((warehouse) => warehouse.id === selectedWarehouseId)) {
      setSelectedWarehouseId(warehouses[0].id);
    }
  }, [activePage, selectedWarehouseId, user?.role, warehouses]);

  const selectedWarehouse = combinedDashboardSelected
    ? combinedWarehouse
    : warehouses.find((warehouse) => warehouse.id === selectedWarehouseId) ?? warehouses[0];
  const dashboardResource = useApiResource(
    () => wmsApi.getDashboard(combinedDashboardSelected ? undefined : selectedWarehouse?.id),
    [combinedDashboardSelected, selectedWarehouse?.id, refreshVersion],
    Boolean(selectedWarehouse)
  );

  const visibleNavigation = navigation.filter((item) => {
    if (item.id === "owner") return user?.role === "owner";
    if (item.id === "audit") return user?.role === "owner" || user?.role === "manager";
    return true;
  });

  useEffect(() => {
    if (!visibleNavigation.some((item) => item.id === activePage)) setActivePage("dashboard");
  }, [activePage, visibleNavigation]);

  if (!user || !selectedWarehouse) {
    return (
      <main className="loginPage">
        <div className="statePanel"><LoaderCircle className="spin" size={28} /><strong>Loading warehouse access…</strong></div>
      </main>
    );
  }

  const refreshAll = () => setRefreshVersion((version) => version + 1);
  const page = {
    dashboard: <DashboardPage combined={combinedDashboardSelected} key={`dashboard-${selectedWarehouse.id}`} error={dashboardResource.error} metrics={dashboardResource.data} onNavigate={setActivePage} onRetry={dashboardResource.reload} refreshVersion={refreshVersion} status={dashboardResource.status} warehouse={selectedWarehouse} />,
    receiving: <ReceivingPage key={`receiving-${selectedWarehouse.id}`} onChanged={refreshAll} refreshVersion={refreshVersion} role={user.role} warehouse={selectedWarehouse} />,
    inventory: <InventoryPage key={`inventory-${selectedWarehouse.id}`} onChanged={refreshAll} refreshVersion={refreshVersion} role={user.role} warehouse={selectedWarehouse} />,
    orders: <OrdersPage key={`orders-${selectedWarehouse.id}`} onChanged={refreshAll} refreshVersion={refreshVersion} warehouse={selectedWarehouse} />,
    audit: <AuditPage key={`audit-${selectedWarehouse.id}`} refreshVersion={refreshVersion} warehouse={selectedWarehouse} />,
    owner: <OwnerPage key={`owner-${selectedWarehouse.id}`} onChanged={refreshAll} warehouse={selectedWarehouse} warehouses={warehouses} />
  }[activePage];

  const handleSearch = async (query: string) => {
    const normalized = query.trim();
    if (!normalized) return;
    setSearchMessage("Searching product master…");
    try {
      const products = await wmsApi.searchProducts(normalized);
      if (!products.length) setSearchMessage(`No product found for “${normalized}”.`);
      else {
        setSearchMessage(`${products[0].sku} · ${products[0].name}${products.length > 1 ? ` and ${products.length - 1} more` : ""}`);
        setActivePage("inventory");
      }
    } catch (caught) {
      setSearchMessage(caught instanceof ApiError ? caught.message : "Search failed.");
    }
  };

  return (
    <AppShell
      activePage={activePage}
      hasMoreWarehouses={Boolean(warehouseResource.nextCursor)}
      loadingMoreWarehouses={warehouseResource.loadingMore}
      metrics={dashboardResource.data}
      navigation={visibleNavigation}
      onLoadMoreWarehouses={() => void warehouseResource.loadMore()}
      onLogout={() => void logout()}
      onNavigate={setActivePage}
      onNotifications={() => {
        setActivePage("dashboard");
        setSearchMessage("Low-stock alerts are shown in the selected warehouse command view.");
      }}
      onSearch={(query) => void handleSearch(query)}
      onWarehouseChange={setSelectedWarehouseId}
      selectedWarehouse={selectedWarehouse}
      user={user}
      warehouses={warehouseOptions}
    >
      {warehouseResource.status === "error" && !fallbackWarehouses.length ? (
        <DataState error={warehouseResource.error} loading={false} onRetry={warehouseResource.reload}><span /></DataState>
      ) : (
        <>
          {warehouseResource.loadMoreError && <InlineNotice message={warehouseResource.loadMoreError.message} tone="error" />}
          {searchMessage && <InlineNotice message={searchMessage} />}
          {page}
        </>
      )}
      <button
        className="voiceDock"
        aria-label="Open voice receiving assistant"
        aria-expanded={voiceOpen}
        onClick={() => setVoiceOpen((v) => !v)}
        type="button"
      >
        <Activity size={18} />
        <span>Voice assistant</span>
      </button>
      {voiceOpen && accessToken && (
        <VoiceAssistant
          accessToken={accessToken}
          onClose={() => setVoiceOpen(false)}
          warehouseName={selectedWarehouse.name}
        />
      )}
      <div className="shipmentRibbon" aria-label="Shipment label provider status">
        <Truck size={16} />
        <span>Development label adapter</span>
      </div>
    </AppShell>
  );
}

export function App() {
  const { status } = useAuth();
  const [showLanding, setShowLanding] = useState(true);

  if (status === "checking") {
    return <main className="loginPage"><div className="statePanel"><LoaderCircle className="spin" size={28} /><strong>Checking your secure session…</strong></div></main>;
  }

  if (status === "unauthenticated" && showLanding) {
    return <LandingPage onSignIn={() => setShowLanding(false)} />;
  }

  return status === "authenticated" ? <AuthorizedApp /> : <LoginPage />;
}
