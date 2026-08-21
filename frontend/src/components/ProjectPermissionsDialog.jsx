import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import UserPlus from "lucide-react/dist/esm/icons/user-plus.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import Crown from "lucide-react/dist/esm/icons/crown.mjs";
import Send from "lucide-react/dist/esm/icons/send.mjs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import ConfirmDialog from "@/components/ConfirmDialog";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";

/**
 * 协作者行（memo 化，审查 L1）：props 为原始值/稳定回调，父级状态变化
 * （输入框、其他行忙态）时只有目标行重渲染。
 */
const CollaboratorRow = memo(function CollaboratorRow({
  collab,
  busy,
  onRoleChange,
  onRemoveRequest,
}) {
  const { t } = useTranslation(["components", "common"]);
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{collab.username}</p>
        <p className="truncate text-xs text-muted-foreground">
          {collab.email || "—"}
        </p>
      </div>
      <Select
        value={collab.role}
        onValueChange={(v) => onRoleChange(collab.user_id, v)}
        disabled={busy}
      >
        <SelectTrigger size="sm" className="w-24">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="editor">{t("common:role.editor")}</SelectItem>
          <SelectItem value="viewer">{t("common:role.viewer")}</SelectItem>
        </SelectContent>
      </Select>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("permissions.remove")}
        onClick={() => onRemoveRequest(collab)}
        disabled={busy}
      >
        <Trash2 className="size-4" />
      </Button>
    </div>
  );
});

/**
 * 项目权限对话框（P5，owner 专属）。
 * 展示 owner 与协作者，支持按用户名添加、改角色、移除、转交 owner。
 */
