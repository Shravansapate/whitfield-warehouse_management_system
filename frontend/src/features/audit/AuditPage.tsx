import { Activity, LoaderCircle, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCursorResource } from "../../hooks/useCursorResource";
import { wmsApi } from "../../lib/api/client";
import type { AuditRow, WarehouseRef } from "../../types/wms";

interface AuditPageProps {
  refreshVersion: number;
  warehouse: WarehouseRef;
}

const toneBySource: Record<AuditRow["source"], "blue" | "green" | "amber" | "gray"> = {
  web: "blue",
  scanner: "green",
  voice: "amber",
  automation: "gray",
  api: "blue",
  system: "gray"
};

function formatTime(value: string) {
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

export function AuditPage({ refreshVersion, warehouse }: AuditPageProps) {
  const resource = useCursorResource(
    (cursor) => wmsApi.getAuditLogsPage(warehouse.id, { cursor }),
    [warehouse.id, refreshVersion]
  );
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return resource.data ?? [];
    return (resource.data ?? []).filter((entry) => [entry.actor, entry.action, entry.target, entry.source, entry.reason ?? ""].some((value) => value.toLowerCase().includes(normalized)));
  }, [query, resource.data]);

  return (
    <div className="pageStack">
      <div className="pageHeader">
        <div>
          <p className="eyebrow">Append-only history</p>
          <h2>Audit trail</h2>
          <p>Each stock-changing workflow records actor, source, target, reason, and time in the same transaction.</p>
        </div>
      </div>

      <section className="surface auditSurface">
        <label className="filterInput"><Search size={17} /><span className="srOnly">Filter audit history</span><input onChange={(event) => setQuery(event.target.value)} placeholder="Filter actor, action, source, or record" value={query} /></label>
        <DataState dataLength={filtered.length + (resource.nextCursor ? 1 : 0)} emptyMessage={query ? "No audit events match this filter." : "No audit events exist for this warehouse yet."} error={resource.error} loading={resource.status === "loading"} onRetry={resource.reload}>
          <>
            {filtered.map((entry) => (
              <div className="auditRow" key={entry.id}>
                <div className="auditIcon"><Activity size={18} /></div>
                <div><strong>{entry.action}</strong><span>{entry.actor} changed {entry.target}</span>{entry.reason && <small>{entry.reason}</small>}</div>
                <StatusBadge label={entry.source} tone={toneBySource[entry.source] ?? "gray"} />
                <time dateTime={entry.time}>{formatTime(entry.time)}</time>
              </div>
            ))}
            {resource.loadMoreError && <InlineNotice message={resource.loadMoreError.message} tone="error" />}
            {resource.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={resource.loadingMore} onClick={() => void resource.loadMore()} type="button">{resource.loadingMore && <LoaderCircle className="spin" size={17} />} Load more audit events</button></div>}
          </>
        </DataState>
      </section>
    </div>
  );
}
