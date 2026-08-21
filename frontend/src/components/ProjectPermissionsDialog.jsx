import { useEffect, useMemo, useState } from "react";
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

  const load = async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.projects.permissions.list(projectId);
      setData(res);
    } catch (err) {
      setError(translateError(err.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open && projectId) {
      setNewUsername("");
      setTransferUsername("");
      setKeepAccess(false);
      load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId]);

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
      await api.projects.permissions.add(
        projectId,
        newUsername.trim(),
        newRole,
      );
      toast.success(t("permissions.added"));
      setNewUsername("");
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(t("permissions.addFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setAddLoading(false);
    }
  };

  const handleRoleChange = async (collabUserId, role) => {
    try {
      await api.projects.permissions.update(projectId, collabUserId, role);
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(t("permissions.updateFailed"), {
        description: translateError(err.message),
      });
    }
  };

  const handleRemove = async (collabUserId) => {
    try {
      await api.projects.permissions.remove(projectId, collabUserId);
      toast.success(t("permissions.removed"));
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(t("permissions.removeFailed"), {
        description: translateError(err.message),
      });
    }
  };

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
                        <div
                          key={c.user_id}
                          className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium">
                              {c.username}
                            </p>
                            <p className="truncate text-xs text-muted-foreground">
                              {c.email || "—"}
                            </p>
                          </div>
                          <Select
                            value={c.role}
                            onValueChange={(v) =>
                              handleRoleChange(c.user_id, v)
                            }
                          >
                            <SelectTrigger size="sm" className="w-24">
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
                            variant="ghost"
                            size="icon"
                            aria-label={t("permissions.remove")}
                            onClick={() => handleRemove(c.user_id)}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
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
    </>
  );
}
