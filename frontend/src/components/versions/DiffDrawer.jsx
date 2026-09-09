import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import ArrowLeft from "lucide-react/dist/esm/icons/arrow-left.mjs";
import ChevronRight from "lucide-react/dist/esm/icons/chevron-right.mjs";
import FileCode2 from "lucide-react/dist/esm/icons/file-code-2.mjs";
import FileMinus from "lucide-react/dist/esm/icons/file-minus.mjs";
import FilePen from "lucide-react/dist/esm/icons/file-pen.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import RotateCcw from "lucide-react/dist/esm/icons/rotate-ccw.mjs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { api } from "@/api/index";
import { cn } from "@/lib/utils";
import { translateError } from "@/utils/errorMessages";
import DiffView from "./DiffView";
import { CHANGE_KIND_META } from "./changeKind";

/**
 * 源码 diff 抽屉（版本管理页）。
 *
 * - mode="worktree"：单文件工作区 vs HEAD 的 diff，可勾选 hunk 撤销、
 *   整文件恢复（restorable=false 的场景——未跟踪/无基线——只展示）；
 * - mode="commit"：单个提交的两级导航（文件清单 → 单文件 diff），只读。
 *
 * 恢复请求回传 hunk.raw 原文切片；服务端重新生成 diff 校验切片后
 * apply（fail-closed），409（工作区已变）时自动刷新 diff。
 * 工作区模式支持预取缓存（getCachedDiff）：命中即渲染，随后静默刷新。
 */