export default function ProjectPermissionsDialog({
  open,
  onOpenChange,
  project,
  onChanged,
}) {
  const { t } = useTranslation(["components", "common"]);
  const projectId = project?.project_id;
  const projectName = project?.name || "";

  const [data, setData] = useState(null); // { owner, collaborators }
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [newUsername, setNewUsername] = useState("");
  const [newRole, setNewRole] = useState("editor");
  const [addLoading, setAddLoading] = useState(false);

  const [transferUsername, setTransferUsername] = useState("");
  const [keepAccess, setKeepAccess] = useState(false);
  const [transferLoading, setTransferLoading] = useState(false);
  const [transferConfirmOpen, setTransferConfirmOpen] = useState(false);

  // 行级忙态：改角色 / 移除请求进行中，仅目标行禁用（审查 L2）
  const [busyUserId, setBusyUserId] = useState(null);
  // 待二次确认的移除目标（审查 L12：与转交、管理面板删除用户行为一致）
  const [removeTarget, setRemoveTarget] = useState(null);

  // 请求序号守卫：快速"打开 A → 关闭 → 打开 B"时，在途的旧响应不得覆盖新数据；
  // 组件卸载/重开时递增序号使旧请求作废（同时消除 StrictMode 下开发期双请求
  // 中第一个请求写入状态的竞态——首个 effect 的 cleanup 即作废它）。
  const loadSeqRef = useRef(0);

  const load = useCallback(async () => {
    if (!projectId) return;
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.projects.permissions.list(projectId);
      if (seq !== loadSeqRef.current) return; // 已有更新的请求/已关闭
      setData(res);
    } catch (err) {
      if (seq !== loadSeqRef.current) return;
      setError(translateError(err.message));
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (open && projectId) {
      setNewUsername("");
      setTransferUsername("");
      setKeepAccess(false);
      setRemoveTarget(null);
      load();
    }
    return () => {
      // 关闭/切换项目：作废在途请求
      loadSeqRef.current++;
    };
  }, [open, projectId, load]);

  const roleLabel = useMemo(
    () => ({
      owner: t("common:role.owner"),
      editor: t("common:role.editor"),
      viewer: t("common:role.viewer"),
    }),
    [t],
  );

  const handleAdd = async () => {
    if (!newUsername.trim()) return;
    setAddLoading(true);
    try {
      const res = await api.projects.permissions.add(
        projectId,
        newUsername.trim(),
        newRole,
      );
      // 后端返回完整协作者视图：本地合并即可，无需全量重拉（审查 L2）
      if (res?.collaborator) {
        setData((prev) =>
          prev
            ? {
                ...prev,
                collaborators: [...prev.collaborators, res.collaborator],
              }
            : prev,
        );
      } else {
        await load(); // 兜底：响应结构变化时不丢数据
      }
      toast.success(t("permissions.added"));
      setNewUsername("");
      onChanged?.();
    } catch (err) {
      toast.error(t("permissions.addFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setAddLoading(false);
    }
  };

  const handleRoleChange = useCallback(
    async (collabUserId, role) => {
      setBusyUserId(collabUserId);
      try {
        await api.projects.permissions.update(projectId, collabUserId, role);
        // 服务端确认后才更新受控 value（无跳变），本地合并不再全量重拉（审查 L2）
        setData((prev) =>
          prev
            ? {
                ...prev,
                collaborators: prev.collaborators.map((c) =>
                  c.user_id === collabUserId ? { ...c, role } : c,
                ),
              }
            : prev,
        );
        onChanged?.();
      } catch (err) {
        toast.error(t("permissions.updateFailed"), {
          description: translateError(err.message),
        });
      } finally {
        setBusyUserId(null);
      }
    },
    [projectId, t, onChanged],
  );

  /** 二次确认后的真正移除（审查 L12）：成功后本地过滤，不再全量重拉 */
  const handleRemoveConfirm = useCallback(async () => {
    if (!removeTarget) return;
    const target = removeTarget;
    setBusyUserId(target.user_id);
    try {
      await api.projects.permissions.remove(projectId, target.user_id);
      setData((prev) =>
        prev
          ? {
              ...prev,
              collaborators: prev.collaborators.filter(
                (c) => c.user_id !== target.user_id,
              ),
            }
          : prev,
      );
      toast.success(t("permissions.removed"));
      setRemoveTarget(null);
      onChanged?.();
    } catch (err) {
      toast.error(t("permissions.removeFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyUserId(null);
    }
  }, [removeTarget, projectId, t, onChanged]);

  const handleTransfer = async () => {
    if (!transferUsername.trim()) return;
    setTransferLoading(true);
    try {
      await api.projects.transfer(
        projectId,
        transferUsername.trim(),
        keepAccess,
      );
      toast.success(t("permissions.transferred"));
      await load();
      onChanged?.();
      setTransferConfirmOpen(false);
    } catch (err) {
      toast.error(t("permissions.transferFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setTransferLoading(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-lg max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>{t("permissions.title")}</DialogTitle>
            <DialogDescription>
              {t("permissions.desc", { name: projectName })}
            </DialogDescription>
          </DialogHeader>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
            {error ? (
              <p className="text-sm text-destructive">{error}</p>
            ) : loading && !data ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                {t("permissions.loading")}
              </p>
            ) : data ? (
              <>
                {/* owner */}
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">
                    {t("permissions.ownerLabel")}
                  </Label>
                  <div className="flex items-center justify-between rounded-md border px-3 py-2">
                    <span className="inline-flex items-center gap-2">
                      <Crown className="size-4 text-amber-500" />
                      {data.owner?.username}
                    </span>
                    <Badge variant="secondary">{roleLabel.owner}</Badge>
                  </div>
                </div>

                {/* collaborators */}
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">
                    {t("permissions.collaboratorsLabel")}
                  </Label>
                  {data.collaborators.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("permissions.empty")}
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {data.collaborators.map((c) => (
                        <CollaboratorRow
                          key={c.user_id}
                          collab={c}
                          busy={busyUserId === c.user_id}
                          onRoleChange={handleRoleChange}
                          onRemoveRequest={setRemoveTarget}
                        />
                      ))}
                    </div>
                  )}
                </div>

                {/* 添加协作者 */}
                <div className="space-y-2 rounded-md border bg-muted/30 p-3">
                  <Label className="text-xs">{t("permissions.addTitle")}</Label>
                  <div className="flex gap-2">
                    <Input
                      value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      placeholder={t("permissions.usernamePlaceholder")}
                      onKeyDown={(e) => e.key === "Enter" && handleAdd()}
                    />
                    <Select value={newRole} onValueChange={setNewRole}>
                      <SelectTrigger className="w-28">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="editor">
                          {t("common:role.editor")}
                        </SelectItem>
                        <SelectItem value="viewer">
                          {t("common:role.viewer")}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                    <Button
                      onClick={handleAdd}
                      disabled={addLoading || !newUsername.trim()}
                    >
                      <UserPlus />
                    </Button>
                  </div>
                </div>

                {/* 转交 owner */}
                <div className="space-y-2 rounded-md border bg-muted/30 p-3">
                  <Label className="text-xs">
                    {t("permissions.transferTitle")}
                  </Label>
                  <div className="flex gap-2">
                    <Input
                      value={transferUsername}
                      onChange={(e) => setTransferUsername(e.target.value)}
                      placeholder={t("permissions.transferPlaceholder")}
                    />
                    <Button
                      variant="outline"
                      onClick={() => setTransferConfirmOpen(true)}
                      disabled={!transferUsername.trim()}
                    >
                      <Send /> {t("permissions.transfer")}
                    </Button>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={keepAccess}
                      onChange={(e) => setKeepAccess(e.target.checked)}
                    />
                    {t("permissions.transferKeepAccess")}
                  </label>
                </div>
              </>
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("permissions.close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={transferConfirmOpen}
        onOpenChange={setTransferConfirmOpen}
        title={t("permissions.transferConfirmTitle")}
        description={t("permissions.transferConfirmDesc", {
          username: transferUsername.trim(),
        })}
        confirmText={t("permissions.transfer")}
        onConfirm={handleTransfer}
        confirmLoading={transferLoading}
      />

      {/* 移除协作者二次确认（审查 L12）：与转交、管理面板删除用户行为一致 */}
      <ConfirmDialog
        open={!!removeTarget}
        onOpenChange={(o) => !o && setRemoveTarget(null)}
        title={t("permissions.removeConfirmTitle")}
        description={t("permissions.removeConfirmDesc", {
          name: removeTarget?.username || "",
        })}
        confirmText={t("permissions.remove")}
        variant="destructive"
        onConfirm={handleRemoveConfirm}
        confirmLoading={busyUserId !== null}
      />
    </>
  );
}
