import type { ReactNode } from "react";
import { Bell, ChevronDown, LockKeyhole, LogOut, MapPin, Search } from "lucide-react";
import type { DashboardMetrics, NavigationItem, UserProfile, WarehouseRef } from "../../types/wms";

interface AppShellProps {
  activePage: NavigationItem["id"];
  children: ReactNode;
  hasMoreWarehouses: boolean;
  loadingMoreWarehouses: boolean;
  metrics: DashboardMetrics | null;
  navigation: NavigationItem[];
  onLogout: () => void;
  onLoadMoreWarehouses: () => void;
  onNavigate: (page: NavigationItem["id"]) => void;
  onNotifications: () => void;
  onSearch: (query: string) => void;
  onWarehouseChange: (warehouseId: string) => void;
  selectedWarehouse: WarehouseRef;
  user: UserProfile;
  warehouses: WarehouseRef[];
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "WU";
}

export function AppShell({
  activePage,
  children,
  hasMoreWarehouses,
  loadingMoreWarehouses,
  metrics,
  navigation,
  onLogout,
  onLoadMoreWarehouses,
  onNavigate,
  onNotifications,
  onSearch,
  onWarehouseChange,
  selectedWarehouse,
  user,
  warehouses
}: AppShellProps) {
  return (
    <div className="appFrame">
      <aside className="sidebar">
        <div className="brandBlock">
          <div className="brandMark">W</div>
          <div>
            <p className="eyebrow">Whitfield</p>
            <h1>WMS Control</h1>
          </div>
        </div>

        <nav className="navList" aria-label="Primary navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <button
                aria-label={item.label}
                aria-current={activePage === item.id ? "page" : undefined}
                className={activePage === item.id ? "navItem navItemActive" : "navItem"}
                key={item.id}
                onClick={() => onNavigate(item.id)}
                type="button"
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebarPanel">
          <div className="scopeHeader">
            <LockKeyhole size={16} />
            <span>Warehouse scope</span>
          </div>
          <p>{user.role === "owner" ? "Owner access can switch between authorized warehouses." : `Your account is locked to ${selectedWarehouse.name}.`}</p>
        </div>
      </aside>

      <main className="mainPanel">
        <header className="topbar">
          <form className="searchBox" onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            onSearch(String(form.get("query") ?? ""));
          }}>
            <Search size={18} />
            <input aria-label="Search product by SKU UPC or name" name="query" placeholder="Scan UPC or search product SKU or name" />
          </form>
          <div className="topbarActions">
            <label className="warehousePicker">
              <MapPin size={16} />
              <span className="srOnly">Warehouse</span>
              <select
                aria-label="Warehouse"
                disabled={warehouses.length < 2 || user.role !== "owner"}
                value={selectedWarehouse.id}
                onChange={(event) => onWarehouseChange(event.target.value)}
              >
                {warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}
              </select>
              {warehouses.length > 1 && user.role === "owner" && <ChevronDown size={16} />}
            </label>
            {hasMoreWarehouses && <button className="textButton" disabled={loadingMoreWarehouses} onClick={onLoadMoreWarehouses} type="button">{loadingMoreWarehouses ? "Loading warehouses..." : "Load more warehouses"}</button>}
            <button className="iconButton" aria-label="Open low-stock notifications" onClick={onNotifications} type="button">
              <Bell size={18} />
              <span className="pulseDot" />
            </button>
            <div className="profileChip">
              <span>{initials(user.name)}</span>
              <div>
                <strong>{user.name}</strong>
                <small>{user.role} view</small>
              </div>
            </div>
            <button className="iconButton" aria-label="Sign out" onClick={onLogout} title="Sign out" type="button">
              <LogOut size={18} />
            </button>
          </div>
        </header>

        <section className="statusStrip" aria-label="Current warehouse metrics">
          <span>{selectedWarehouse.name} active</span>
          <span>{metrics ? metrics.availableUnits.toLocaleString() : "—"} available</span>
          <span>{metrics ? metrics.reservedUnits.toLocaleString() : "—"} reserved</span>
          <span>{metrics ? metrics.auditEvents.toLocaleString() : "—"} audit events</span>
        </section>

        <div className="contentShell">{children}</div>
      </main>
    </div>
  );
}
