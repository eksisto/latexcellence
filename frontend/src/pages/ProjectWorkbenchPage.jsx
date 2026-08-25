import { memo, useCallback, useEffect, useRef, useState } from "react";
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
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
import Loader from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Crown from "lucide-react/dist/esm/icons/crown.mjs";
import Mail from "lucide-react/dist/esm/icons/mail.mjs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import PageHeader from "@/components/PageHeader";
import ConfirmDialog from "@/components/ConfirmDialog";
import ProfileEmailDialog from "@/components/ProfileEmailDialog";
import UploadProjectDialog from "@/components/UploadProjectDialog";
import ProjectPermissionsDialog from "@/components/ProjectPermissionsDialog";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";
import { formatBytes, formatDate } from "@/utils/format";

// 角色→i18n key 映射提升为模块级常量：避免每行渲染时重建（rerender / hoist）
const ROLE_LABEL_KEYS = {
  owner: "common:role.owner",
  editor: "common:role.editor",
  viewer: "common:role.viewer",
};

// 衍生数据分类（与后端 userspace.DERIVED_SUBDIRS 对齐，v2 P4）
const DERIVED_CATEGORIES = [
  { key: "writing_chat_sessions", labelKey: "derivedWritingChat" },
  { key: "style_modify_sessions", labelKey: "derivedStyleModify" },
  { key: "templates", labelKey: "derivedTemplates" },
  { key: "plots", labelKey: "derivedPlots" },
];

/**
 * 衍生数据分类用量行（memo 化）：单类用量条 + 「清理」按钮。
 * props 全为原始值 + 稳定回调（onCleanup 由父级 useCallback 提供，
 * category 经 prop 传入、行内闭包拼装），与 ProjectRow 同一约定，
 * 父级无关状态变化时仅依赖变化的行重渲染。
 */
