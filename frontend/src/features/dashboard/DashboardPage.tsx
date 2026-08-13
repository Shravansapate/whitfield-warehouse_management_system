import { AlertTriangle, Boxes, ClipboardList, LoaderCircle, PackageCheck } from "lucide-react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatCard } from "../../components/ui/StatCard";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCursorResource } from "../../hooks/useCursorResource";
import type { ApiError } from "../../lib/api/client";
import { wmsApi } from "../../lib/api/client";
import type { AsyncStatus, DashboardMetrics, NavigationId, WarehouseRef } from "../../types/wms";

interface DashboardPageProps {
  combined?: boolean;
  error: ApiError | null;
  metrics: DashboardMetrics | null;
  onNavigate: (page: NavigationId) => void;
  onRetry: () => void;
  refreshVersion: number;
  status: AsyncStatus;
  warehouse: WarehouseRef;
}

export function DashboardPage({ combined = false, error, metrics, onNavigate, onRetry, refreshVersion, status, warehouse }: DashboardPageProps) {
  const lowStock = useCursorResource(
    (cursor) => wmsApi.getLowStockPage(warehouse.id, { cursor }),
    [warehouse.id, refreshVersion],
    !combined
  );
  const totalTracked = (metrics?.availableUnits ?? 0) + (metrics?.reservedUnits ?? 0);
  const sellablePercent = totalTracked > 0 ? Math.round(((metrics?.availableUnits ?? 0) / totalTracked) * 100) : 0;

  return (
    <DataState error={error} loading={status === "loading" && !metrics} onRetry={onRetry}>
      <div className="pageStack">
        <section className="heroGrid">
          <div className="heroPanel">
            <p className="eyebrow">Live warehouse command</p>
            <h2>{combined ? "Reno and Columbus" : warehouse.name} can receive, reserve, pack, label, and audit without Excel.</h2>
            <p>
              The client rules stay visible: no cross-warehouse fallback, damaged goods stay out of sellable stock,
              and every stock move has history.
            </p>
            <div className="heroActions">
              <button className="primaryButton" onClick={() => onNavigate("receiving")} type="button">Open receiving</button>
              <button className="secondaryButton" onClick={() => onNavigate("orders")} type="button">Open picking queue</button>
            </div>
          </div>
          <div className="dockPanel">
            <div className="radarDial">
              <span>{sellablePercent}%</span>
              <small>sellable</small>
            </div>
            <div className="laneList">
              <div><span>Receiving</span><strong>{metrics?.receivingBacklog ?? 0}</strong></div>
              <div><span>To ship</span><strong>{metrics?.ordersToShip ?? 0}</strong></div>
              <div><span>Damaged return</span><strong>{metrics?.damagedReturns ?? 0}</strong></div>
            </div>
          </div>
        </section>

        <section className="statGrid">
          <StatCard icon={Boxes} label="Available units" value={(metrics?.availableUnits ?? 0).toLocaleString()} detail="On hand minus reserved" tone="mint" />
          <StatCard icon={PackageCheck} label="Orders to ship" value={String(metrics?.ordersToShip ?? 0)} detail="Packed or label ready" tone="ink" />
          <StatCard icon={ClipboardList} label="Receiving backlog" value={String(metrics?.receivingBacklog ?? 0)} detail="Open or in progress" tone="amber" />
          <StatCard icon={AlertTriangle} label="Low stock SKUs" value={combined ? "\u2014" : `${lowStock.data?.length ?? 0}${lowStock.nextCursor ? "+" : ""}`} detail={combined ? "Select a warehouse for thresholds" : "Below warehouse threshold"} tone="rose" />
        </section>

        <section className="splitGrid">
          <article className="surface">
            <div className="sectionHeader">
              <div>
                <p className="eyebrow">Reservation safety</p>
                <h3>Allocation pipeline</h3>
              </div>
              <StatusBadge label="atomic" tone="green" />
            </div>
            {["Order created", "All lines reserved", "Picking started", "Package measured", "Label created", "Shipped"].map((step, index) => (
              <div className="timelineRow" key={step}>
                <span>{index + 1}</span>
                <p>{step}</p>
              </div>
            ))}
          </article>

          <article className="surface">
            <div className="sectionHeader">
              <div>
                <p className="eyebrow">Needs attention</p>
                <h3>Low-stock watch</h3>
              </div>
              <StatusBadge label="read only" tone="blue" />
            </div>
            {combined ? (
              <div className="statePanel" role="status"><strong>Select Reno or Columbus to view warehouse-specific stock thresholds.</strong></div>
            ) : (
              <DataState
                dataLength={lowStock.data?.length}
                emptyMessage="All products are above their warehouse threshold."
                error={lowStock.error}
                loading={lowStock.status === "loading"}
                onRetry={lowStock.reload}
              >
                <>
                  {(lowStock.data ?? []).map((item) => (
                    <div className="inventorySignal" key={item.productId}>
                      <div>
                        <strong>{item.name}</strong>
                        <small>{item.sku}</small>
                      </div>
                      <span>{item.available} available</span>
                    </div>
                  ))}
                  {lowStock.loadMoreError && <InlineNotice message={lowStock.loadMoreError.message} tone="error" />}
                  {lowStock.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={lowStock.loadingMore} onClick={() => void lowStock.loadMore()} type="button">{lowStock.loadingMore && <LoaderCircle className="spin" size={17} />} Load more low-stock products</button></div>}
                </>
              </DataState>
            )}
          </article>
        </section>
      </div>
    </DataState>
  );
}
