/**
 * EditorPage —— /editor 全宽三栏布局（editor-plan §4）。
 *
 * 布局：左栏复用 ProjectExplorer 文件树（点击打开文件）· 中栏 LaTeXEditor
 * （CM6 打开-编辑-保存-冲突闭环）· 右栏 Tab 切换「编译与预览 ↔ AI 助手」，
 * 可折叠（阶段四）。桌面优先。
 *
 * 闭环职责：
 *   - 打开：GET file-content → 缓冲区 + baseMd5 基线；超约 2MB 拒绝打开；
 *   - 编辑：onChange 回写 editorSession 并置脏标记；
 *   - 保存：Ctrl/Cmd+S（window 级统一拦截，编辑器内外行为一致）与工具栏
 *     按钮 → save-file 乐观锁落盘，成功更新 baseMd5、清脏标记；
 *   - 冲突：409 → ConflictDialog（放弃本地重载 / 本地内容剪贴板逃生）；
 *   - 编译联动（§8 / 决策 Q18）：有脏改动时先自动保存，失败中断编译进入
 *     冲突流程；右栏展示编译结果并复用 PDFPreview 预览 PDF；
 *   - AI 助手（§7）：建议应用只改 CM6 缓冲区并置脏标记，落盘走保存路径；
 *   - 权限：viewer 可进入但只读（禁用保存与后端撤销），助手仅问答模式；
 *     判断项目已打开一律用 useIsOpened()（多用户看 projectId）；
 *   - 功能开关：editor.enabled=false 时路由守卫提示（决策 Q17）；
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
import PageHeader from "@/components/PageHeader";
import ProjectExplorer from "@/components/ProjectExplorer";
import LaTeXEditor from "@/components/editor/LaTeXEditor";
import EditorToolbar from "@/components/editor/EditorToolbar";
import ConflictDialog from "@/components/editor/ConflictDialog";
import CommandPalette from "@/components/editor/CommandPalette";
import FilePreviewOverlay from "@/components/editor/FilePreviewOverlay";
import EditorRightPanel from "@/components/editor/EditorRightPanel";
import FullscreenPDFPreview from "@/components/editor/FullscreenPDFPreview";

import FileCode from "lucide-react/dist/esm/icons/file-code.mjs";

import useStore from "@/store/useStore";
import useEditorStore from "@/store/editorStore";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import useIsOpened from "@/hooks/useIsOpened";
import useCanWrite from "@/hooks/useCanWrite";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import useEditorBuffer from "@/hooks/useEditorBuffer";
import useEditorCompile from "@/hooks/useEditorCompile";
import { translateError } from "@/utils/errorMessages";
import { isEditableFilePath, isPreviewableFilePath } from "@/lib/editor/files";
import { expandEntryInsert, insertSnippet } from "@/lib/editor/snippetInsert";
import { applyUnifiedDiff } from "@/lib/editor/applyDiff";
import {
  buildPackageInsertion,
  isPackageMissing,
} from "@/lib/editor/preambleScan";
import { getCommandZhDisplay } from "@/lib/editor/commandZh";
import i18n from "@/i18n";

/**
 * SessionEditor —— 缓冲区内容订阅隔离层（A2 按键渲染解耦）。
 *
 * content / softWrap / fontSize 的组件级订阅收拢到这一层：每次按键只有
 * 本组件与 CM6 内部（value 与文档恒等，dispatch 直接跳过）参与渲染，
 * 页面骨架、文件树、工具栏、右栏不再随键入重渲染。外部整缓冲区替换
 * （打开文件 / 放弃重载 / 后端撤销刷新）仍经 store 单向流入，受控协议
 * 与 LaTeXEditor 保持不变。
 */
function SessionEditor({ readOnly, onCreateEditor, onCompletionApply }) {
  const content = useEditorStore((s) => s.content);
  const softWrap = useEditorStore((s) => s.softWrap);
  const fontSize = useEditorStore((s) => s.fontSize);
  const setBufferContent = useEditorStore((s) => s.setBufferContent);
  return (
    <LaTeXEditor
      value={content}
      onChange={setBufferContent}
      softWrap={softWrap}
      fontSize={fontSize}
      readOnly={readOnly}
      onCreateEditor={onCreateEditor}
      onCompletionApply={onCompletionApply}
    />
  );
}

