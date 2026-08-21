import { useState, useCallback, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import UploadCloud from "lucide-react/dist/esm/icons/upload-cloud.mjs";
import Download from "lucide-react/dist/esm/icons/download.mjs";
import Pencil from "lucide-react/dist/esm/icons/pencil.mjs";
import ShieldCheck from "lucide-react/dist/esm/icons/shield-check.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import RotateCcw from "lucide-react/dist/esm/icons/rotate-ccw.mjs";
import ArchiveRestore from "lucide-react/dist/esm/icons/archive-restore.mjs";
import LogOut from "lucide-react/dist/esm/icons/log-out.mjs";
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
import Loader from "lucide-react/dist/esm/icons/loader-circle.mjs";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import PageHeader from "@/components/PageHeader";
import ConfirmDialog from "@/components/ConfirmDialog";
import UploadProjectDialog from "@/components/UploadProjectDialog";
import ProjectPermissionsDialog from "@/components/ProjectPermissionsDialog";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { logoutUser } from "@/hooks/useAuth";
import { translateError } from "@/utils/errorMessages";

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(n) / Math.log(1024)),
    units.length - 1,
  );
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/**
 * 多用户模式项目工作台（P5）。
 * 项目列表（我的 / 共享给我的，展示 owner 与 role）、上传入口、配额用量、回收站入口、
 * 打开/下载/重命名/权限/转交/删除、管理入口。
 */
