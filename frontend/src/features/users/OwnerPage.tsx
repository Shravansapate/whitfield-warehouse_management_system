import { KeyRound, LoaderCircle, Save, Shield, UserPlus } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { DataState, InlineNotice } from "../../components/ui/DataState";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useCursorResource } from "../../hooks/useCursorResource";
import { ApiError, wmsApi } from "../../lib/api/client";
import type { Role, TeamMember, UserCreateInput, WarehouseRef } from "../../types/wms";
import { ProductAdmin } from "./ProductAdmin";

interface OwnerPageProps {
  onChanged: () => void;
  warehouse: WarehouseRef;
  warehouses: WarehouseRef[];
}

const initialForm: UserCreateInput = { name: "", email: "", password: "", role: "staff", warehouse_id: "" };

interface AccessDraft {
  role: Role;
  warehouseId: string;
}

export function OwnerPage({ onChanged, warehouse, warehouses }: OwnerPageProps) {
  const resource = useCursorResource((cursor) => wmsApi.getUsersPage({ cursor }), [], true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<UserCreateInput>(initialForm);
  const [accessDrafts, setAccessDrafts] = useState<Record<string, AccessDraft>>({});
  const [resetTargetId, setResetTargetId] = useState<string | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "error" } | null>(null);

  useEffect(() => {
    if (!resource.data) return;
    setAccessDrafts((current) => {
      const next = { ...current };
      for (const member of resource.data ?? []) {
        if (next[member.id]) continue;
        next[member.id] = {
          role: member.role,
          warehouseId: member.role === "owner" ? "" : member.warehouseId ?? ""
        };
      }
      return next;
    });
  }, [resource.data]);

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    const warehouseRequired = form.role !== "owner";
    if (!form.name.trim() || !form.email.trim() || form.password.length < 10 || (warehouseRequired && !form.warehouse_id)) {
      setNotice({ message: "Enter name, email, a password of at least 10 characters, and a warehouse for non-owner users.", tone: "error" });
      return;
    }
    setSubmitting("create");
    try {
      await wmsApi.createUser({ ...form, name: form.name.trim(), email: form.email.trim().toLowerCase(), warehouse_id: form.role === "owner" ? undefined : form.warehouse_id });
      setForm(initialForm);
      setShowCreate(false);
      setNotice({ message: "User created with the selected role and warehouse scope.", tone: "success" });
      setAccessDrafts({});
      resource.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The user could not be created.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const saveAccess = async (member: TeamMember) => {
    const draft = accessDrafts[member.id] ?? { role: member.role, warehouseId: member.warehouseId ?? "" };
    if (draft.role !== "owner" && !draft.warehouseId) {
      setNotice({ message: "Choose a warehouse for every non-owner user.", tone: "error" });
      return;
    }
    if (draft.role === member.role && (draft.role === "owner" || draft.warehouseId === member.warehouseId)) {
      setNotice({ message: `${member.name}'s access is already up to date.`, tone: "success" });
      return;
    }
    if (!window.confirm(`Save ${draft.role} access${draft.role === "owner" ? " across both warehouses" : " for the selected warehouse"} for ${member.name}?`)) return;
    setSubmitting(`access-${member.id}`);
    try {
      if (draft.role !== member.role) {
        await wmsApi.updateUser(member.id, draft.role === "owner"
          ? { role: draft.role }
          : { role: draft.role, warehouse_id: draft.warehouseId });
      } else if (draft.role !== "owner" && draft.warehouseId !== member.warehouseId) {
        await wmsApi.assignUserWarehouse(member.id, draft.warehouseId);
      }
      setNotice({ message: `${member.name}'s role and warehouse scope were saved.`, tone: "success" });
      setAccessDrafts({});
      resource.reload();
      onChanged();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The access change could not be completed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const handleResetPassword = async (event: FormEvent, member: TeamMember) => {
    event.preventDefault();
    if (resetPassword.length < 10) {
      setNotice({ message: "The replacement password must be at least 10 characters.", tone: "error" });
      return;
    }
    if (!window.confirm(`Reset ${member.name}'s password and sign out their active sessions?`)) return;
    setSubmitting(`password-${member.id}`);
    try {
      await wmsApi.resetUserPassword(member.id, resetPassword);
      setResetPassword("");
      setResetTargetId(null);
      setNotice({ message: `${member.name}'s password was reset and active sessions were revoked.`, tone: "success" });
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The password could not be reset.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  const toggleUser = async (id: string, active: boolean) => {
    if (!window.confirm(`${active ? "Disable" : "Enable"} this user? Audit history will be retained.`)) return;
    setSubmitting(`state-${id}`);
    try {
      await wmsApi.updateUser(id, { is_active: !active });
      setNotice({ message: `User ${active ? "disabled" : "enabled"}.`, tone: "success" });
      setAccessDrafts({});
      resource.reload();
    } catch (caught) {
      setNotice({ message: caught instanceof ApiError ? caught.message : "The user state could not be changed.", tone: "error" });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="pageStack">
      <div className="pageHeader">
        <div><p className="eyebrow">Owner controls</p><h2>Users and warehouse access</h2><p>Only owner can create users, change roles, or view both Reno and Columbus.</p></div>
        <button className="primaryButton" onClick={() => setShowCreate((visible) => !visible)} type="button"><UserPlus size={18} /> {showCreate ? "Close form" : "Add user"}</button>
      </div>

      {notice && <InlineNotice message={notice.message} tone={notice.tone} />}

      {showCreate && (
        <form className="surface formGrid userCreateForm" onSubmit={handleCreate}>
          <label><span>Full name</span><input autoFocus onChange={(event) => setForm({ ...form, name: event.target.value })} value={form.name} /></label>
          <label><span>Email</span><input inputMode="email" onChange={(event) => setForm({ ...form, email: event.target.value })} type="email" value={form.email} /></label>
          <label><span>Temporary password</span><input autoComplete="new-password" minLength={10} onChange={(event) => setForm({ ...form, password: event.target.value })} type="password" value={form.password} /></label>
          <label><span>Role</span><select onChange={(event) => setForm({ ...form, role: event.target.value as Role, warehouse_id: event.target.value === "owner" ? undefined : form.warehouse_id })} value={form.role}><option value="staff">Staff</option><option value="trusted">Trusted</option><option value="manager">Manager</option><option value="owner">Owner</option></select></label>
          {form.role !== "owner" && <label><span>Assigned warehouse</span><select onChange={(event) => setForm({ ...form, warehouse_id: event.target.value })} value={form.warehouse_id}><option value="">Choose warehouse</option>{warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}</select></label>}
          <button className="primaryButton" disabled={submitting === "create"} type="submit">{submitting === "create" && <LoaderCircle className="spin" size={18} />} Create user</button>
        </form>
      )}

      <section className="ownerGrid">
        <article className="surface accessPanel">
          <Shield size={32} />
          <h3>Cross-warehouse lock</h3>
          <p>Managers, trusted users, and staff are server-scoped to one warehouse. UI permissions mirror that boundary.</p>
          <div className="accessSplit"><span>{warehouses[0]?.name ?? "Warehouse one"}</span><strong>Owner visible</strong><span>{warehouses[1]?.name ?? "Warehouse two"}</span></div>
        </article>
        <article className="surface">
          <DataState dataLength={resource.data?.length} emptyMessage="No managed users were returned." error={resource.error} loading={resource.status === "loading"} onRetry={resource.reload}>
            <>
              <div className="recordList">
                {(resource.data ?? []).map((member) => (
                <div className="recordCard userRecord" key={member.id}>
                  <div><strong>{member.name}</strong><span>{member.email}</span><small>{member.warehouse}</small></div>
                  <div className="userAccessControls">
                    <label className="compactSelect"><span>Role for {member.name}</span><select disabled={Boolean(submitting)} onChange={(event) => {
                      const role = event.target.value as Role;
                      setAccessDrafts({ ...accessDrafts, [member.id]: {
                        role,
                        warehouseId: role === "owner" ? "" : accessDrafts[member.id]?.warehouseId || member.warehouseId || warehouses[0]?.id || ""
                      } });
                    }} value={accessDrafts[member.id]?.role ?? member.role}><option value="staff">staff</option><option value="trusted">trusted</option><option value="manager">manager</option><option value="owner">owner</option></select></label>
                    <label className="compactSelect"><span>Warehouse for {member.name}</span><select disabled={Boolean(submitting) || (accessDrafts[member.id]?.role ?? member.role) === "owner"} onChange={(event) => setAccessDrafts({ ...accessDrafts, [member.id]: { role: accessDrafts[member.id]?.role ?? member.role, warehouseId: event.target.value } })} value={accessDrafts[member.id]?.warehouseId ?? member.warehouseId ?? ""}><option value="">Choose warehouse</option>{warehouses.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label>
                    <button className="textButton" disabled={Boolean(submitting)} onClick={() => void saveAccess(member)} type="button"><Save size={15} /> Save access for {member.name}</button>
                  </div>
                  <div className="userActions">
                    <button className="stateButton" disabled={Boolean(submitting)} onClick={() => void toggleUser(member.id, member.state === "Active")} type="button"><StatusBadge label={member.state} tone={member.state === "Active" ? "green" : "red"} /></button>
                    <button className="textButton" disabled={Boolean(submitting)} onClick={() => {
                      setResetTargetId((current) => current === member.id ? null : member.id);
                      setResetPassword("");
                    }} type="button"><KeyRound size={15} /> Reset password for {member.name}</button>
                  </div>
                  {resetTargetId === member.id && (
                    <form className="userResetForm" onSubmit={(event) => void handleResetPassword(event, member)}>
                      <label><span>New password for {member.name}</span><input autoComplete="new-password" autoFocus minLength={10} onChange={(event) => setResetPassword(event.target.value)} type="password" value={resetPassword} /></label>
                      <button className="secondaryButton" disabled={submitting === `password-${member.id}`} type="submit">{submitting === `password-${member.id}` && <LoaderCircle className="spin" size={17} />} Reset and revoke sessions</button>
                    </form>
                  )}
                </div>
                ))}
              </div>
              {resource.loadMoreError && <InlineNotice message={resource.loadMoreError.message} tone="error" />}
              {resource.nextCursor && <div className="buttonRow"><button className="secondaryButton" disabled={resource.loadingMore} onClick={() => void resource.loadMore()} type="button">{resource.loadingMore && <LoaderCircle className="spin" size={17} />} Load more users</button></div>}
            </>
          </DataState>
        </article>
      </section>

      <ProductAdmin onChanged={onChanged} warehouse={warehouse} />
    </div>
  );
}
