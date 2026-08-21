import { memo, useCallback, useEffect, useRef, useState } from "react";
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
import { translateError } from "@/utils/errorMessages";
import { formatDate } from "@/utils/format";

const MB = 1024 * 1024;

// 状态→i18n key 映射提升为模块级常量：每行渲染不再重建 3 键对象（审查 L4，
// 原实现还有从未被读取的 cls 死字段，一并移除）
const STATUS_LABEL_KEYS = {
  pending: "pages:admin.statusPending",
  active: "pages:admin.statusActive",
  disabled: "pages:admin.statusDisabled",
};

function mbText(bytes) {
  if (bytes == null) return null;
  const mb = bytes / MB;
  // 非整 MB 时保留小数（去尾零），避免 Math.round 把 0.5 MiB 回显成 "1"，
  // 管理员不改输入直接保存时配额被静默翻倍
  return String(Number.isInteger(mb) ? mb : Number(mb.toFixed(3)));
}

/**
 * 用户行（memo 化）：props 全部为原始值/稳定回调，父级状态变化（busy、筛选、加载）
 * 时只有目标行重渲染；busy 按行独立传递，单行操作不再带动全列表重建。
 */
const AdminUserRow = memo(function AdminUserRow({
  user: u,
  isCurrentUser,
  busy,
  onApprove,
  onReject,
  onDisable,
  onEnable,
  onResetPwd,
  onOpenQuota,
  onDelete,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const statusBadge = (status) => {
    const labelKey = STATUS_LABEL_KEYS[status];
    return (
      <Badge variant={status === "active" ? "secondary" : "outline"}>
        {status === "pending" ? "⏳ " : ""}
        {labelKey ? t(labelKey) : status}
      </Badge>
    );
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-medium">{u.username}</p>
          {u.role === "admin" && (
            <Badge variant="secondary">
              <Crown className="size-3" /> {t("common:role.admin")}
            </Badge>
          )}
          {statusBadge(u.status)}
          {isCurrentUser && (
            <Badge variant="outline">{t("pages:admin.you")}</Badge>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {u.email || "—"} ·{" "}
          {t("pages:admin.createdAt", { date: formatDate(u.created_at) })}
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
            <Button size="sm" onClick={() => onApprove(u)} disabled={busy}>
              <Check /> {t("pages:admin.approve")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onReject(u)}
              disabled={busy}
            >
              <X /> {t("pages:admin.reject")}
            </Button>
          </>
        )}
        {u.status === "active" && !isCurrentUser && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onDisable(u)}
            disabled={busy}
          >
            <Ban /> {t("pages:admin.disable")}
          </Button>
        )}
        {u.status === "disabled" && (
          <Button size="sm" onClick={() => onEnable(u)} disabled={busy}>
            <Play /> {t("pages:admin.enable")}
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={() => onResetPwd(u)}
          disabled={busy}
        >
          <KeyRound /> {t("pages:admin.resetPassword")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onOpenQuota(u)}
          disabled={busy}
        >
          <Gauge /> {t("pages:admin.quota")}
        </Button>
        {!isCurrentUser && (
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive"
            onClick={() => onDelete(u)}
            disabled={busy}
          >
            <Trash2 /> {t("pages:admin.delete")}
          </Button>
        )}
      </div>
    </div>
  );
});

/**
 * 多用户模式管理面板（P5，仅 admin）。
 * 待审批列表、启用/禁用、重置密码（两步式：先生成临时密码预览，
 * 管理员确认后才写入）、配额调整、删除用户。
 */
export default function AdminPage() {
  const { t } = useTranslation(["pages", "common"]);
  const authUser = useStore((s) => s.authUser);

  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");
  const loadRef = useRef(false);

  // 行级操作状态：记录当前操作的目标用户 id，仅目标行进入 busy（统计类操作时
  // 只重渲染目标行；对话框类操作用 busyUserId !== null 表示进行中）
  const [busyUserId, setBusyUserId] = useState(null);

  // 重置密码：两步式——先展示生成的临时密码（尚未生效），确认后才写入
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

  const run = useCallback(
    async (fn, successMsg, userId) => {
      setBusyUserId(userId);
      try {
        await fn();
        if (successMsg) toast.success(successMsg);
        await loadUsers();
      } catch (err) {
        toast.error(translateError(err.message));
      } finally {
        setBusyUserId(null);
      }
    },
    [loadUsers],
  );

  // 行级动作回调：稳定引用（仅依赖 run / t），配合 AdminUserRow memo 生效
  const handleApprove = useCallback(
    (u) =>
      run(
        () => api.admin.approve(u.id),
        t("pages:admin.approved", { username: u.username }),
        u.id,
      ),
    [run, t],
  );
  const handleReject = useCallback(
    (u) =>
      run(
        () => api.admin.reject(u.id),
        t("pages:admin.rejected", { username: u.username }),
        u.id,
      ),
    [run, t],
  );
  const handleDisable = useCallback(
    (u) =>
      run(
        () => api.admin.disable(u.id),
        t("pages:admin.disabled", { username: u.username }),
        u.id,
      ),
    [run, t],
  );
  const handleEnable = useCallback(
    (u) =>
      run(
        () => api.admin.enable(u.id),
        t("pages:admin.enabled", { username: u.username }),
        u.id,
      ),
    [run, t],
  );

  const filtered = users.filter(
    (u) =>
      filter === "all" ||
      (filter === "pending" && u.status === "pending") ||
      (filter === "active" && u.status === "active") ||
      (filter === "disabled" && u.status === "disabled"),
  );

  const openQuota = useCallback((u) => {
    setQuotaTarget(u);
    setQuotaPj(mbText(u.project_quota_bytes) ?? "");
    setQuotaDerived(mbText(u.derived_quota_bytes) ?? "");
  }, []);

  // 配额输入未改动时透传原始字节数：即使回显有舍入，保存也不会漂移
  const quotaBytes = (text, originalBytes) => {
    if (text.trim() === "") return null;
    const n = Number(text);
    if (!Number.isFinite(n)) return NaN;
    return text === (mbText(originalBytes) ?? "")
      ? originalBytes
      : Math.round(n * MB);
  };

  const saveQuota = async () => {
    if (!quotaTarget) return;
    const pj = quotaBytes(quotaPj, quotaTarget.project_quota_bytes);
    const dv = quotaBytes(quotaDerived, quotaTarget.derived_quota_bytes);
    if (Number.isNaN(pj) || Number.isNaN(dv)) {
      toast.error(t("pages:admin.quotaInvalid"));
      return;
    }
    setBusyUserId(quotaTarget.id);
    try {
      await api.admin.setQuota(quotaTarget.id, {
        projectQuotaBytes: pj,
        derivedQuotaBytes: dv,
      });
      toast.success(t("pages:admin.quotaSaved"));
      setQuotaTarget(null);
      await loadUsers();
    } catch (err) {
      toast.error(t("pages:admin.quotaSaveFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyUserId(null);
    }
  };

  // 第一步：仅生成并预览临时密码，不修改用户密码
  const resetPassword = useCallback(
    async (u) => {
      setBusyUserId(u.id);
      try {
        const res = await api.admin.resetPasswordPreview(u.id);
        setTempPwd({
          id: u.id,
          username: u.username,
          temp_password: res.temp_password,
        });
      } catch (err) {
        toast.error(t("pages:admin.resetFailed"), {
          description: translateError(err.message),
        });
      } finally {
        setBusyUserId(null);
      }
    },
    [t],
  );

  // 第二步：管理员确认后，才将用户密码写入为该临时密码
  const confirmResetPassword = async () => {
    if (!tempPwd) return;
    setBusyUserId(tempPwd.id);
    try {
      await api.admin.resetPasswordConfirm(tempPwd.id, tempPwd.temp_password);
      toast.success(
        t("pages:admin.resetSuccess", { username: tempPwd.username }),
      );
      setTempPwd(null);
    } catch (err) {
      toast.error(t("pages:admin.resetFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyUserId(null);
    }
  };

  const deleteUser = useCallback(
    async (u) => {
      setBusyUserId(u.id);
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
        setBusyUserId(null);
      }
    },
    [t, loadUsers],
  );

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
          {/* 退出登录入口统一收在右上角工具栏图标（带悬浮提示），页头不再重复 */}
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
              <AdminUserRow
                key={u.id}
                user={u}
                isCurrentUser={isSelf(u)}
                busy={busyUserId === u.id}
                onApprove={handleApprove}
                onReject={handleReject}
                onDisable={handleDisable}
                onEnable={handleEnable}
                onResetPwd={resetPassword}
                onOpenQuota={openQuota}
                onDelete={setDeleteTarget}
              />
            ))
          )}
        </div>

        {/* 临时密码确认（两步式）：确认前用户密码未被修改 */}
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
            <p className="text-xs text-muted-foreground">
              {t("pages:admin.tempPwdHint")}
            </p>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setTempPwd(null)}
                disabled={busyUserId !== null}
              >
                {t("common:cancel")}
              </Button>
              <Button
                onClick={confirmResetPassword}
                disabled={busyUserId !== null}
              >
                {busyUserId !== null
                  ? t("pages:admin.resetting")
                  : t("pages:admin.confirmReset")}
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
              <Button onClick={saveQuota} disabled={busyUserId !== null}>
                {busyUserId !== null
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
          confirmLoading={busyUserId !== null}
        />
      </div>
    </div>
  );
}