export default function ProjectWorkbenchPage() {
  const { t } = useTranslation(["pages", "common"]);
  const navigate = useNavigate();

  const authUser = useStore((s) => s.authUser);
  const quota = useStore((s) => s.quota);
  const usage = useStore((s) => s.usage);
  const setOpenedProject = useStore((s) => s.setOpenedProject);
  const clearProject = useStore((s) => s.clearProject);
  const workspaceId = useStore((s) => s.workspaceId);

  const [tab, setTab] = useState("mine");
  const [projects, setProjects] = useState([]);
  const [recycle, setRecycle] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [permsProject, setPermsProject] = useState(null);
  const [renameProject, setRenameProject] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteProject, setDeleteProject] = useState(null);
  const [restoreProject, setRestoreProject] = useState(null);
  const [permanentProject, setPermanentProject] = useState(null);
  const [busyAction, setBusyAction] = useState(false);

  const loadRef = useRef(false);

  const loadProjects = useCallback(async () => {
    try {
      const [list, rb] = await Promise.all([
        api.projects.list(),
        api.projects.recycleBin(),
      ]);
      setProjects(list.projects || []);
      setRecycle(rb.projects || []);
      setError(null);
    } catch (err) {
      setError(translateError(err.message));
    }
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    await loadProjects();
    setLoading(false);
  }, [loadProjects]);

  useEffect(() => {
    if (loadRef.current) return;
    loadRef.current = true;
    loadAll();
  }, [loadAll]);

  const mine = projects.filter((p) => p.role === "owner");
  const shared = projects.filter((p) => p.role !== "owner");

  const isAdmin = authUser?.role === "admin";

  const handleOpen = useCallback(
    (project) => {
      clearProject();
      setOpenedProject({
        projectId: project.project_id,
        projectRole: project.role,
      });
      navigate("/workspace");
    },
    [clearProject, setOpenedProject, navigate],
  );

  const handleDownload = useCallback(
    async (project) => {
      try {
        await api.projects.download(
          project.project_id,
          `${project.name || "project"}.zip`,
        );
        toast.success(t("pages:workbench.downloadStarted"));
      } catch (err) {
        toast.error(t("pages:workbench.downloadFailed"), {
          description: translateError(err.message),
        });
      }
    },
    [t],
  );

  const handleRename = useCallback(async () => {
    if (!renameProject || !renameValue.trim()) return;
    setBusyAction(true);
    try {
      await api.projects.rename(renameProject.project_id, renameValue.trim());
      toast.success(t("pages:workbench.renamed"));
      setRenameProject(null);
      await loadAll();
    } catch (err) {
      toast.error(t("pages:workbench.renameFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyAction(false);
    }
  }, [renameProject, renameValue, t, loadAll]);

  const handleDelete = useCallback(async () => {
    if (!deleteProject) return;
    setBusyAction(true);
    try {
      await api.projects.remove(deleteProject.project_id);
      toast.success(t("pages:workbench.movedToRecycle"));
      setDeleteProject(null);
      await loadAll();
    } catch (err) {
      toast.error(t("pages:workbench.deleteFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyAction(false);
    }
  }, [deleteProject, t, loadAll]);

  const handleRestore = useCallback(async () => {
    if (!restoreProject) return;
    setBusyAction(true);
    try {
      await api.projects.restore(restoreProject.project_id);
      toast.success(t("pages:workbench.restored"));
      setRestoreProject(null);
      await loadAll();
    } catch (err) {
      toast.error(t("pages:workbench.restoreFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyAction(false);
    }
  }, [restoreProject, t, loadAll]);

  const handlePermanent = useCallback(async () => {
    if (!permanentProject) return;
    setBusyAction(true);
    try {
      await api.projects.permanentDelete(permanentProject.project_id);
      toast.success(t("pages:workbench.permanentDeleted"));
      setPermanentProject(null);
      await loadAll();
    } catch (err) {
      toast.error(t("pages:workbench.permanentDeleteFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyAction(false);
    }
  }, [permanentProject, t, loadAll]);

  const projectQuota = quota?.project_quota_bytes;
  const projectUsage = usage?.project_usage_bytes;
  const usagePct =
    projectQuota && projectQuota > 0
      ? Math.min(100, Math.round((projectUsage / projectQuota) * 100))
      : 0;

  const roleBadge = (role) => {
    const map = {
      owner: { label: t("common:role.owner"), cls: "" },
      editor: { label: t("common:role.editor"), cls: "" },
      viewer: { label: t("common:role.viewer"), cls: "" },
    };
    const r = map[role] || { label: role, cls: "" };
    return <Badge variant="secondary">{r.label}</Badge>;
  };

  const renderProjectRow = (p) => {
    const isOwner = p.role === "owner";
    return (
      <div
        key={p.project_id}
        className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium">{p.name}</p>
            {roleBadge(p.role)}
            {p.is_shared && (
              <Badge variant="outline">{t("pages:workbench.sharedTag")}</Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {isOwner
              ? `${t("pages:workbench.ownerLabel")} · ${p.owner_username || "—"}`
              : `${t("pages:workbench.ownerBy", {
                  username: p.owner_username || "—",
                })}`}{" "}
            · {formatBytes(p.size_bytes)} ·{" "}
            {t("pages:workbench.createdAt", {
              date: formatDate(p.created_at),
            })}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <Button size="sm" onClick={() => handleOpen(p)}>
            <FolderOpen /> {t("pages:workbench.open")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => handleDownload(p)}>
            <Download /> {t("pages:workbench.download")}
          </Button>
          {isOwner && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setRenameProject(p);
                  setRenameValue(p.name);
                }}
              >
                <Pencil /> {t("pages:workbench.rename")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setPermsProject(p)}
              >
                <ShieldCheck /> {t("pages:workbench.permissions")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive"
                onClick={() => setDeleteProject(p)}
              >
                <Trash2 /> {t("pages:workbench.delete")}
              </Button>
            </>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="mx-auto max-w-5xl">
        <PageHeader
          title={t("pages:workbench.title")}
          description={t("pages:workbench.desc")}
          backTo="/"
        >
          <Button onClick={() => setUploadOpen(true)}>
            <UploadCloud /> {t("pages:workbench.upload")}
          </Button>
          <Button variant="outline" onClick={loadAll} disabled={loading}>
            <RefreshCw className={loading ? "animate-spin" : ""} />
            {t("pages:workbench.refresh")}
          </Button>
          {isAdmin && (
            <Button
              variant="outline"
              nativeButton={false}
              render={<Link to="/admin" />}
            >
              <ShieldCheck /> {t("pages:workbench.admin")}
            </Button>
          )}
          <Button
            variant="ghost"
            onClick={async () => {
              await logoutUser();
              navigate("/login", { replace: true });
            }}
          >
            <LogOut /> {t("pages:workbench.logout")}
          </Button>
        </PageHeader>

        {/* 配额用量条 */}
        <Card className="mb-6">
          <CardContent className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="inline-flex items-center gap-2 text-muted-foreground">
                {t("pages:workbench.userLabel")}:{" "}
                <span className="font-medium text-foreground">
                  {authUser?.username || "—"}
                </span>
                {isAdmin && <Badge variant="secondary">Admin</Badge>}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatBytes(projectUsage)} / {formatBytes(projectQuota)} ·{" "}
                {t("pages:workbench.projectQuota")}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${usagePct}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {error ? (
          <p className="mb-4 text-sm text-destructive">{error}</p>
        ) : null}

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="w-full">
            <TabsTrigger value="mine">
              {t("pages:workbench.tabMine")} ({mine.length})
            </TabsTrigger>
            <TabsTrigger value="shared">
              {t("pages:workbench.tabShared")} ({shared.length})
            </TabsTrigger>
            <TabsTrigger value="recycle">
              {t("pages:workbench.tabRecycle")} ({recycle.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="mine" className="pt-4">
            {loading && mine.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                <Loader className="mr-2 inline size-4 animate-spin" />
                {t("pages:workbench.loading")}
              </p>
            ) : mine.length === 0 ? (
              <EmptyHint onUpload={() => setUploadOpen(true)} />
            ) : (
              <div className="space-y-3">{mine.map(renderProjectRow)}</div>
            )}
          </TabsContent>

          <TabsContent value="shared" className="pt-4">
            {shared.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t("pages:workbench.noShared")}
              </p>
            ) : (
              <div className="space-y-3">{shared.map(renderProjectRow)}</div>
            )}
          </TabsContent>

          <TabsContent value="recycle" className="pt-4">
            {recycle.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t("pages:workbench.noRecycle")}
              </p>
            ) : (
              <div className="space-y-3">
                {recycle.map((p) => (
                  <div
                    key={p.project_id}
                    className="flex flex-col gap-3 rounded-lg border p-4 opacity-80 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{p.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatBytes(p.size_bytes)} ·{" "}
                        {t("pages:workbench.deletedAt", {
                          date: formatDate(p.deleted_at),
                        })}
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-1.5">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setRestoreProject(p)}
                      >
                        <ArchiveRestore /> {t("pages:workbench.restore")}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() => setPermanentProject(p)}
                      >
                        <RotateCcw className="rotate-180" />
                        {t("pages:workbench.permanentDelete")}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>

        <ProjectPermissionsDialog
          open={!!permsProject}
          onOpenChange={(o) => !o && setPermsProject(null)}
          project={permsProject}
          onChanged={loadAll}
        />

        <UploadProjectDialog
          open={uploadOpen}
          onOpenChange={setUploadOpen}
          onUploaded={loadAll}
        />

        {/* 重命名 */}
        <ConfirmDialog
          open={!!renameProject}
          onOpenChange={(o) => !o && setRenameProject(null)}
          title={t("pages:workbench.renameTitle")}
          confirmText={t("pages:workbench.rename")}
          onConfirm={handleRename}
          confirmLoading={busyAction}
          description={
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                {t("pages:workbench.renameDesc")}
              </p>
              <Input
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleRename()}
              />
            </div>
          }
        />

        <ConfirmDialog
          open={!!deleteProject}
          onOpenChange={(o) => !o && setDeleteProject(null)}
          title={t("pages:workbench.deleteTitle")}
          description={t("pages:workbench.deleteDesc", {
            name: deleteProject?.name || "",
          })}
          confirmText={t("pages:workbench.delete")}
          variant="destructive"
          onConfirm={handleDelete}
          confirmLoading={busyAction}
        />

        <ConfirmDialog
          open={!!restoreProject}
          onOpenChange={(o) => !o && setRestoreProject(null)}
          title={t("pages:workbench.restoreTitle")}
          description={t("pages:workbench.restoreDesc")}
          confirmText={t("pages:workbench.restore")}
          onConfirm={handleRestore}
          confirmLoading={busyAction}
        />

        <ConfirmDialog
          open={!!permanentProject}
          onOpenChange={(o) => !o && setPermanentProject(null)}
          title={t("pages:workbench.permanentTitle")}
          description={t("pages:workbench.permanentDesc", {
            name: permanentProject?.name || "",
          })}
          confirmText={t("pages:workbench.permanentDelete")}
          variant="destructive"
          onConfirm={handlePermanent}
          confirmLoading={busyAction}
        />
      </div>
    </div>
  );
}

function EmptyHint({ onUpload }) {
  const { t } = useTranslation("pages");
  return (
    <div className="py-14 text-center">
      <p className="text-sm text-muted-foreground">
        {t("workbench.emptyMine")}
      </p>
      <Button variant="outline" className="mt-3" onClick={onUpload}>
        <UploadCloud /> {t("workbench.uploadFirst")}
      </Button>
    </div>
  );
}
