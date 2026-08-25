/**
 * EditorPage —— /editor 全宽三栏布局（editor-plan §4，阶段二骨架）。
 *
 * 布局：左栏复用 ProjectExplorer 文件树（点击打开文件）· 中栏 LaTeXEditor
 * （CM6 打开-编辑-保存-冲突闭环）· 右栏占位骨架（编译结果 / PDF 预览完整
 * 联动属阶段四）。桌面优先。
 *
 * 闭环职责：
 *   - 打开：GET file-content → 缓冲区 + baseMd5 基线；超约 2MB 拒绝打开；
 *   - 编辑：onChange 回写 editorSession 并置脏标记；
 *   - 保存：Ctrl/Cmd+S（window 级统一拦截，编辑器内外行为一致）与工具栏
 *     按钮 → save-file 乐观锁落盘，成功更新 baseMd5、清脏标记；
 *   - 冲突：409 → ConflictDialog（放弃本地重载 / 本地内容剪贴板逃生）；
 *   - 权限：viewer 可进入但只读（禁用保存与后端撤销）；判断项目已打开
 *     一律用 useIsOpened()（多用户看 projectId）；
 *   - 刷新直达恢复：复用模块页恢复语义（多用户 openMultiUserProject
 *     preserveActiveFile，单用户 api.project.open），仅当 fileTree 为空时执行。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import EmptyState from "@/components/EmptyState";
import ConfirmDialog from "@/components/ConfirmDialog";
import ProjectExplorer from "@/components/ProjectExplorer";
import LaTeXEditor from "@/components/editor/LaTeXEditor";
import EditorToolbar from "@/components/editor/EditorToolbar";
import ConflictDialog from "@/components/editor/ConflictDialog";
import CommandPalette from "@/components/editor/CommandPalette";

import FileCode from "lucide-react/dist/esm/icons/file-code.mjs";
import PanelRight from "lucide-react/dist/esm/icons/panel-right.mjs";

import useStore from "@/store/useStore";
import useEditorStore from "@/store/editorStore";
import useIsOpened from "@/hooks/useIsOpened";
import useCanWrite from "@/hooks/useCanWrite";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import { translateError } from "@/utils/errorMessages";
import { openMultiUserProject } from "@/utils/multiUserProject";
import { isEditableFilePath, isFileTooLarge } from "@/lib/editor/files";
import { expandEntryInsert, insertSnippet } from "@/lib/editor/snippetInsert";
import {
  buildPackageInsertion,
  isPackageMissing,
} from "@/lib/editor/preambleScan";

/** 右栏占位骨架：编译结果 / PDF 预览完整联动属阶段四 */
function EditorPlaceholderPanel() {
  const { t } = useTranslation("components");
  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("editor.panelTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <EmptyState
          icon={PanelRight}
          title={t("editor.panelEmptyTitle")}
          description={t("editor.panelEmptyDescription")}
          className="py-10"
        />
      </CardContent>
    </Card>
  );
}

