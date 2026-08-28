import { Fragment, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import ArrowUpRight from "lucide-react/dist/esm/icons/arrow-up-right.mjs";
import CircleCheck from "lucide-react/dist/esm/icons/circle-check.mjs";
import FileDiff from "lucide-react/dist/esm/icons/file-diff.mjs";
import FileMinus from "lucide-react/dist/esm/icons/file-minus.mjs";
import FilePlus from "lucide-react/dist/esm/icons/file-plus.mjs";
import FilePen from "lucide-react/dist/esm/icons/file-pen.mjs";
import GitBranch from "lucide-react/dist/esm/icons/git-branch.mjs";
import History from "lucide-react/dist/esm/icons/history.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import RefreshCw from "lucide-react/dist/esm/icons/refresh-cw.mjs";
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
const COMMIT_MAX_LENGTH = 500;

const CHANGE_KIND_META = {
  added: {
    icon: FilePlus,
    key: "changeAdded",
    chip: "bg-primary/10 text-primary",
  },
  modified: {
    icon: FilePen,
    key: "changeModified",
    chip: "bg-accent text-accent-foreground",
  },
  deleted: {
    icon: FileMinus,
    key: "changeDeleted",
    chip: "bg-destructive/10 text-destructive",
  },
};
const CHANGE_KIND_ORDER = ["added", "modified", "deleted"];

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

const MS_MINUTE = 60_000;
const MS_HOUR = 3_600_000;
const MS_DAY = 86_400_000;

/** RelativeTimeFormat 按语言缓存：Intl 构造器解析 locale 数据开销大，不能每次渲染重建 */
const relativeTimeFormatCache = new Map();
function getRelativeTimeFormat(language) {
  const key = language || "default";
  let rtf = relativeTimeFormatCache.get(key);
  if (!rtf) {
    try {
      rtf = new Intl.RelativeTimeFormat(key === "default" ? undefined : key, {
        numeric: "auto",
      });
    } catch {
      /* 语言标签非法时回落到默认 locale */
      rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    }
    relativeTimeFormatCache.set(key, rtf);
  }
  return rtf;
}

/** 相对时间（7 天内），更早回落为本地日期；title 中仍可用 formatDateTime 看精确时刻 */
function formatRelativeTime(iso, language) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diff = d.getTime() - Date.now();
  const abs = Math.abs(diff);
  if (abs < 7 * MS_DAY) {
    const rtf = getRelativeTimeFormat(language);
    if (abs < MS_MINUTE) return rtf.format(Math.round(diff / 1000), "second");
    if (abs < MS_HOUR)
      return rtf.format(Math.round(diff / MS_MINUTE), "minute");
    if (abs < MS_DAY) return rtf.format(Math.round(diff / MS_HOUR), "hour");
    return rtf.format(Math.round(diff / MS_DAY), "day");
  }
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/** 日历日归组键（本地时区） */
function dayKey(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? null
    : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** 时间线日期分隔标签：今天 / 昨天 / 本地日期 */
function dayLabel(iso, t) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const startOfDay = (x) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(new Date()) - startOfDay(d)) / MS_DAY);
  if (days === 0) return t("pages:versionManage.today");
  if (days === 1) return t("pages:versionManage.yesterday");
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/** 提交表单：message/committing 内聚在本组件，输入按键只重渲表单而不重渲整页时间线 */
function CommitForm({ changeCount, workspaceId, loadStatus, loadLog }) {
  const { t } = useTranslation(["pages"]);
  const [message, setMessage] = useState("");
  const [committing, setCommitting] = useState(false);

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

  return (
    <div className="space-y-2.5 border-t pt-4">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor="commit-message" className="text-sm font-medium">
          {t("pages:versionManage.commitMessageLabel")}
        </label>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
          {message.length}/{COMMIT_MAX_LENGTH}
        </span>
      </div>
      <Textarea
        id="commit-message"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder={t("pages:versionManage.commitPlaceholder")}
        rows={3}
        maxLength={COMMIT_MAX_LENGTH}
      />
      <div className="flex justify-end">
        <Button onClick={handleCommit} disabled={committing || !message.trim()}>
          {committing && <Loader2 className="size-4 animate-spin" />}
          {t("pages:versionManage.commitButtonCount", { count: changeCount })}
        </Button>
      </div>
    </div>
  );
}