export default function EditorPage() {
  const { t } = useTranslation(["pages", "components", "common"]);
  const [searchParams, setSearchParams] = useSearchParams();

  const workspaceId = useStore((s) => s.workspaceId);
  const fileTree = useStore((s) => s.fileTree);
  const hydrated = useStore((s) => s.hydrated);
  const mainFile = useStore((s) => s.mainFile);
  const isOpened = useIsOpened();
  const canWrite = useCanWrite();
  const readOnly = !canWrite;
  const enabledFeatures = useEnabledFeatures();

  const editorDisabled =
    hydrated && enabledFeatures !== null && !enabledFeatures.editor;
  const assistantEnabled =
    enabledFeatures === null
      ? true
      : enabledFeatures.editor_assistant !== false;

  const filePath = useEditorStore((s) => s.filePath);
  const conflict = useEditorStore((s) => s.conflict);

  // ── 只读预览状态（PDF/位图覆盖层，不进入 CM6 缓冲区） ─────────────
  const [filePreviewPath, setFilePreviewPath] = useState(null);
  const [filePreviewUrl, setFilePreviewUrl] = useState(null);
  const filePreviewUrlRef = useRef(null);

  // ── 右栏状态 ─────────────────────────────────────────────────────
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [panelTab, setPanelTab] = useState("compile");
  const [previewFullscreen, setPreviewFullscreen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ── CM6 视图实例 ────────────────────────────────────────────────
  const editorViewRef = useRef(null);

  /* ── 缓冲区管理（打开 / 保存 / 冲突 / 自动保存 / 刷新恢复） ──── */
  const {
    opening,
    saving,
    switchConfirmPath,
    setSwitchConfirmPath,
    doOpenFile,
    requestOpenFile,
    doSave,
    saveRef,
    handleOverwriteSave,
    handleReloadBuffer,
    handleDiscardAndReload,
    handleSelectTreeNode,
    conflictSnapshotRef,
  } = useEditorBuffer({
    workspaceId,
    canWrite,
    setSearchParams,
    t,
    setFilePreviewPath,
    setFilePreviewUrl,
  });

  /* ── 只读预览：按路径拉取 blob URL，切换/关闭/卸载时释放 ──────── */
  useEffect(() => {
    if (!filePreviewPath) return;
    let cancelled = false;
    (async () => {
      try {
        const url = await api.project.rawFileBlobUrl(
          filePreviewPath,
          workspaceId,
        );
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        filePreviewUrlRef.current = url;
        setFilePreviewUrl(url);
      } catch (e) {
        if (cancelled) return;
        setFilePreviewPath(null);
        // 文案走模块级 i18n.t：t 在语言切换时换身份会把本 effect 整体
        // 重跑（revoke 当前 blob 并重新拉取同一文件），此处无需响应语言
        toast.error(i18n.t("pages:editor.openFailed"), {
          description: translateError({ error: e.code, message: e.message }),
        });
      }
    })();
    return () => {
      cancelled = true;
      if (filePreviewUrlRef.current) {
        URL.revokeObjectURL(filePreviewUrlRef.current);
        filePreviewUrlRef.current = null;
      }
      setFilePreviewUrl(null);
    };
  }, [filePreviewPath, workspaceId]);

  const closeFilePreview = useCallback(() => setFilePreviewPath(null), []);

  /* ── URL ?file= 参数驱动打开 ──────────────────────────────────── */
  const requestedFile = searchParams.get("file");
  useEffect(() => {
    if (!isOpened || fileTree.length === 0 || !requestedFile) return;
    if (isPreviewableFilePath(requestedFile)) {
      setFilePreviewPath(requestedFile);
      return;
    }
    if (!isEditableFilePath(requestedFile)) {
      toast.error(t("pages:editor.unsupportedType", { path: requestedFile }));
      return;
    }
    const cur = useEditorStore.getState();
    if (cur.filePath === requestedFile) return;
    if (cur.dirty) {
      setSwitchConfirmPath(requestedFile);
      return;
    }
    doOpenFile(requestedFile);
  }, [requestedFile, isOpened, fileTree.length, doOpenFile, t]);

  /* ── 编译联动 ──────────────────────────────────────────────── */
  const { compiling, compileResult, pdfUrl, handleCompile } = useEditorCompile({
    saveRef,
    mainFile,
    t,
    workspaceId,
    setPanelCollapsed,
    setPanelTab,
    onCompileSuccess: useCallback(
      () => toast.success(t("pages:workspace.compileSuccessToast")),
      [t],
    ),
    onCompileError: useCallback(
      (data) => {
        const reason =
          data.error_code || data.explanation
            ? translateError({
                error: data.error_code,
                message: data.explanation,
              })
            : "";
        toast.error(t("pages:workspace.compileFailedToast"), {
          description: reason || t("pages:workspace.compileFailedToast"),
        });
      },
      [t],
    ),
  });

  // 全屏预览 Escape 退出
  useEffect(() => {
    if (!previewFullscreen) return;
    const onKeyDown = (e) => {
      if (e.key === "Escape") setPreviewFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [previewFullscreen]);

  /* ── 命令面板 Ctrl/Cmd+/ ──────────────────────────────────── */
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

  /* ── 宏包缺失联动 ──────────────────────────────────────────── */
  const addPackageToPreamble = useCallback(
    (pkg) => {
      const view = editorViewRef.current;
      if (!view || view.destroyed || !pkg) return;
      const insertion = buildPackageInsertion(
        useEditorStore.getState().content,
        pkg,
      );
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
    async (entry) => {
      const pkg = entry?.pkg;
      if (!pkg || !canWrite) return;
      const description = `\\usepackage{${pkg}}`;
      const displayZh = await getCommandZhDisplay(i18n.language);
      const zhLabel = displayZh ? displayZh(entry.zh ?? "") : "";
      toast.warning(
        t("components:commandPalette.pkgMissing", {
          name: zhLabel || `\\${entry.name}`,
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

  const handleInsertEntry = useCallback(
    (entry, size) => {
      if (!canWrite) return false;
      const cur = useEditorStore.getState();
      const view = editorViewRef.current;
      if (!cur.filePath || !view || view.destroyed) {
        toast.info(t("components:commandPalette.noFileHint"));
        return false;
      }
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

  /* ── AI 助手：上下文快照与建议应用 ──────────────────────────── */
  const getEditorContext = useCallback(() => {
    const cur = useEditorStore.getState();
    const view = editorViewRef.current;
    let cursorLine = null;
    let selection = "";
    if (view && !view.destroyed) {
      const sel = view.state.selection.main;
      cursorLine = view.state.doc.lineAt(sel.head).number;
      selection = sel.empty ? "" : view.state.sliceDoc(sel.from, sel.to);
    }
    return {
      filePath: cur.filePath ?? "",
      buffer: cur.content ?? "",
      cursorLine,
      selection,
    };
  }, []);

  const handleApplySnippet = useCallback((text, { replace = false } = {}) => {
    const view = editorViewRef.current;
    if (!view || view.destroyed || !text) return false;
    const sel = view.state.selection.main;
    view.dispatch({
      changes: {
        from: sel.from,
        to: replace ? sel.to : sel.from,
        insert: text,
      },
      selection: { anchor: sel.from + text.length },
      scrollIntoView: true,
    });
    view.focus();
    return true;
  }, []);

  const handleApplyDiffText = useCallback(
    (diffText) => {
      const view = editorViewRef.current;
      if (!view || view.destroyed) return false;
      const result = applyUnifiedDiff(
        useEditorStore.getState().content,
        diffText,
      );
      if (!result.ok) {
        const reasonKeys = {
          hunk_not_matched: "components:assistant.diffHunkNotMatched",
          hunk_ambiguous: "components:assistant.diffHunkAmbiguous",
          hunk_overlap: "components:assistant.diffHunkOverlap",
        };
        toast.error(t("components:assistant.diffApplyFailed"), {
          description: t(
            reasonKeys[result.reason] ?? "components:assistant.diffMalformed",
          ),
        });
        return false;
      }
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: result.result },
        scrollIntoView: true,
      });
      toast.success(t("components:assistant.diffApplied"));
      return true;
    },
    [t],
  );

  const handleCompletionApplyEntry = useCallback(
    (entry) => {
      if (!canWrite || !entry?.pkg) return;
      if (isPackageMissing(useEditorStore.getState().content, entry.pkg)) {
        notifyMissingPackage(entry);
      }
    },
    [canWrite, notifyMissingPackage],
  );

  /* ── 守卫 ──────────────────────────────────────────────────── */
  if (hydrated && !isOpened) {
    return (
      <div className="min-h-screen p-8 animate-tab-enter">
        <div className="max-w-6xl mx-auto space-y-4">
          <PageHeader
            title={t("pages:editor.title")}
            groupKey="aiAssist"
            className="mb-0"
          />
          <EmptyState
            icon={FileCode}
            title={t("pages:editor.noProjectTitle")}
            description={
              isMultiUser()
                ? t("pages:editor.noProjectDescriptionMultiUser")
                : t("pages:editor.noProjectDescription")
            }
          >
            <Button
              nativeButton={false}
              render={<Link to={isMultiUser() ? "/projects" : "/workspace"} />}
            >
              {isMultiUser()
                ? t("common:openProjects")
                : t("pages:editor.goWorkspace")}
            </Button>
          </EmptyState>
        </div>
      </div>
    );
  }

  if (editorDisabled) {
    return (
      <div className="min-h-screen p-8 animate-tab-enter">
        <div className="max-w-6xl mx-auto space-y-4">
          <PageHeader
            title={t("pages:editor.title")}
            groupKey="aiAssist"
            className="mb-0"
          />
          <EmptyState
            icon={FileCode}
            title={t("pages:editor.disabledTitle")}
            description={t("pages:editor.disabledDescription")}
          >
            <Button
              nativeButton={false}
              render={<Link to={isMultiUser() ? "/projects" : "/workspace"} />}
            >
              {isMultiUser()
                ? t("common:openProjects")
                : t("pages:editor.goWorkspace")}
            </Button>
          </EmptyState>
        </div>
      </div>
    );
  }

  /* ── 主布局 ──────────────────────────────────────────────────── */
  return (
    <div className="h-screen flex flex-col gap-3 p-4 pb-6 animate-tab-enter">
      <PageHeader
        title={t("pages:editor.title")}
        groupKey="aiAssist"
        className="mb-1 shrink-0"
      />

      <EditorToolbar
        filePath={filePath}
        saving={saving}
        readOnly={readOnly}
        onSave={doSave}
        onReloadBuffer={handleReloadBuffer}
        onOpenPalette={() => setPaletteOpen(true)}
        compileEnabled={
          enabledFeatures === null || enabledFeatures.compile !== false
        }
        compiling={compiling}
        onCompile={handleCompile}
        templateLibraryEnabled={
          enabledFeatures === null || enabledFeatures.template_library !== false
        }
      />

      <div className="flex flex-1 min-h-0 gap-3">
        {/* 左栏：文件树 */}
        <aside className="ml-8 w-64 shrink-0 min-h-0">
          <Card className="flex h-full flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {t("pages:workspace.files")}
              </CardTitle>
            </CardHeader>
            <CardContent className="min-h-0 flex-1 overflow-auto px-4 pb-3 pt-0">
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
          <FilePreviewOverlay
            filePreviewPath={filePreviewPath}
            filePreviewUrl={filePreviewUrl}
            onClose={closeFilePreview}
          />
          {filePath ? (
            <SessionEditor
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

        {/* 右栏：编译与预览 ↔ AI 助手 */}
        <EditorRightPanel
          assistantEnabled={assistantEnabled}
          panelCollapsed={panelCollapsed}
          onCollapse={setPanelCollapsed}
          panelTab={panelTab}
          onTabChange={setPanelTab}
          compiling={compiling}
          compileResult={compileResult}
          pdfUrl={pdfUrl}
          previewFullscreen={previewFullscreen}
          onEnterFullscreen={() => setPreviewFullscreen(true)}
          readOnly={readOnly}
          getEditorContext={getEditorContext}
          onApplySnippet={handleApplySnippet}
          onApplyDiffText={handleApplyDiffText}
        />
      </div>

      {/* 编译预览全屏覆盖层 */}
      {previewFullscreen && pdfUrl && (
        <FullscreenPDFPreview
          url={pdfUrl}
          onClose={() => setPreviewFullscreen(false)}
        />
      )}

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

      {/* 409 冲突弹窗（localContent 由弹窗在复制时刻经 store 现取，
          避免页面订阅 content 导致按键重渲染） */}
      <ConflictDialog
        open={conflict !== null}
        onOpenChange={(open) => {
          if (!open) useEditorStore.getState().setConflict(null);
        }}
        conflict={conflict}
        onDiscardAndReload={handleDiscardAndReload}
        onOverwriteSave={handleOverwriteSave}
      />

      {/* 命令面板 */}
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        readOnly={readOnly}
        onInsert={handleInsertEntry}
      />
    </div>
  );
}