export default function EditorPage() {
  const { t } = useTranslation(["pages", "components"]);
  const [searchParams, setSearchParams] = useSearchParams();

  const workspaceId = useStore((s) => s.workspaceId);
  const fileTree = useStore((s) => s.fileTree);
  const hydrated = useStore((s) => s.hydrated);
  const isOpened = useIsOpened();
  const canWrite = useCanWrite();
  const readOnly = !canWrite;

  const session = useEditorStore(
    useShallow((s) => ({
      filePath: s.filePath,
      content: s.content,
      dirty: s.dirty,
      conflict: s.conflict,
      softWrap: s.softWrap,
      fontSize: s.fontSize,
    })),
  );
  const openBuffer = useEditorStore((s) => s.openBuffer);
  const setBufferContent = useEditorStore((s) => s.setBufferContent);
  const markSaved = useEditorStore((s) => s.markSaved);
  const setConflict = useEditorStore((s) => s.setConflict);

  const [saving, setSaving] = useState(false);
  const [opening, setOpening] = useState(false);
  const [switchConfirmPath, setSwitchConfirmPath] = useState(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const restoringRef = useRef(false);
  /** 打开请求序号：单调递增，用于丢弃过期在途响应（快速切换 / 后退前进） */
  const openSeqRef = useRef(0);
  /** 保存请求重入门闸：同步 ref，杜绝同一渲染帧内连点产生并发保存 */
  const savingRef = useRef(false);
  /** CM6 视图实例：命令面板 / 宏包自动添加等外部插入入口经此落笔 */
  const editorViewRef = useRef(null);

  /** 结构化错误翻译：错误码优先（file_not_found 补 path 插值），未收录回落 message */
  const describeError = useCallback((e, filePathForNotFound) => {
    if (e?.code === "file_not_found") {
      return translateError({ error: e.code, path: filePathForNotFound ?? "" });
    }
    return translateError({ error: e?.code, message: e?.message });
  }, []);

  /* ── 打开文件（返回是否成功；过期请求返回 false） ──────────────────── */
  const doOpenFile = useCallback(
    async (filePath) => {
      const seq = ++openSeqRef.current;
      setOpening(true);
      try {
        const data = await api.project.fileContent(filePath, workspaceId);
        // 响应返回前已有更新的打开请求发出：本次结果整体作废，
        // 防止慢响应后到把旧文件写进新缓冲区（URL 与缓冲区错位）
        if (seq !== openSeqRef.current) return false;
        // 文件上限（方案 §4）：超约 2MB 拒绝进入缓冲区，防浏览器端卡顿
        if (isFileTooLarge(data.content)) {
          toast.error(t("pages:editor.fileTooLarge", { path: filePath }));
          return false;
        }
        openBuffer({ filePath, content: data.content, baseMd5: data.md5 });
        // URL 同步当前文件（replace 避免污染历史栈）
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (next.get("file") !== filePath) next.set("file", filePath);
            return next;
          },
          { replace: true },
        );
        return true;
      } catch (e) {
        if (seq !== openSeqRef.current) return false;
        // 失败不动现有缓冲区；仅当失败目标本就是当前缓冲区时清残留
        const cur = useEditorStore.getState();
        if (cur.filePath === filePath && !cur.dirty) {
          cur.closeBuffer();
        }
        toast.error(t("pages:editor.openFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      } finally {
        if (seq === openSeqRef.current) setOpening(false);
      }
    },
    [workspaceId, openBuffer, setSearchParams, t, describeError],
  );

  /** 文件树点击入口：白名单 + 脏改动确认后打开 */
  const requestOpenFile = useCallback(
    (filePath) => {
      if (!filePath || opening || saving) return;
      if (!isEditableFilePath(filePath)) {
        toast.error(t("pages:editor.unsupportedType", { path: filePath }));
        return;
      }
      const cur = useEditorStore.getState();
      // 同一文件且无脏改动：幂等跳过；同文件带脏改动也无须重开
      if (cur.filePath === filePath) return;
      if (cur.dirty) {
        setSwitchConfirmPath(filePath);
        return;
      }
      doOpenFile(filePath);
    },
    [doOpenFile, opening, saving, t],
  );

  /* ── URL ?file= 参数驱动打开（含刷新直达） ────────────────────────── */
  const requestedFile = searchParams.get("file");
  useEffect(() => {
    if (!isOpened || fileTree.length === 0 || !requestedFile) return;
    if (!isEditableFilePath(requestedFile)) {
      toast.error(t("pages:editor.unsupportedType", { path: requestedFile }));
      return;
    }
    const cur = useEditorStore.getState();
    // 已打开同一文件（无论脏否）不重复拉取；不同文件且有脏改动走确认
    if (cur.filePath === requestedFile) return;
    if (cur.dirty) {
      setSwitchConfirmPath(requestedFile);
      return;
    }
    doOpenFile(requestedFile);
  }, [requestedFile, isOpened, fileTree.length, doOpenFile, t]);

  /* ── 刷新直达恢复：fileTree 为空且已声明打开过项目时重建工作区绑定 ── */
  useEffect(() => {
    if (!hydrated || restoringRef.current || !isOpened) return;
    if (fileTree.length > 0) return;
    restoringRef.current = true;
    (async () => {
      try {
        const store = useStore.getState();
        if (isMultiUser()) {
          if (store.projectId) {
            await openMultiUserProject(store.projectId, workspaceId, {
              preserveActiveFile: true,
            });
          }
        } else if (store.projectPath) {
          const data = await api.project.open(store.projectPath, workspaceId);
          store.setRootPath(data.root_path);
          store.setFileTree(data.file_tree);
          store.setMainFile(data.main_file);
        }
      } catch (_) {
        // 恢复失败静默：呈现"未打开项目"空态，用户可回工作台重开
      } finally {
        restoringRef.current = false;
      }
    })();
  }, [hydrated, isOpened, fileTree.length, workspaceId]);

  /* ── 保存（MD5 乐观锁；返回是否成功落账） ─────────────────────────── */
  const doSave = useCallback(async () => {
    // 同步 ref 重入门闸：快速连点 / 键盘连击在同一渲染帧内到达时，
    // 仅第一笔生效，避免并发双写同一 baseMd5 触发自伤式 409 冲突弹窗
    if (savingRef.current) return false;
    const cur = useEditorStore.getState();
    // 冲突弹窗打开期间 baseMd5 已过期：再次保存必然 409，
    // 只会重复弹出冲突提示；出路只有弹窗内的两条显式路径
    if (!cur.filePath || !cur.dirty || cur.conflict || !canWrite) {
      return false;
    }
    savingRef.current = true;
    setSaving(true);
    // 保存快照：请求发出后缓冲区可能继续变化或切换文件，
    // 结果只对发起时的 (filePath, content) 生效
    const savedPath = cur.filePath;
    const savedContent = cur.content;
    try {
      const res = await api.project.saveFile(
        savedPath,
        savedContent,
        cur.baseMd5,
        workspaceId,
      );
      // 在途切换文件：结果属于旧缓冲区，丢弃且不惊扰新文件的编辑
      if (useEditorStore.getState().filePath !== savedPath) return false;
      markSaved(res.md5, savedContent);
      toast.success(t("pages:editor.saved"));
      return true;
    } catch (e) {
      if (useEditorStore.getState().filePath !== savedPath) return false;
      if (e?.code === "file_conflict") {
        // 409 冲突流程：弹窗接管（消息经 translateError 三语翻译）
        setConflict({ message: describeError(e, savedPath) });
        toast.error(t("pages:editor.conflictToast"));
      } else {
        toast.error(t("pages:editor.saveFailed"), {
          description: describeError(e, savedPath),
        });
      }
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [canWrite, describeError, markSaved, setConflict, t, workspaceId]);

  /* ── Ctrl/Cmd+S：window 级统一拦截（编辑器内外行为一致、单一触发点） ── */
  const saveRef = useRef(doSave);
  saveRef.current = doSave;
  useEffect(() => {
    const handler = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key?.toLowerCase() !== "s") return;
      e.preventDefault();
      saveRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  /* ── Ctrl/Cmd+/：命令面板（editor-plan §6.3 入口二） ───────────────── */
  useEffect(() => {
    const handler = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key !== "/") return;
      e.preventDefault();
      setPaletteOpen((open) => !open);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  /* ── 后端撤销/重做落盘后 / 冲突放弃本地：重拉文件内容刷新缓冲区 ──── */
  const handleReloadBuffer = useCallback(async () => {
    const cur = useEditorStore.getState();
    if (!cur.filePath) return false;
    return doOpenFile(cur.filePath);
  }, [doOpenFile]);

  /* ── 宏包缺失联动（editor-plan §6.5 / 决策 Q14） ─────────────────────
   * 插入含 pkg 条目时对插入前的缓冲区做导言区扫描；缺失则 toast 提示并
   * 提供「自动添加 \usepackage」动作：按三级回退插入点写入 CM6 缓冲区，
   * 经 onChange 正常置脏，落盘仍走 save-file 单一路径。viewer 不触发。 */
  const addPackageToPreamble = useCallback(
    (pkg) => {
      const view = editorViewRef.current;
      // 缓冲区关闭会卸载 CM 视图：宏包提示条（10s）可能比视图存活更久，
      // 向已销毁视图 dispatch 会抛错，此处一并拦截
      if (!view || view.destroyed || !pkg) return;
      const insertion = buildPackageInsertion(
        useEditorStore.getState().content,
        pkg,
      );
      // 已存在或无法定位插入点：静默返回（提示条本身只是辅助）
      if (!insertion) return;
      view.dispatch({
        changes: { from: insertion.index, insert: insertion.text },
        scrollIntoView: true,
      });
      view.focus();
      toast.success(t("components:commandPalette.pkgAdded", { pkg }));
    },
    [t],
  );

  const notifyMissingPackage = useCallback(
    (entry) => {
      const pkg = entry.pkg;
      if (!pkg || !canWrite) return;
      const description = `\\usepackage{${pkg}}`;
      toast.warning(
        t("components:commandPalette.pkgMissing", {
          name: entry.zh || `\\${entry.name}`,
          pkg,
        }),
        {
          description,
          duration: 10000,
          action: {
            label: t("components:commandPalette.pkgAddAction"),
            onClick: () => addPackageToPreamble(pkg),
          },
        },
      );
    },
    [addPackageToPreamble, canWrite, t],
  );

  /**
   * 命令面板统一插入出口：模板经占位符模式写入缓冲区。
   * @returns {boolean} 是否已执行插入（false 时面板保持打开）
   */
  const handleInsertEntry = useCallback(
    (entry, size) => {
      if (!canWrite) return false;
      const cur = useEditorStore.getState();
      const view = editorViewRef.current;
      if (!cur.filePath || !view || view.destroyed) {
        toast.info(t("components:commandPalette.noFileHint"));
        return false;
      }
      // 宏包扫描基于插入前内容：命令文本不影响导言区已有声明
      const contentBefore = cur.content;
      insertSnippet(view, expandEntryInsert(entry, size));
      if (entry.pkg && isPackageMissing(contentBefore, entry.pkg)) {
        notifyMissingPackage(entry);
      }
      return true;
    },
    [canWrite, notifyMissingPackage, t],
  );

  const handleCreateEditor = useCallback((view) => {
    editorViewRef.current = view;
  }, []);

  /* 行内补全应用后的宏包缺失联动：与面板插入同一提示逻辑（§6.5 一致性）。
   * 扫描当前缓冲区即可——插入的命令文本不含 usepackage 声明。 */
  const handleCompletionApplyEntry = useCallback(
    (entry) => {
      if (!canWrite || !entry?.pkg) return;
      if (isPackageMissing(useEditorStore.getState().content, entry.pkg)) {
        notifyMissingPackage(entry);
      }
    },
    [canWrite, notifyMissingPackage],
  );

  const handleSelectTreeNode = useCallback(
    (node) => {
      if (node.is_dir) return;
      requestOpenFile(node.path);
    },
    [requestOpenFile],
  );

  const handleDiscardAndReload = useCallback(async () => {
    // 重拉失败（网络断开 / 文件被删）时返回 false：冲突弹窗保持打开，
    // 本地缓冲区原样保留，用户仍可走剪贴板逃生，不误报"已重载"
    const ok = await handleReloadBuffer();
    if (ok) toast.success(t("pages:editor.reloadedFromDisk"));
    return ok;
  }, [handleReloadBuffer, t]);

  /* ── 未打开项目：空态守卫 ─────────────────────────────────────────── */
  if (hydrated && !isOpened) {
    return (
      <div className="min-h-screen p-8 pt-16 animate-tab-enter">
        <div className="max-w-6xl mx-auto space-y-4">
          <h1 className="font-heading text-xl font-semibold">
            {t("pages:editor.title")}
          </h1>
          <EmptyState
            icon={FileCode}
            title={t("pages:editor.noProjectTitle")}
            description={t("pages:editor.noProjectDescription")}
          >
            <Button nativeButton={false} render={<Link to="/workspace" />}>
              {t("pages:editor.goWorkspace")}
            </Button>
          </EmptyState>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col gap-3 p-4 pt-16 pb-6 animate-tab-enter">
      <EditorToolbar
        filePath={session.filePath}
        saving={saving}
        readOnly={readOnly}
        onSave={doSave}
        onReloadBuffer={handleReloadBuffer}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <div className="flex flex-1 min-h-0 gap-3">
        {/* 左栏：文件树（复用 ProjectExplorer） */}
        <aside className="w-64 shrink-0 min-h-0">
          <Card className="flex h-full flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {t("pages:workspace.files")}
              </CardTitle>
            </CardHeader>
            <CardContent className="min-h-0 flex-1 overflow-auto p-2">
              <ProjectExplorer
                tree={fileTree}
                onSelect={handleSelectTreeNode}
              />
            </CardContent>
          </Card>
        </aside>

        {/* 中栏：CM6 编辑器 */}
        <main className="relative min-h-0 min-w-0 flex-1">
          {opening && (
            <div className="absolute inset-0 z-10 grid place-items-center bg-background/60 text-sm text-muted-foreground">
              {t("pages:editor.opening")}
            </div>
          )}
          {session.filePath ? (
            <LaTeXEditor
              value={session.content}
              onChange={setBufferContent}
              softWrap={session.softWrap}
              fontSize={session.fontSize}
              readOnly={readOnly}
              onCreateEditor={handleCreateEditor}
              onCompletionApply={handleCompletionApplyEntry}
            />
          ) : (
            <EmptyState
              icon={FileCode}
              title={t("pages:editor.noFileSelectedTitle")}
              description={
                readOnly
                  ? t("pages:editor.noFileSelectedViewerHint")
                  : t("pages:editor.noFileSelectedHint")
              }
              className="h-full justify-center border border-dashed rounded-lg"
            />
          )}
        </main>

        {/* 右栏：编译结果 / PDF 预览联动属阶段四，本阶段为占位骨架 */}
        <aside className="hidden w-80 shrink-0 min-h-0 xl:block">
          <EditorPlaceholderPanel />
        </aside>
      </div>

      {/* 切换文件放弃脏改动确认 */}
      <ConfirmDialog
        open={switchConfirmPath !== null}
        onOpenChange={(open) => {
          if (!open) setSwitchConfirmPath(null);
        }}
        title={t("pages:editor.switchDirtyTitle")}
        description={t("pages:editor.switchDirtyDescription")}
        confirmText={t("pages:editor.switchDirtyConfirm")}
        variant="destructive"
        onConfirm={async () => {
          const target = switchConfirmPath;
          setSwitchConfirmPath(null);
          if (target) await doOpenFile(target);
        }}
      />

      {/* 409 冲突弹窗 */}
      <ConflictDialog
        open={session.conflict !== null}
        onOpenChange={(open) => {
          if (!open) setConflict(null);
        }}
        conflict={session.conflict}
        localContent={session.content}
        onDiscardAndReload={handleDiscardAndReload}
      />

      {/* 命令面板（Ctrl/Cmd+/ 或工具栏按钮；viewer 仅浏览不可插入） */}
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        readOnly={readOnly}
        onInsert={handleInsertEntry}
      />
    </div>
  );
}