export default function VersionManagePage() {
  const { t, i18n } = useTranslation(["pages", "common"]);
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const workspaceId = useStore((s) => s.workspaceId);
  const fileTreeLength = useStore((s) => s.fileTree.length);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setRootPath = useStore((s) => s.setRootPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setMainFile = useStore((s) => s.setMainFile);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const isOpened = useIsOpened();
  const canWrite = useCanWrite();

  const [status, setStatus] = useState(null);
  const [initting, setInitting] = useState(false);
  const [logEntries, setLogEntries] = useState([]);
  const [logOffset, setLogOffset] = useState(0);
  const [loadingLog, setLoadingLog] = useState(false);

  const loadStatus = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const data = await api.gitManage.status(workspaceId);
      setStatus(data);
    } catch (err) {
      toast.error(translateError(err.message));
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
    const needsRestore = fileTreeLength === 0;
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
  }, [isOpened, projectId, projectPath, workspaceId, fileTreeLength]);

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
  const changeCount = status?.changes?.length ?? 0;
  const kindCounts = { added: 0, modified: 0, deleted: 0 };
  for (const change of status?.changes ?? []) {
    kindCounts[CHANGE_KIND_META[change.kind] ? change.kind : "modified"] += 1;
  }

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto space-y-6">
        <PageHeader
          title={t("pages:versionManage.title")}
          description={t("pages:versionManage.description")}
          groupKey="versionControl"
        />

        {!status ? (
          /* 加载骨架：与 repo bar 同形，避免加载完成跳动 */
          <div
            role="status"
            className="flex items-center gap-4 rounded-lg border bg-card px-4 py-3"
          >
            <span className="sr-only">{t("pages:versionManage.loading")}</span>
            <div className="h-5 w-24 rounded-sm bg-muted motion-safe:animate-pulse" />
            <div className="h-5 w-28 rounded-sm bg-muted motion-safe:animate-pulse" />
            <div className="ml-auto h-5 w-52 rounded-sm bg-muted motion-safe:animate-pulse" />
          </div>
        ) : !initialized ? (
          <EmptyState
            icon={GitBranch}
            title={t("pages:versionManage.initTitle")}
            description={t("pages:versionManage.notInitialized")}
          >
            <div className="flex flex-col items-center gap-3">
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
          </EmptyState>
        ) : (
          <>
            {/* repo bar：分支 · 脏净状态 · 版本库占用，一条看全当前仓库 */}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5 rounded-lg border bg-card px-4 py-3">
              <div className="flex min-w-0 items-center gap-3">
                <Badge variant="outline" className="gap-1.5 font-mono text-xs">
                  <GitBranch className="size-3" aria-hidden />
                  <span className="max-w-40 truncate">
                    {status.current_branch ||
                      t("pages:versionManage.detachedHead")}
                  </span>
                </Badge>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    aria-hidden
                    className={cn(
                      "size-1.5 rounded-full",
                      status.is_dirty
                        ? "bg-primary motion-safe:animate-pulse"
                        : "bg-muted-foreground/40",
                    )}
                  />
                  {t(
                    status.is_dirty
                      ? "pages:versionManage.dirty"
                      : "pages:versionManage.clean",
                  )}
                </span>
              </div>
              <div className="ml-auto flex flex-wrap items-center gap-2.5 text-xs text-muted-foreground">
                <span>{t("pages:versionManage.gitDirUsage")}</span>
                <span className="font-mono tabular-nums">
                  {formatBytes(status.git_dir_size_bytes, t)}
                  {status.git_dir_max_bytes > 0 &&
                    ` / ${formatBytes(status.git_dir_max_bytes, t)}`}
                </span>
                {status.git_dir_max_bytes > 0 && (
                  <span
                    aria-hidden
                    className="h-1.5 w-28 overflow-hidden rounded-full bg-muted"
                  >
                    <span
                      className={cn(
                        "block h-full rounded-full transition-all duration-300",
                        gitNearCap ? "bg-destructive" : "bg-primary",
                      )}
                      style={{ width: `${gitPct}%` }}
                    />
                  </span>
                )}
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6 items-start">
              {/* 左栏：未提交的世界——变更文件 + 提交表单 */}
              <Card className="self-start lg:sticky lg:top-8">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FilePen className="size-4" />
                    {t("pages:versionManage.changesTitle")}
                    {changeCount > 0 && (
                      <Badge
                        variant="secondary"
                        className="font-mono text-[11px] tabular-nums"
                      >
                        {changeCount}
                      </Badge>
                    )}
                  </CardTitle>
                  <CardDescription>
                    {t("pages:versionManage.changesDescription")}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {changeCount === 0 ? (
                    <div className="flex items-center gap-2 py-1 text-sm text-muted-foreground">
                      <CircleCheck className="size-4 shrink-0" aria-hidden />
                      {t("pages:versionManage.noChanges")}
                    </div>
                  ) : (
                    <>
                      {/* diffstat 摘要：按类型计数的行内概览 */}
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        {CHANGE_KIND_ORDER.filter((k) => kindCounts[k] > 0).map(
                          (kind) => {
                            const meta = CHANGE_KIND_META[kind];
                            const KindIcon = meta.icon;
                            return (
                              <span
                                key={kind}
                                className="flex items-center gap-1"
                                title={t(`pages:versionManage.${meta.key}`)}
                              >
                                <KindIcon className="size-3" aria-hidden />
                                {t(`pages:versionManage.${meta.key}`)}
                                <span className="font-mono tabular-nums">
                                  {kindCounts[kind]}
                                </span>
                              </span>
                            );
                          },
                        )}
                      </div>

                      <ul className="-mx-1 space-y-0.5">
                        {status.changes.map((change) => {
                          const meta =
                            CHANGE_KIND_META[change.kind] ||
                            CHANGE_KIND_META.modified;
                          const KindIcon = meta.icon;
                          const chip = (
                            <span
                              className={cn(
                                "grid size-5 shrink-0 place-items-center rounded-sm",
                                meta.chip,
                              )}
                              title={t(`pages:versionManage.${meta.key}`)}
                            >
                              <KindIcon className="size-3" aria-hidden />
                            </span>
                          );
                          // 已删除文件无法在编辑器中打开，渲染为不可点击条目
                          if (change.kind === "deleted") {
                            return (
                              <li
                                key={change.path}
                                className="flex items-center gap-2.5 rounded-md px-2 py-1.5 opacity-60"
                              >
                                {chip}
                                <span
                                  className="min-w-0 flex-1 truncate font-mono text-xs line-through"
                                  title={change.path}
                                >
                                  {change.path}
                                </span>
                              </li>
                            );
                          }
                          return (
                            <li key={change.path}>
                              <Link
                                to={`/editor?file=${encodeURIComponent(change.path)}`}
                                className="group flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-muted/60"
                              >
                                {chip}
                                <span
                                  className="min-w-0 flex-1 truncate font-mono text-xs"
                                  title={change.path}
                                >
                                  {change.path}
                                </span>
                                <ArrowUpRight
                                  className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
                                  aria-hidden
                                />
                              </Link>
                            </li>
                          );
                        })}
                      </ul>

                      {canWrite ? (
                        <CommitForm
                          changeCount={changeCount}
                          workspaceId={workspaceId}
                          loadStatus={loadStatus}
                          loadLog={loadLog}
                        />
                      ) : (
                        <p className="border-t pt-4 text-xs text-muted-foreground">
                          {t("pages:versionManage.readOnlyHint")}
                        </p>
                      )}
                    </>
                  )}
                </CardContent>
              </Card>

              {/* 右栏：已提交的世界——以「当前工作区」为起点的时间线 */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <History className="size-4" />
                    {t("pages:versionManage.logTitle")}
                  </CardTitle>
                  <CardAction>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => loadLog(logOffset)}
                      disabled={loadingLog}
                      aria-label={t("pages:versionManage.refresh")}
                    >
                      <RefreshCw className={loadingLog ? "animate-spin" : ""} />
                    </Button>
                  </CardAction>
                </CardHeader>
                <CardContent>
                  <div className="relative">
                    <div
                      aria-hidden
                      className="absolute bottom-3 left-[7px] top-3 w-px bg-border"
                    />
                    <ol className="space-y-5">
                      <li className="relative pl-8">
                        <span
                          aria-hidden
                          className="absolute left-0 top-0.5 grid size-[15px] place-items-center rounded-full border border-primary/50 bg-primary/15"
                        >
                          <span
                            className={cn(
                              "size-1.5 rounded-full bg-primary",
                              status.is_dirty && "motion-safe:animate-pulse",
                            )}
                          />
                        </span>
                        <p className="text-sm font-medium">
                          {t("pages:versionManage.worktreeNode")}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {status.is_dirty
                            ? t("pages:versionManage.worktreeDirtyCount", {
                                count: changeCount,
                              })
                            : t("pages:versionManage.worktreeClean")}
                        </p>
                      </li>
                      {loadingLog && logEntries.length === 0 ? (
                        <li className="relative pl-8">
                          <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="size-4 animate-spin" />
                            {t("pages:versionManage.loading")}
                          </div>
                        </li>
                      ) : logEntries.length === 0 ? (
                        <li className="relative pl-8 pt-1 text-sm text-muted-foreground">
                          {t("pages:versionManage.noCommits")}
                        </li>
                      ) : (
                        logEntries.map((entry, i) => {
                          const showDate =
                            i === 0 ||
                            dayKey(entry.date_iso) !==
                              dayKey(logEntries[i - 1].date_iso);
                          return (
                            <Fragment key={entry.hash}>
                              {showDate && (
                                <li className="relative pl-8 pt-1">
                                  <span className="text-[11px] font-medium tracking-wide text-muted-foreground">
                                    {dayLabel(entry.date_iso, t)}
                                  </span>
                                </li>
                              )}
                              <li className="relative pl-8">
                                <span
                                  aria-hidden
                                  className="absolute left-0 top-1 size-[15px] rounded-full border border-border bg-card"
                                />
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0">
                                    <div className="flex min-w-0 items-center gap-2">
                                      <span className="shrink-0 font-mono text-xs text-muted-foreground">
                                        {entry.short_hash}
                                      </span>
                                      {logOffset === 0 && i === 0 && (
                                        <span className="shrink-0 rounded-sm bg-accent px-1 py-px font-mono text-[10px] leading-4 text-accent-foreground">
                                          HEAD
                                        </span>
                                      )}
                                      <span
                                        className="truncate text-sm font-medium"
                                        title={entry.subject}
                                      >
                                        {entry.subject}
                                      </span>
                                    </div>
                                    <p className="mt-0.5 text-xs text-muted-foreground">
                                      {entry.author_name} ·{" "}
                                      <time
                                        dateTime={entry.date_iso}
                                        title={formatDateTime(entry.date_iso)}
                                      >
                                        {formatRelativeTime(
                                          entry.date_iso,
                                          i18n.language,
                                        )}
                                      </time>
                                    </p>
                                  </div>
                                  <Button
                                    variant="ghost"
                                    size="icon-sm"
                                    className="shrink-0 text-muted-foreground"
                                    nativeButton={false}
                                    render={
                                      <Link
                                        to={`/git-diff?base=${entry.hash}&target=${WORKTREE_REF}`}
                                        title={t(
                                          "pages:versionManage.compareWithWorktree",
                                        )}
                                        aria-label={t(
                                          "pages:versionManage.compareWithWorktree",
                                        )}
                                      />
                                    }
                                  >
                                    <FileDiff className="size-3.5" />
                                  </Button>
                                </div>
                              </li>
                            </Fragment>
                          );
                        })
                      )}
                    </ol>
                  </div>
                  {logEntries.length > 0 && (
                    <div className="mt-6 flex items-center justify-end gap-2">
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
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