export default function DiffDrawer({
  open,
  onOpenChange,
  workspaceId,
  mode = "worktree",
  path = "",
  commit = "",
  canWrite = false,
  getCachedDiff,
  onRestored,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const [loading, setLoading] = useState(false);
  const [diff, setDiff] = useState(null);
  const [commitFiles, setCommitFiles] = useState(null);
  const [activeFile, setActiveFile] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [restoring, setRestoring] = useState(false);
  const [confirmFileRestore, setConfirmFileRestore] = useState(false);

  const loadWorktreeDiff = useCallback(
    async (silent = false) => {
      if (!workspaceId || !path) return;
      if (!silent) setLoading(true);
      try {
        const data = await api.gitManage.fileDiff(path, workspaceId);
        setDiff(data);
        setSelected(new Set());
      } catch (err) {
        toast.error(translateError(err.message));
      } finally {
        setLoading(false);
      }
    },
    [workspaceId, path],
  );

  const loadCommitFiles = useCallback(async () => {
    if (!workspaceId || !commit) return;
    setLoading(true);
    try {
      const data = await api.gitManage.commitFiles(commit, workspaceId);
      setCommitFiles(data);
    } catch (err) {
      toast.error(translateError(err.message));
    } finally {
      setLoading(false);
    }
  }, [workspaceId, commit]);

  // 打开/目标变化时重新加载；关闭时清态（下次打开必为新数据）。
  // 预取缓存命中：即时渲染缓存内容，再后台静默刷新保鲜。
  useEffect(() => {
    if (!open) {
      setDiff(null);
      setCommitFiles(null);
      setActiveFile("");
      setSelected(new Set());
      setConfirmFileRestore(false);
      return;
    }
    if (mode === "worktree") {
      const cached = getCachedDiff ? getCachedDiff(path) : null;
      if (cached) {
        setDiff(cached);
        setSelected(new Set());
        loadWorktreeDiff(true);
      } else {
        loadWorktreeDiff();
      }
    } else {
      loadCommitFiles();
    }
  }, [open, mode, path, loadWorktreeDiff, loadCommitFiles, getCachedDiff]);

  const toggleHunk = useCallback((index) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const doRestore = useCallback(
    async (payload, successKey) => {
      setRestoring(true);
      try {
        await api.gitManage.restore(payload, workspaceId);
        toast.success(
          t(`pages:versionManage.${successKey}`, {
            count: payload.hunks?.length ?? 0,
          }),
        );
        onRestored?.(payload.path);
        if (mode === "worktree") await loadWorktreeDiff(true);
        return true;
      } catch (err) {
        toast.error(translateError(err.message));
        // fail-closed 被拒（工作区已变）：静默刷新 diff 让用户基于最新状态重选
        if (err?.code === "git_worktree_changed" && mode === "worktree") {
          await loadWorktreeDiff(true);
        }
        return false;
      } finally {
        setRestoring(false);
      }
    },
    [workspaceId, mode, onRestored, loadWorktreeDiff, t],
  );

  const selectedCount = selected.size;
  const hunks = diff?.hunks ?? [];
  const kindMeta = diff?.kind ? CHANGE_KIND_META[diff.kind] : null;

  const worktreeRestorable =
    mode === "worktree" && canWrite && diff?.restorable;
  // 勾选框仅在存在可恢复基线时有意义（未跟踪/无基线隐藏，避免无效勾选）
  const hunksSelectable = mode === "worktree" && diff?.restorable === true;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>
            {mode === "worktree"
              ? path
              : activeFile || t("pages:versionManage.commitDiffTitle")}
          </SheetTitle>
          <SheetDescription className="flex items-center gap-2">
            {mode === "commit" && (
              <>
                <span className="font-mono">{commit.slice(0, 7)}</span>
                <span>·</span>
                {t("pages:versionManage.commitDiffReadonly")}
              </>
            )}
            {mode === "worktree" && diff?.kind && kindMeta && (
              <>
                <KindChip kind={diff.kind} />
                {t(`pages:versionManage.${kindMeta.key}`)}
                {(diff.additions > 0 || diff.deletions > 0) && (
                  <span className="font-mono tabular-nums">
                    <span className="text-emerald-600 dark:text-emerald-400">
                      +{diff.additions}
                    </span>{" "}
                    <span className="text-red-600 dark:text-red-400">
                      −{diff.deletions}
                    </span>
                  </span>
                )}
              </>
            )}
            {mode === "worktree" &&
              !diff?.kind &&
              !loading &&
              t("pages:versionManage.diffEmpty")}
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("pages:versionManage.diffLoading")}
            </div>
          ) : mode === "commit" && !activeFile ? (
            <CommitFileList files={commitFiles} onOpen={setActiveFile} />
          ) : mode === "commit" && activeFile ? (
            <CommitFileDiff
              workspaceId={workspaceId}
              commit={commit}
              file={activeFile}
              onBack={() => setActiveFile("")}
            />
          ) : (
            <WorktreeDiffBody
              diff={diff}
              selectable={hunksSelectable}
              selected={selected}
              onToggle={toggleHunk}
            />
          )}
        </div>

        {mode === "worktree" && (
          <div className="border-t p-4">
            {worktreeRestorable ? (
              <div className="flex items-center gap-2">
                <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {selectedCount > 0
                    ? t("pages:versionManage.selectedHunks", {
                        count: selectedCount,
                      })
                    : t("pages:versionManage.restoreHint")}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={restoring || selectedCount === 0}
                  onClick={() =>
                    doRestore(
                      {
                        path,
                        mode: "hunks",
                        hunks: [...selected]
                          .sort((a, b) => a - b)
                          .map((i) => hunks[i].raw),
                      },
                      "restoreSuccess",
                    )
                  }
                >
                  {restoring && <Loader2 className="size-4 animate-spin" />}
                  <RotateCcw className="size-3.5" />
                  {t("pages:versionManage.restoreSelected")}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={restoring}
                  onClick={() => setConfirmFileRestore(true)}
                >
                  <RotateCcw className="size-3.5" />
                  {t("pages:versionManage.restoreWholeFile")}
                </Button>
              </div>
            ) : diff?.kind && !canWrite ? (
              <p className="text-xs text-muted-foreground">
                {t("pages:versionManage.readOnlyHint")}
              </p>
            ) : diff?.kind === "added" ? (
              <p className="text-xs text-muted-foreground">
                {t("pages:versionManage.untrackedNoRestore")}
              </p>
            ) : null}
          </div>
        )}

        {/* 整文件恢复确认：丢弃未提交修改属破坏性操作，强制确认 */}
        <Dialog open={confirmFileRestore} onOpenChange={setConfirmFileRestore}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>
                {t("pages:versionManage.restoreFileConfirmTitle")}
              </DialogTitle>
              <DialogDescription>
                {t("pages:versionManage.restoreFileConfirmBody", { path })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setConfirmFileRestore(false)}
              >
                {t("common:cancel")}
              </Button>
              <Button
                variant="destructive"
                disabled={restoring}
                onClick={() => {
                  setConfirmFileRestore(false);
                  doRestore({ path, mode: "file" }, "restoreFileSuccess");
                }}
              >
                {t("pages:versionManage.confirmRestore")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </SheetContent>
    </Sheet>
  );
}

