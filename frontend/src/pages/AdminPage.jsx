import { useState, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import Check from "lucide-react/dist/esm/icons/check.mjs";
import X from "lucide-react/dist/esm/icons/x.mjs";
import Ban from "lucide-react/dist/esm/icons/ban.mjs";
import Play from "lucide-react/dist/esm/icons/play.mjs";
import KeyRound from "lucide-react/dist/esm/icons/key-round.mjs";
import Gauge from "lucide-react/dist/esm/icons/gauge.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
import Loader from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Copy from "lucide-react/dist/esm/icons/copy.mjs";
import Crown from "lucide-react/dist/esm/icons/crown.mjs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import PageHeader from "@/components/PageHeader";
import ConfirmDialog from "@/components/ConfirmDialog";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { logoutUser } from "@/hooks/useAuth";
import { translateError } from "@/utils/errorMessages";

const MB = 1024 * 1024;

function mbText(bytes) {
  if (bytes == null) return null;
  return String(Math.round(bytes / MB));
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/**
 * 多用户模式管理面板（P5，仅 admin）。
 * 待审批列表、启用/禁用、重置密码（一次性临时密码）、配额调整、删除用户。
 */
export default function AdminPage() {
  const { t } = useTranslation(["pages", "common"]);
  const authUser = useStore((s) => s.authUser);

  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");
  const loadRef = useRef(false);

  const [actionLoading, setActionLoading] = useState(false);

  // 重置密码：展示一次性临时密码
  const [tempPwd, setTempPwd] = useState(null);
  // 配额编辑
  const [quotaTarget, setQuotaTarget] = useState(null);
  const [quotaPj, setQuotaPj] = useState("");
  const [quotaDerived, setQuotaDerived] = useState("");
  // 删除确认
  const [deleteTarget, setDeleteTarget] = useState(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.admin.listUsers();
      setUsers(res.users || []);
    } catch (err) {
      setError(translateError(err.message));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (loadRef.current) return;
    loadRef.current = true;
    loadUsers();
  }, [loadUsers]);

  const run = async (fn, successMsg) => {
    setActionLoading(true);
    try {
      await fn();
      if (successMsg) toast.success(successMsg);
      await loadUsers();
    } catch (err) {
      toast.error(translateError(err.message));
    } finally {
      setActionLoading(false);
    }
  };

  const statusBadge = (status) => {
    const map = {
      pending: { label: t("pages:admin.statusPending"), cls: "" },
      active: { label: t("pages:admin.statusActive"), cls: "" },
      disabled: { label: t("pages:admin.statusDisabled"), cls: "" },
    };
    const s = map[status] || { label: status, cls: "" };
    return (
      <Badge variant={status === "active" ? "secondary" : "outline"}>
        {status === "pending" ? "⏳ " : ""}
        {s.label}
      </Badge>
    );
  };

  const filtered = users.filter(
    (u) =>
      filter === "all" ||
      (filter === "pending" && u.status === "pending") ||
      (filter === "active" && u.status === "active") ||
      (filter === "disabled" && u.status === "disabled"),
  );

  const openQuota = (u) => {
    setQuotaTarget(u);
    setQuotaPj(mbText(u.project_quota_bytes) ?? "");
    setQuotaDerived(mbText(u.derived_quota_bytes) ?? "");
  };

  const saveQuota = async () => {
    if (!quotaTarget) return;
    const pj = quotaPj.trim() === "" ? null : Number(quotaPj);
    const dv = quotaDerived.trim() === "" ? null : Number(quotaDerived);
    if (
      (pj !== null && !Number.isFinite(pj)) ||
      (dv !== null && !Number.isFinite(dv))
    ) {
      toast.error(t("pages:admin.quotaInvalid"));
      return;
    }
    setActionLoading(true);
    try {
      await api.admin.setQuota(quotaTarget.id, {
        projectQuotaBytes: pj === null ? null : Math.round(pj * MB),
        derivedQuotaBytes: dv === null ? null : Math.round(dv * MB),
      });
      toast.success(t("pages:admin.quotaSaved"));
      setQuotaTarget(null);
      await loadUsers();
    } catch (err) {
      toast.error(t("pages:admin.quotaSaveFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setActionLoading(false);
    }
  };

  const resetPassword = async (u) => {
    setActionLoading(true);
    try {
      const res = await api.admin.resetPassword(u.id);
      setTempPwd({ username: u.username, temp_password: res.temp_password });
    } catch (err) {
      toast.error(t("pages:admin.resetFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setActionLoading(false);
    }
  };

  const deleteUser = async (u) => {
    setActionLoading(true);
    try {
      await api.admin.deleteUser(u.id);
      toast.success(t("pages:admin.deleted", { username: u.username }));
      setDeleteTarget(null);
      await loadUsers();
    } catch (err) {
      toast.error(t("pages:admin.deleteFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setActionLoading(false);
    }
  };

  const isSelf = (u) => u.id === authUser?.id;

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="mx-auto max-w-5xl">
        <PageHeader
          title={t("pages:admin.title")}
          description={t("pages:admin.desc")}
          backTo="/projects"
        >
          <Button variant="outline" onClick={loadUsers} disabled={loading}>
            <RefreshCw className={loading ? "animate-spin" : ""} />
            {t("pages:admin.refresh")}
          </Button>
          <Button
            variant="ghost"
            onClick={async () => {
              await logoutUser();
            }}
          >
            {t("pages:workbench.logout")}
          </Button>
        </PageHeader>

        {error ? (
          <p className="mb-4 text-sm text-destructive">{error}</p>
        ) : null}

        {/* 筛选 */}
        <div className="mb-4 flex gap-2">
          {["all", "pending", "active", "disabled"].map((f) => (
            <Button
              key={f}
              size="sm"
              variant={filter === f ? "default" : "outline"}
              onClick={() => setFilter(f)}
            >
              {t(`pages:admin.filter.${f}`)}
            </Button>
          ))}
        </div>

        <div className="space-y-3">
          {loading && filtered.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              <Loader className="mr-2 inline size-4 animate-spin" />
              {t("pages:admin.loading")}
            </p>
          ) : filtered.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {t("pages:admin.empty")}
            </p>
          ) : (
            filtered.map((u) => (
              <div
                key={u.id}
                className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-medium">{u.username}</p>
                    {u.role === "admin" && (
                      <Badge variant="secondary">
                        <Crown className="size-3" /> Admin
                      </Badge>
                    )}
                    {statusBadge(u.status)}
                    {isSelf(u) && (
                      <Badge variant="outline">{t("pages:admin.you")}</Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {u.email || "—"} ·{" "}
                    {t("pages:admin.createdAt", {
                      date: formatDate(u.created_at),
                    })}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {t("pages:admin.quotaLabel")}:{" "}
                    {u.project_quota_bytes != null
                      ? mbText(u.project_quota_bytes) + " MB"
                      : "—"}
                    {" / "}
                    {u.derived_quota_bytes != null
                      ? mbText(u.derived_quota_bytes) + " MB"
                      : "—"}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  {u.status === "pending" && (
                    <>
                      <Button
                        size="sm"
                        onClick={() =>
                          run(
                            () => api.admin.approve(u.id),
                            t("pages:admin.approved", { username: u.username }),
                          )
                        }
                        disabled={actionLoading}
                      >
                        <Check /> {t("pages:admin.approve")}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          run(
                            () => api.admin.reject(u.id),
                            t("pages:admin.rejected", { username: u.username }),
                          )
                        }
                        disabled={actionLoading}
                      >
                        <X /> {t("pages:admin.reject")}
                      </Button>
                    </>
                  )}
                  {u.status === "active" && !isSelf(u) && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        run(
                          () => api.admin.disable(u.id),
                          t("pages:admin.disabled", { username: u.username }),
                        )
                      }
                      disabled={actionLoading}
                    >
                      <Ban /> {t("pages:admin.disable")}
                    </Button>
                  )}
                  {u.status === "disabled" && (
                    <Button
                      size="sm"
                      onClick={() =>
                        run(
                          () => api.admin.enable(u.id),
                          t("pages:admin.enabled", { username: u.username }),
                        )
                      }
                      disabled={actionLoading}
                    >
                      <Play /> {t("pages:admin.enable")}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => resetPassword(u)}
                    disabled={actionLoading}
                  >
                    <KeyRound /> {t("pages:admin.resetPassword")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => openQuota(u)}
                    disabled={actionLoading}
                  >
                    <Gauge /> {t("pages:admin.quota")}
                  </Button>
                  {!isSelf(u) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive"
                      onClick={() => setDeleteTarget(u)}
                      disabled={actionLoading}
                    >
                      <Trash2 /> {t("pages:admin.delete")}
                    </Button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* 临时密码展示 */}
        <Dialog open={!!tempPwd} onOpenChange={(o) => !o && setTempPwd(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("pages:admin.tempPwdTitle")}</DialogTitle>
              <DialogDescription>
                {t("pages:admin.tempPwdDesc", {
                  username: tempPwd?.username || "",
                })}
              </DialogDescription>
            </DialogHeader>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded-md bg-muted px-3 py-2 font-mono text-sm">
                {tempPwd?.temp_password || ""}
              </code>
              <Button
                variant="outline"
                onClick={() => {
                  if (tempPwd?.temp_password) {
                    navigator.clipboard?.writeText(tempPwd.temp_password);
                    toast.success(t("pages:admin.copied"));
                  }
                }}
              >
                <Copy />
              </Button>
            </div>
            <DialogFooter>
              <Button onClick={() => setTempPwd(null)}>
                {t("pages:admin.close")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 配额编辑 */}
        <Dialog
          open={!!quotaTarget}
          onOpenChange={(o) => !o && setQuotaTarget(null)}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("pages:admin.quotaTitle")}</DialogTitle>
              <DialogDescription>
                {t("pages:admin.quotaDesc", {
                  username: quotaTarget?.username || "",
                })}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>{t("pages:admin.quotaProjectMb")}</Label>
                <Input
                  type="number"
                  min="0"
                  value={quotaPj}
                  onChange={(e) => setQuotaPj(e.target.value)}
                  placeholder={t("pages:admin.quotaPlaceholder")}
                />
              </div>
              <div className="space-y-1.5">
                <Label>{t("pages:admin.quotaDerivedMb")}</Label>
                <Input
                  type="number"
                  min="0"
                  value={quotaDerived}
                  onChange={(e) => setQuotaDerived(e.target.value)}
                  placeholder={t("pages:admin.quotaPlaceholder")}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {t("pages:admin.quotaHint")}
              </p>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setQuotaTarget(null)}>
                {t("pages:admin.close")}
              </Button>
              <Button onClick={saveQuota} disabled={actionLoading}>
                {actionLoading
                  ? t("pages:admin.saving")
                  : t("pages:admin.saveQuota")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除用户 */}
        <ConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(o) => !o && setDeleteTarget(null)}
          title={t("pages:admin.deleteTitle")}
          description={t("pages:admin.deleteDesc", {
            username: deleteTarget?.username || "",
          })}
          confirmText={t("pages:admin.delete")}
          variant="destructive"
          onConfirm={() => deleteUser(deleteTarget)}
          confirmLoading={actionLoading}
        />
      </div>
    </div>
  );
}
