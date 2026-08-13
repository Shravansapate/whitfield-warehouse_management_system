import { AlertCircle, Inbox, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { ApiError } from "../../lib/api/client";

interface DataStateProps {
  children: ReactNode;
  dataLength?: number;
  emptyMessage?: string;
  error: ApiError | null;
  loading: boolean;
  onRetry: () => void;
}

export function DataState({ children, dataLength, emptyMessage = "No records found for this warehouse.", error, loading, onRetry }: DataStateProps) {
  if (loading) {
    return (
      <div className="statePanel" role="status">
        <LoaderCircle className="spin" size={24} />
        <strong>Loading live warehouse data…</strong>
      </div>
    );
  }
  if (error) {
    return (
      <div className="statePanel statePanelError" role="alert">
        <AlertCircle size={24} />
        <div>
          <strong>{error.status === 403 ? "Access restricted" : "Unable to load this view"}</strong>
          <span>{error.message}</span>
          {error.requestId && <small>Request ID: {error.requestId}</small>}
        </div>
        <button className="secondaryButton" onClick={onRetry} type="button">
          <RefreshCw size={16} /> Retry
        </button>
      </div>
    );
  }
  if (dataLength === 0) {
    return (
      <div className="statePanel" role="status">
        <Inbox size={24} />
        <strong>{emptyMessage}</strong>
      </div>
    );
  }
  return <>{children}</>;
}

interface NoticeProps {
  message: string;
  tone?: "success" | "error" | "info";
}

export function InlineNotice({ message, tone = "info" }: NoticeProps) {
  return <div className={`inlineNotice inlineNotice-${tone}`} role={tone === "error" ? "alert" : "status"}>{message}</div>;
}