function KindChip({ kind }) {
  const { t } = useTranslation(["pages"]);
  const meta = CHANGE_KIND_META[kind] ?? CHANGE_KIND_META.modified;
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "grid size-4 shrink-0 place-items-center rounded-sm",
        meta.chip,
      )}
      title={t(`pages:versionManage.${meta.key}`)}
    >
      <Icon className="size-2.5" aria-hidden />
    </span>
  );
}

/** 工作区 diff 正文：降级卡片（二进制/超大/截断）+ DiffView */
function WorktreeDiffBody({ diff, selectable, selected, onToggle }) {
  const { t } = useTranslation(["pages"]);
  if (!diff) return null;
  if (diff.kind && diff.is_binary) {
    return (
      <Notice icon={FileMinus} text={t("pages:versionManage.diffBinary")} />
    );
  }
  if (diff.kind && diff.too_large) {
    return (
      <Notice icon={FileMinus} text={t("pages:versionManage.diffTooLarge")} />
    );
  }
  if (diff.truncated && diff.hunks.length === 0) {
    return (
      <Notice icon={FilePen} text={t("pages:versionManage.diffOversized")} />
    );
  }
  return (
    <>
      {diff.truncated && (
        <p className="border-b bg-accent/50 px-4 py-2 text-xs text-muted-foreground">
          {t("pages:versionManage.diffTruncated", {
            count: MAX_DIFF_LINES_HINT,
          })}
        </p>
      )}
      <DiffView
        diff={diff}
        selectable={selectable}
        selected={selected}
        onToggle={onToggle}
      />
    </>
  );
}

// 与后端 _MAX_DIFF_LINES（git_manage/service.py）保持一致，仅用于
// 「仅显示前 N 行」提示文案；后端实际在 hunk 边界截断，N 为近似值
const MAX_DIFF_LINES_HINT = 2000;

function Notice({ icon: Icon, text }) {
  return (
    <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
      <Icon className="size-4 shrink-0" aria-hidden />
      {text}
    </div>
  );
}

/** commit 模式第一级：该提交变更的文件清单 */
function CommitFileList({ files, onOpen }) {
  const { t } = useTranslation(["pages"]);
  if (!files) return null;
  if (files.files.length === 0) {
    return (
      <Notice icon={FileCode2} text={t("pages:versionManage.commitNoFiles")} />
    );
  }
  return (
    <ul className="p-2">
      {files.files.map((item) => (
        <li key={item.path}>
          <button
            type="button"
            onClick={() => onOpen(item.path)}
            className="group flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/60"
          >
            <KindChip kind={item.kind} />
            <span
              className={cn(
                "min-w-0 flex-1 truncate font-mono text-xs",
                item.kind === "deleted" && "line-through text-muted-foreground",
              )}
              title={item.path}
            >
              {item.path}
            </span>
            <ChevronRight
              className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
              aria-hidden
            />
          </button>
        </li>
      ))}
    </ul>
  );
}

/** commit 模式第二级：单文件只读 diff（按需拉取） */
function CommitFileDiff({ workspaceId, commit, file, onBack }) {
  const { t } = useTranslation(["pages"]);
  const [state, setState] = useState({ loading: true, diff: null });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, diff: null });
    api.gitManage
      .fileDiff(file, workspaceId, commit)
      .then((data) => {
        if (!cancelled) setState({ loading: false, diff: data });
      })
      .catch((err) => {
        if (!cancelled) {
          setState({ loading: false, diff: null });
          toast.error(translateError(err.message));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, commit, file]);

  return (
    <div>
      <div className="sticky top-0 z-10 flex items-center gap-1 border-b bg-popover/95 px-2 py-1.5 backdrop-blur">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-3.5" />
          {t("pages:versionManage.backToFiles")}
        </Button>
      </div>
      {state.loading ? (
        <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {t("pages:versionManage.diffLoading")}
        </div>
      ) : state.diff?.hunks?.length ? (
        <DiffView diff={state.diff} />
      ) : (
        <Notice
          icon={FileCode2}
          text={t("pages:versionManage.commitFileNoDiff")}
        />
      )}
    </div>
  );
}