const DerivedUsageRow = memo(function DerivedUsageRow({
  category,
  label,
  bytes,
  quotaBytes,
  cleanupLabel,
  onCleanup,
}) {
  const pct =
    quotaBytes > 0 ? Math.min(100, Math.round((bytes / quotaBytes) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="flex items-center gap-2 text-muted-foreground">
          {formatBytes(bytes)}
          <Button
            size="sm"
            variant="ghost"
            disabled={bytes <= 0}
            className="h-6 px-2 text-xs text-destructive hover:text-destructive"
            onClick={() => onCleanup(category)}
          >
            <Trash2 className="size-3" /> {cleanupLabel}
          </Button>
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary/70"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
});

/**
 * 项目行（memo 化）：props 为原始值 + 稳定回调，父级状态变化（busyAction、
 * tab 切换、配额条等）时仅依赖变化的行重渲染。Tabs 激活过的 Panel 会保持挂载，
 * 因此三个列表所有访问过的行都会在父级每次重渲染时被 memo 拦截。
 */
const ProjectRow = memo(function ProjectRow({
  project: p,
  onOpen,
  onDownload,
  onRename,
  onPerms,
  onDelete,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const isOwner = p.role === "owner";
  const roleKey = ROLE_LABEL_KEYS[p.role];
  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{p.name}</p>
          {roleKey && <Badge variant="secondary">{t(roleKey)}</Badge>}
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
        <Button size="sm" onClick={() => onOpen(p)}>
          <FolderOpen /> {t("pages:workbench.open")}
        </Button>
        <Button size="sm" variant="outline" onClick={() => onDownload(p)}>
          <Download /> {t("pages:workbench.download")}
        </Button>
        {isOwner && (
          <>
            <Button size="sm" variant="ghost" onClick={() => onRename(p)}>
              <Pencil /> {t("pages:workbench.rename")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onPerms(p)}>
              <ShieldCheck /> {t("pages:workbench.permissions")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              onClick={() => onDelete(p)}
            >
              <Trash2 /> {t("pages:workbench.delete")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
});

/** 回收站行（memo 化，同 ProjectRow 理由） */
const RecycleRow = memo(function RecycleRow({
  project: p,
  onRestore,
  onPermanent,
}) {
  const { t } = useTranslation("pages");
  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{p.name}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {formatBytes(p.size_bytes)} ·{" "}
          {t("workbench.deletedAt", { date: formatDate(p.deleted_at) })}
        </p>
      </div>
      <div className="flex shrink-0 gap-1.5">
        <Button size="sm" variant="outline" onClick={() => onRestore(p)}>
          <ArchiveRestore /> {t("workbench.restore")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="text-destructive"
          onClick={() => onPermanent(p)}
        >
          <RotateCcw className="rotate-180" />
          {t("workbench.permanentDelete")}
        </Button>
      </div>
    </div>
  );
});

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
  const setAuthMe = useStore((s) => s.setAuthMe);
  const setOpenedProject = useStore((s) => s.setOpenedProject);
  const clearProject = useStore((s) => s.clearProject);

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
  const [cleanupCategory, setCleanupCategory] = useState(null);
  const [emailDialogOpen, setEmailDialogOpen] = useState(false);
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
    // 上传/删除/恢复/转交都会改变项目空间占用：并行补一次 /auth/me 刷新
    // 用户、配额与用量，避免用量条一直显示启动/登录时的旧值（审查 M2）。
    // me() 失败不阻塞列表加载（会话失效由全局 401 回调处理）。
    const [me] = await Promise.all([
      api.auth.me().catch(() => null),
      loadProjects(),
    ]);
    if (me) setAuthMe(me);
    setLoading(false);
  }, [loadProjects, setAuthMe]);

  useEffect(() => {
    if (loadRef.current) return;
    loadRef.current = true;
    loadAll();
  }, [loadAll]);

  const mine = projects.filter((p) => p.role === "owner");
  const shared = projects.filter((p) => p.role !== "owner");

  const isAdmin = authUser?.role === "admin";

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

  // 衍生数据分类用量与配额（v2 P4：/api/auth/me usage.derived_usage 明细）
  const derivedQuota = quota?.derived_quota_bytes;
  const derivedUsage = usage?.derived_usage || null;
  const cleanupTarget = DERIVED_CATEGORIES.find(
    (c) => c.key === cleanupCategory,
  );

  const handleStartCleanupDerived = useCallback((category) => {
    setCleanupCategory(category);
  }, []);

  const handleCleanupDerived = useCallback(async () => {
    if (!cleanupCategory) return;
    setBusyAction(true);
    try {
      await api.auth.cleanupDerivedData(cleanupCategory);
      toast.success(t("pages:workbench.derivedCleaned"));
      setCleanupCategory(null);
      // 清理即时刷新了服务端缓存，这里同步拉取最新用量（me 失败不阻塞）
      const me = await api.auth.me().catch(() => null);
      if (me) setAuthMe(me);
    } catch (err) {
      toast.error(t("pages:workbench.derivedCleanFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setBusyAction(false);
    }
  }, [cleanupCategory, t, setAuthMe]);

  // 行级动作回调：稳定引用（rerender-memo），配合 ProjectRow/RecycleRow memo 生效
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

  const handleStartRename = useCallback((project) => {
    setRenameProject(project);
    setRenameValue(project.name);
  }, []);

  const handleOpenPerms = useCallback((project) => {
    setPermsProject(project);
  }, []);
  const handleStartDelete = useCallback((project) => {
    setDeleteProject(project);
  }, []);
  const handleStartRestore = useCallback((project) => {
    setRestoreProject(project);
  }, []);
  const handleStartPermanent = useCallback((project) => {
    setPermanentProject(project);
  }, []);

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
          {/* 退出登录与管理后台入口统一收在右上角工具栏图标（带悬浮提示），页头不再重复 */}
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
                {isAdmin && (
                  <Badge variant="secondary">
                    <Crown className="size-3" /> {t("common:role.admin")}
                  </Badge>
                )}
                {/* 绑定/修改邮箱（v2 P6：审批通知与自助找回依赖邮箱） */}
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setEmailDialogOpen(true)}
                >
                  <Mail className="size-3" />
                  {authUser?.email || t("pages:workbench.emailBind")}
                </Button>
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

            {/* 衍生数据分类用量条 + 自助清理（v2 P4） */}
            {derivedUsage && (
              <div className="space-y-2.5 border-t pt-3">
                <p className="text-xs font-medium text-muted-foreground">
                  {t("pages:workbench.derivedUsageTitle")} ·{" "}
                  {formatBytes(derivedQuota)}
                </p>
                {DERIVED_CATEGORIES.map((c) => (
                  <DerivedUsageRow
                    key={c.key}
                    category={c.key}
                    label={t(`pages:workbench.${c.labelKey}`)}
                    bytes={derivedUsage[c.key] || 0}
                    quotaBytes={derivedQuota || 0}
                    cleanupLabel={t("pages:workbench.derivedCleanup")}
                    onCleanup={handleStartCleanupDerived}
                  />
                ))}
              </div>
            )}
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
              <div className="space-y-3">
                {mine.map((p) => (
                  <ProjectRow
                    key={p.project_id}
                    project={p}
                    onOpen={handleOpen}
                    onDownload={handleDownload}
                    onRename={handleStartRename}
                    onPerms={handleOpenPerms}
                    onDelete={handleStartDelete}
                  />
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="shared" className="pt-4">
            {loading && shared.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                <Loader className="mr-2 inline size-4 animate-spin" />
                {t("pages:workbench.loading")}
              </p>
            ) : shared.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t("pages:workbench.noShared")}
              </p>
            ) : (
              <div className="space-y-3">
                {shared.map((p) => (
                  <ProjectRow
                    key={p.project_id}
                    project={p}
                    onOpen={handleOpen}
                    onDownload={handleDownload}
                    onRename={handleStartRename}
                    onPerms={handleOpenPerms}
                    onDelete={handleStartDelete}
                  />
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="recycle" className="pt-4">
            {loading && recycle.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                <Loader className="mr-2 inline size-4 animate-spin" />
                {t("pages:workbench.loading")}
              </p>
            ) : recycle.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {t("pages:workbench.noRecycle")}
              </p>
            ) : (
              <div className="space-y-3">
                {recycle.map((p) => (
                  <RecycleRow
                    key={p.project_id}
                    project={p}
                    onRestore={handleStartRestore}
                    onPermanent={handleStartPermanent}
                  />
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

        {/* 衍生数据自助清理二次确认（v2 P4） */}
        <ConfirmDialog
          open={!!cleanupCategory}
          onOpenChange={(o) => !o && setCleanupCategory(null)}
          title={t("pages:workbench.derivedCleanupTitle", {
            name: cleanupTarget
              ? t(`pages:workbench.${cleanupTarget.labelKey}`)
              : "",
          })}
          description={t("pages:workbench.derivedCleanupDesc", {
            name: cleanupTarget
              ? t(`pages:workbench.${cleanupTarget.labelKey}`)
              : "",
            size: formatBytes(
              cleanupTarget ? derivedUsage?.[cleanupTarget.key] || 0 : 0,
            ),
          })}
          confirmText={t("pages:workbench.derivedCleanup")}
          variant="destructive"
          onConfirm={handleCleanupDerived}
          confirmLoading={busyAction}
        />

        {/* 绑定/修改邮箱（v2 P6） */}
        <ProfileEmailDialog
          open={emailDialogOpen}
          onOpenChange={setEmailDialogOpen}
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
