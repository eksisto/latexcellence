import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import FileMinus from "lucide-react/dist/esm/icons/file-minus.mjs";
import FilePlus from "lucide-react/dist/esm/icons/file-plus.mjs";
import FilePen from "lucide-react/dist/esm/icons/file-pen.mjs";
import GitBranch from "lucide-react/dist/esm/icons/git-branch.mjs";
import GitCommit from "lucide-react/dist/esm/icons/git-commit-horizontal.mjs";
import History from "lucide-react/dist/esm/icons/history.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
import TriangleAlert from "lucide-react/dist/esm/icons/triangle-alert.mjs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import EmptyState from "@/components/EmptyState";
import PageHeader from "@/components/PageHeader";
import { api } from "@/api/index";
import { cn } from "@/lib/utils";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";
import { openMultiUserProject } from "@/utils/multiUserProject";
import useCanWrite from "@/hooks/useCanWrite";
import useIsOpened from "@/hooks/useIsOpened";
import { isMultiUser } from "@/api/connection";

const WORKTREE_REF = "__WORKTREE__";
const PAGE_SIZE = 50;

const CHANGE_KIND_META = {
  added: { icon: FilePlus, variant: "default", key: "changeAdded" },
  modified: { icon: FilePen, variant: "secondary", key: "changeModified" },
  deleted: { icon: FileMinus, variant: "destructive", key: "changeDeleted" },
};

function formatBytes(bytes, t) {
  if (bytes >= 1024 * 1024) {
    return t("pages:versionManage.sizeMB", {
      value: (bytes / 1024 / 1024).toFixed(1),
    });
  }
  if (bytes >= 1024) {
    return t("pages:versionManage.sizeKB", {
      value: (bytes / 1024).toFixed(1),
    });
  }
  return t("pages:versionManage.sizeB", { value: bytes });
}

function formatDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function VersionManagePage() {
  const { t } = useTranslation(["pages", "common"]);
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const workspaceId = useStore((s) => s.workspaceId);
  const fileTree = useStore((s) => s.fileTree);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setRootPath = useStore((s) => s.setRootPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setMainFile = useStore((s) => s.setMainFile);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const isOpened = useIsOpened();
  const canWrite = useCanWrite();

  const [status, setStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [initting, setInitting] = useState(false);
  const [message, setMessage] = useState("");
  const [committing, setCommitting] = useState(false);
  const [logEntries, setLogEntries] = useState([]);
  const [logOffset, setLogOffset] = useState(0);
  const [loadingLog, setLoadingLog] = useState(false);

  const loadStatus = useCallback(async () => {
    if (!workspaceId) return;
    setLoadingStatus(true);
    try {
      const data = await api.gitManage.status(workspaceId);
      setStatus(data);
    } catch (err) {
      toast.error(translateError(err.message));
    } finally {
      setLoadingStatus(false);
    }
  }, [workspaceId]);

  const loadLog = useCallback(
    async (offset = 0) => {
      if (!workspaceId) return;
      setLoadingLog(true);
      try {
        const data = await api.gitManage.log(workspaceId, PAGE_SIZE, offset);
        setLogEntries(data.entries || []);
        setLogOffset(offset);
      } catch (err) {
        toast.error(translateError(err.message));
      } finally {
        setLoadingLog(false);
      }
    },
    [workspaceId],
  );

  // 刷新会话（F5）后服务端项目可能未打开：按 GitDiffPage 同款口径恢复
  // （fileTree 为空视为本会话尚未打开过），随后拉取状态与历史
  useEffect(() => {
    if (!isOpened) return;
    let cancelled = false;
    const needsRestore = fileTree.length === 0;
    const restore = !needsRestore
      ? null
      : isMultiUser()
        ? projectId
          ? openMultiUserProject(projectId, workspaceId)
          : null
        : projectPath
          ? api.project.open(projectPath, workspaceId).then((data) => {
              if (cancelled) return;
              setRootPath(data.root_path);
              setProjectPath(projectPath);
              setFileTree(data.file_tree);
              setMainFile(data.main_file);
              setActiveFile(data.main_file);
            })
          : null;
    (async () => {
      try {
        if (restore) await restore;
      } catch {
        /* 打开失败由后续 status 请求报错 */
      }
      if (cancelled) return;
      // status/log 串行加载：两者都是 git 沙箱命令，并行会在每用户
      // 沙箱并发紧张时互相顶掉（429），串行也避免双 toast 叠加
      await loadStatus();
      if (cancelled) return;
      await loadLog(0);
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpened, projectId, projectPath, workspaceId, fileTree.length]);

  const handleInit = useCallback(async () => {
    setInitting(true);
    try {
      await api.gitManage.init(workspaceId);
      toast.success(t("pages:versionManage.initSuccess"));
      await loadStatus();
      await loadLog(0);
    } catch (err) {
      toast.error(translateError(err.message));
    } finally {
      setInitting(false);
    }
  }, [workspaceId, loadStatus, loadLog, t]);

  const handleCommit = useCallback(async () => {
    const trimmed = message.trim();
    if (!trimmed) return;
    setCommitting(true);
    try {
      await api.gitManage.commit(trimmed, workspaceId);
      toast.success(t("pages:versionManage.commitSuccess"));
      setMessage("");
      await loadStatus();
      await loadLog(0);
    } catch (err) {
      toast.error(translateError(err.message));
    } finally {
      setCommitting(false);
    }
  }, [message, workspaceId, loadStatus, loadLog, t]);

  if (!isOpened) {
    return (
      <div className="min-h-screen p-8 animate-tab-enter">
        <div className="max-w-3xl mx-auto">
          <PageHeader
            title={t("pages:versionManage.title")}
            groupKey="versionControl"
          />
          <EmptyState
            icon={History}
            title={t("pages:versionManage.noProjectTitle")}
            description={t("pages:versionManage.noProjectDesc")}
          >
            <Button
              nativeButton={false}
              render={<Link to={isMultiUser() ? "/projects" : "/workspace"} />}
            >
              {isMultiUser()
                ? t("common:openProjects")
                : t("pages:versionManage.openProjectWorkspace")}
            </Button>
          </EmptyState>
        </div>
      </div>
    );
  }

  const initialized = Boolean(status?.initialized);
  const gitPct =
    status && status.git_dir_max_bytes > 0
      ? Math.min(
          100,
          (status.git_dir_size_bytes / status.git_dir_max_bytes) * 100,
        )
      : 0;
  const gitNearCap = gitPct >= 85;
  const hasPrev = logOffset > 0;
  const hasNext = logEntries.length >= PAGE_SIZE;

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-4xl mx-auto space-y-6">
        <PageHeader
          title={t("pages:versionManage.title")}
          description={t("pages:versionManage.description")}
          groupKey="versionControl"
        />

        {/* 状态卡 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <GitBranch className="size-4" />
              {t("pages:versionManage.statusTitle")}
            </CardTitle>
            <CardDescription>
              {t("pages:versionManage.statusDescription")}
            </CardDescription>
            <CardAction>
              <Button
                variant="ghost"
                size="icon"
                onClick={loadStatus}
                disabled={loadingStatus}
              >
                <RefreshCw className={loadingStatus ? "animate-spin" : ""} />
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-4">
            {!status ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
                <Loader2 className="size-4 animate-spin" />
                {t("pages:versionManage.loading")}
              </div>
            ) : !initialized ? (
              <div className="space-y-3">
                <div className="flex items-start gap-2 rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                  <TriangleAlert className="size-4 mt-0.5 shrink-0" />
                  {t("pages:versionManage.notInitialized")}
                </div>
                <Button onClick={handleInit} disabled={!canWrite || initting}>
                  {initting && <Loader2 className="size-4 animate-spin" />}
                  {t("pages:versionManage.initButton")}
                </Button>
                {!canWrite && (
                  <p className="text-xs text-muted-foreground">
                    {t("pages:versionManage.readOnlyHint")}
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant="outline" className="gap-1">
                    <GitBranch className="size-3" />
                    {status.current_branch ||
                      t("pages:versionManage.detachedHead")}
                  </Badge>
                  {status.is_dirty ? (
                    <Badge variant="secondary">
                      {t("pages:versionManage.dirty")}
                    </Badge>
                  ) : (
                    <Badge variant="outline">
                      {t("pages:versionManage.clean")}
                    </Badge>
                  )}
                </div>

                {/* .git 用量（配额豁免，独立上限） */}
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>{t("pages:versionManage.gitDirUsage")}</span>
                    <span className="tabular-nums">
                      {formatBytes(status.git_dir_size_bytes, t)} /{" "}
                      {formatBytes(status.git_dir_max_bytes, t)}
                    </span>
                  </div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all duration-300",
                        gitNearCap ? "bg-destructive" : "bg-primary",
                      )}
                      style={{ width: `${gitPct}%` }}
                    />
                  </div>
                </div>

                {status.last_commit && (
                  <div className="flex items-start gap-2 text-sm">
                    <GitCommit className="size-4 mt-0.5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="truncate">
                        <span className="font-mono text-xs text-muted-foreground mr-2">
                          {status.last_commit.short_hash}
                        </span>
                        {status.last_commit.subject}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {status.last_commit.author} ·{" "}
                        {formatDateTime(status.last_commit.date)}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 变更文件 + 提交 */}
        {initialized && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                {t("pages:versionManage.changesTitle")}
              </CardTitle>
              <CardDescription>
                {t("pages:versionManage.changesDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {status.changes.length === 0 ? (
                <p className="text-sm text-muted-foreground py-2">
                  {t("pages:versionManage.noChanges")}
                </p>
              ) : (
                <ul className="divide-y rounded-md border text-sm">
                  {status.changes.map((change) => {
                    const meta =
                      CHANGE_KIND_META[change.kind] ||
                      CHANGE_KIND_META.modified;
                    const KindIcon = meta.icon;
                    const label = (
                      <>
                        <Badge
                          variant={meta.variant}
                          className="gap-1 shrink-0"
                        >
                          <KindIcon className="size-3" />
                          {t(`pages:versionManage.${meta.key}`)}
                        </Badge>
                        <span className="truncate font-mono text-xs">
                          {change.path}
                        </span>
                      </>
                    );
                    // 已删除文件无法在编辑器中打开，渲染为不可点击条目
                    if (change.kind === "deleted") {
                      return (
                        <li
                          key={change.path}
                          className="flex items-center gap-3 px-3 py-2 opacity-70"
                        >
                          {label}
                        </li>
                      );
                    }
                    return (
                      <li key={change.path}>
                        <Link
                          to={`/editor?file=${encodeURIComponent(change.path)}`}
                          className="flex items-center gap-3 px-3 py-2 hover:bg-muted/60 transition-colors"
                        >
                          {label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}

              {canWrite ? (
                <div className="space-y-2">
                  <Textarea
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    placeholder={t("pages:versionManage.commitPlaceholder")}
                    rows={3}
                    maxLength={500}
                  />
                  <div className="flex justify-end">
                    <Button
                      onClick={handleCommit}
                      disabled={committing || !message.trim()}
                    >
                      {committing && (
                        <Loader2 className="size-4 animate-spin" />
                      )}
                      {t("pages:versionManage.commitButton")}
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("pages:versionManage.readOnlyHint")}
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {/* 提交历史 */}
        {initialized && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <History className="size-4" />
                {t("pages:versionManage.logTitle")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {loadingLog && logEntries.length === 0 ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="size-4 animate-spin" />
                  {t("pages:versionManage.loading")}
                </div>
              ) : logEntries.length === 0 ? (
                <p className="text-sm text-muted-foreground py-2">
                  {t("pages:versionManage.noCommits")}
                </p>
              ) : (
                <>
                  <ul className="divide-y rounded-md border text-sm">
                    {logEntries.map((entry) => (
                      <li
                        key={entry.hash}
                        className="flex items-start gap-3 px-3 py-2.5"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate">
                            <span className="font-mono text-xs text-muted-foreground mr-2">
                              {entry.short_hash}
                            </span>
                            {entry.subject}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {entry.author_name} ·{" "}
                            {formatDateTime(entry.date_iso)}
                          </p>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          nativeButton={false}
                          render={
                            <Link
                              to={`/git-diff?base=${entry.hash}&target=${WORKTREE_REF}`}
                            />
                          }
                        >
                          {t("pages:versionManage.compareWithWorktree")}
                        </Button>
                      </li>
                    ))}
                  </ul>
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        loadLog(Math.max(0, logOffset - PAGE_SIZE))
                      }
                      disabled={!hasPrev || loadingLog}
                    >
                      {t("pages:versionManage.prevPage")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => loadLog(logOffset + PAGE_SIZE)}
                      disabled={!hasNext || loadingLog}
                    >
                      {t("pages:versionManage.nextPage")}
                    </Button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
