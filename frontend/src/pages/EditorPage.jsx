/**
 * EditorPage —— /editor 全宽三栏布局（editor-plan §4 + 多缓冲区基座，
 * editor-productivity-plan §5.1）。
 *
 * 布局：左栏「文件 / 大纲」双 tab（Q6：文件树点击打开/激活 tab，大纲
 * 展示激活缓冲区分节结构并点击定位）· 中栏顶部
 * tab 条 + LaTeXEditor（单 CM6 实例 + 每文件独立 EditorState 交换）·
 * 右栏 Tab 切换「编译与预览 ↔ AI 助手」，可折叠。桌面优先。
 *
 * 多缓冲区闭环（阶段一）：
 *   - 打开：文件树点击 / URL ?file= → tab 已存在则激活，否则 GET
 *     file-content 新开 tab（缓冲区 + baseMd5 基线；超约 2MB 拒绝打开）；
 *   - 编辑：onChange 回写激活 tab 并置脏标记；
 *   - 保存：Ctrl/Cmd+S（window 级统一拦截）与工具栏按钮 → 激活 tab 经
 *     save-file 乐观锁落盘；陈旧 tab 保存前经用户确认；
 *   - 冲突：409 → 归属 tab 置前 + ConflictDialog（放弃重载 / 剪贴板
 *     逃生 / 覆盖保存三出路）；
 *   - 关闭：脏 tab 三选弹窗（保存并关闭 / 放弃改动 / 取消）；
 *   - 大纲（阶段三 / Q6）：左栏「大纲」tab 仅解析激活缓冲区，编辑防抖
 *     300ms 重扫、切换 tab 立即重扫，点击条目滚动到行首并高亮；
 *   - 刷新恢复：sessionStorage 恢复全部 tab（含脏缓冲区）+ toast；
 *     URL ?file= 指定者激活（恢复集合与 URL 参数合并）；
 *   - 编译联动（Q16）：编译前依次保存所有脏 tab，任一失败中断并切换
 *     到归属 tab 进入冲突/陈旧流程；
 *   - AI 助手（§7 / Q17）：建议卡片携带发送时来源 filePath，活跃 tab
 *     不匹配时禁用应用并提示切换；
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

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import EmptyState from "@/components/EmptyState";
import ConfirmDialog from "@/components/ConfirmDialog";
import PageHeader from "@/components/PageHeader";
import ProjectExplorer from "@/components/ProjectExplorer";
import LaTeXEditor from "@/components/editor/LaTeXEditor";
import EditorToolbar from "@/components/editor/EditorToolbar";
import EditorTabBar from "@/components/editor/EditorTabBar";
import EditorCloseTabDialog from "@/components/editor/EditorCloseTabDialog";
import ConflictDialog from "@/components/editor/ConflictDialog";
import CommandPalette from "@/components/editor/CommandPalette";
import FilePreviewOverlay from "@/components/editor/FilePreviewOverlay";
import EditorRightPanel from "@/components/editor/EditorRightPanel";
import FullscreenPDFPreview from "@/components/editor/FullscreenPDFPreview";
import OutlinePanel from "@/components/editor/OutlinePanel";

import { EditorView } from "@codemirror/view";
import FileCode from "lucide-react/dist/esm/icons/file-code.mjs";

import useStore from "@/store/useStore";
import useEditorStore, {
  activeBuffer,
  activeFilePath,
  findTab,
} from "@/store/editorStore";
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
import { wrapViewSelections, WRAP_CATEGORY } from "@/lib/editor/wrap";
import { applyUnifiedDiff } from "@/lib/editor/applyDiff";
import {
  buildPackageInsertion,
  isPackageMissing,
} from "@/lib/editor/preambleScan";
import {
  projectSymbolCache,
  reduceProjectKey,
} from "@/lib/editor/latexSymbols";
import { getCommandZhDisplay } from "@/lib/editor/commandZh";
import i18n from "@/i18n";

/**
 * SessionEditor —— 缓冲区内容订阅隔离层（A2 按键渲染解耦）。
 *
 * 激活 tab 的 buffer / softWrap / fontSize 的组件级订阅收拢到这一层：
 * 每次按键只有本组件与 CM6 内部（value 与文档恒等，dispatch 直接跳过）
 * 参与渲染，页面骨架、文件树、tab 条、工具栏、右栏不再随键入重渲染。
 * 多缓冲区换档（activeKey 变化时的 EditorState 交换）在 LaTeXEditor
 * 内部完成，激活 tab 的 content 仍经本层单点订阅流入 CM6。
 */
function SessionEditor({ readOnly, onCreateEditor, onCompletionApply }) {
  const buffer = useEditorStore(activeBuffer);
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const softWrap = useEditorStore((s) => s.softWrap);
  const fontSize = useEditorStore((s) => s.fontSize);
  const autoCloseEnv = useEditorStore((s) => s.autoCloseEnv);
  const setBufferContent = useEditorStore((s) => s.setBufferContent);
  // 引用补全（阶段四 / Q7）：项目符号缓存读取器。缓存以项目为键
  // （单用户 projectPath / 多用户 projectId），首次触发补全时经
  // projectSymbolCache 拉取（在途 Promise 共享），保存成功后由
  // useEditorBuffer 失效、项目切换/关闭时清空
  const workspaceId = useStore((s) => s.workspaceId);
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const getRefSymbols = useCallback(() => {
    const projectKey = reduceProjectKey({ projectPath, projectId });
    if (!projectKey || !workspaceId) return Promise.resolve(null);
    return projectSymbolCache.load(projectKey, () =>
      api.project.latexSymbols(workspaceId),
    );
  }, [projectPath, projectId, workspaceId]);
  return (
    <LaTeXEditor
      value={buffer}
      activeKey={activeTabPath}
      onChange={setBufferContent}
      softWrap={softWrap}
      fontSize={fontSize}
      readOnly={readOnly}
      autoCloseEnv={autoCloseEnv}
      onCreateEditor={onCreateEditor}
      onCompletionApply={onCompletionApply}
      getRefSymbols={getRefSymbols}
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

  const filePath = useEditorStore((s) => s.activeTabPath);
  const conflict = useEditorStore((s) => s.conflict);
  // autoSave 偏好（原始布尔）：关闭脏 tab 三选弹窗的默认项（决策 Q5）
  const autoSavePref = useEditorStore((s) => s.autoSave);

  // ── 只读预览状态（PDF/位图覆盖层，不进入 CM6 缓冲区） ─────────────
  const [filePreviewPath, setFilePreviewPath] = useState(null);
  const [filePreviewUrl, setFilePreviewUrl] = useState(null);
  const filePreviewUrlRef = useRef(null);

  // ── 右栏状态 ─────────────────────────────────────────────────────
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [panelTab, setPanelTab] = useState("compile");
  const [previewFullscreen, setPreviewFullscreen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // 命令面板类目过滤（Ctrl+L 打开 wrap 专项浮层，Ctrl/Cmd+/ 打开全量面板）
  const [paletteFilter, setPaletteFilter] = useState(null);

  // ── 左栏双 tab（决策 Q6）：文件树 ↔ 当前激活缓冲区大纲 ────────────
  const [leftTab, setLeftTab] = useState("files");

  // ── CM6 视图实例 ────────────────────────────────────────────────
  const editorViewRef = useRef(null);

  /* ── 缓冲区管理（打开/激活 / 保存 / 冲突 / 自动保存 / 关闭三选 /
     陈旧检测 / 刷新恢复 / 项目切换守卫） ─────────────────────────── */
  const {
    opening,
    saving,
    closingTabPath,
    setClosingTabPath,
    staleConfirmPath,
    setStaleConfirmPath,
    doOpenFile,
    requestCloseTab,
    closeTabImmediate,
    doSaveTab,
    doSaveActive,
    saveAndCloseTab,
    doSaveAllDirty,
    doSaveAllDirtyRef,
    handleOverwriteSave,
    handleReloadBuffer,
    handleDiscardAndReload,
    handleSelectTreeNode,
  } = useEditorBuffer({
    workspaceId,
    canWrite,
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

  /* ── URL ?file= 参数驱动打开（打开/激活；与 sessionStorage 恢复合并：
     恢复全部 tab 集合，?file= 指定者激活，§4） ───────────────────── */
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
    if (cur.activeTabPath === requestedFile) return;
    if (findTab(cur, requestedFile)) {
      cur.activateTab(requestedFile);
      return;
    }
    doOpenFile(requestedFile);
  }, [requestedFile, isOpened, fileTree.length, doOpenFile, t]);

  /* ── URL ?file= 跟随激活 tab（声明在打开 effect 之后：同一 commit 内
     先消费 requestedFile 再回写 URL，避免恢复集合激活位互相覆盖；
     只读预览直达参数在无激活 tab 时保留） ─────────────────────────── */
  useEffect(() => {
    if (!isOpened || fileTree.length === 0) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (filePath) {
          if (next.get("file") !== filePath) next.set("file", filePath);
          return next;
        }
        const current = next.get("file");
        if (current && isPreviewableFilePath(current)) return next;
        if (current) next.delete("file");
        return next;
      },
      { replace: true },
    );
  }, [filePath, isOpened, fileTree.length, setSearchParams]);

  /* ── 编译联动 ──────────────────────────────────────────────── */
  const { compiling, compileResult, pdfUrl, handleCompile } = useEditorCompile({
    doSaveAllDirtyRef,
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

  /* ── 命令面板 Ctrl/Cmd+/（全量） 与包裹菜单 Ctrl+L（wrap 全集浮层） ── */
  useEffect(() => {
    const handler = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "/") {
        e.preventDefault();
        setPaletteFilter(null);
        setPaletteOpen((open) => !open);
      } else if (e.key === "l" || e.key === "L") {
        e.preventDefault();
        setPaletteFilter(WRAP_CATEGORY);
        setPaletteOpen(true);
      }
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
        activeBuffer(useEditorStore.getState()),
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
      if (!activeFilePath(cur) || !view || view.destroyed) {
        toast.info(t("components:commandPalette.noFileHint"));
        return false;
      }
      const contentBefore = activeBuffer(cur);
      // wrap 类目（editor v2 决策 Q13）：面板执行包裹动作——对每个选区
      // 包成 \cmd{内容}，光标处插入空命令并定位到参数内；其余类目走
      // 占位符模板插入
      let ok = true;
      if (entry.category === WRAP_CATEGORY) {
        ok = wrapViewSelections(view, entry.name);
      } else {
        insertSnippet(view, expandEntryInsert(entry, size));
      }
      if (ok && entry.pkg && isPackageMissing(contentBefore, entry.pkg)) {
        notifyMissingPackage(entry);
      }
      return ok;
    },
    [canWrite, notifyMissingPackage, t],
  );

  const handleCreateEditor = useCallback((view) => {
    editorViewRef.current = view;
  }, []);

  /* ── 大纲点击定位（阶段三）：滚动到目标行首并高亮 ────────────────
     selection anchor 置行首使 CM6 既有 cm-activeLine 高亮该行
     （editorBaseTheme 已配主题色），scrollIntoView y:start 滚到视口顶部；
     行号越界（编辑期间条目行漂移）时忽略本次定位，不抛错 */
  const handleJumpToLine = useCallback((line) => {
    const view = editorViewRef.current;
    if (!view || view.destroyed || !Number.isInteger(line) || line < 1) return;
    if (line > view.state.doc.lines) return;
    const target = view.state.doc.line(line);
    view.dispatch({
      selection: { anchor: target.from },
      effects: EditorView.scrollIntoView(target.from, { y: "start" }),
    });
    view.focus();
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
      filePath: activeFilePath(cur) ?? "",
      buffer: activeBuffer(cur),
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
        activeBuffer(useEditorStore.getState()),
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
      if (
        isPackageMissing(activeBuffer(useEditorStore.getState()), entry.pkg)
      ) {
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
        onSave={doSaveActive}
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
        {/* 左栏：文件树 / 大纲双 tab（决策 Q6） */}
        <aside className="ml-8 w-64 shrink-0 min-h-0">
          <Card className="flex h-full flex-col">
            <CardHeader className="pb-2">
              <Tabs value={leftTab} onValueChange={setLeftTab}>
                <TabsList className="w-full">
                  <TabsTrigger value="files" className="w-full">
                    {t("pages:workspace.files")}
                  </TabsTrigger>
                  <TabsTrigger value="outline" className="w-full">
                    {t("components:editor.outline.tab")}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </CardHeader>
            <CardContent className="min-h-0 flex-1 overflow-auto px-2 pb-3 pt-0">
              {leftTab === "outline" ? (
                <OutlinePanel onJumpToLine={handleJumpToLine} />
              ) : (
                <ProjectExplorer
                  tree={fileTree}
                  onSelect={handleSelectTreeNode}
                />
              )}
            </CardContent>
          </Card>
        </aside>

        {/* 中栏：tab 条 + CM6 编辑器 */}
        <main className="relative flex min-h-0 min-w-0 flex-1 flex-col gap-2">
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
            <>
              <EditorTabBar
                onActivateTab={(path) =>
                  useEditorStore.getState().activateTab(path)
                }
                onRequestCloseTab={requestCloseTab}
                onSaveAll={() => doSaveAllDirty()}
              />
              <div className="min-h-0 flex-1">
                <SessionEditor
                  readOnly={readOnly}
                  onCreateEditor={handleCreateEditor}
                  onCompletionApply={handleCompletionApplyEntry}
                />
              </div>
            </>
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

      {/* 陈旧保存确认（决策 Q4）：磁盘已变更的 tab 保存前需用户确认；
          确认后仍走 save-file 乐观锁，磁盘再次变化时 409 兜底 */}
      <ConfirmDialog
        open={staleConfirmPath !== null}
        onOpenChange={(open) => {
          if (!open) setStaleConfirmPath(null);
        }}
        title={t("pages:editor.staleConfirmTitle")}
        description={t("pages:editor.staleConfirmDescription")}
        confirmText={t("pages:editor.staleConfirmProceed")}
        variant="destructive"
        onConfirm={async () => {
          const target = staleConfirmPath;
          setStaleConfirmPath(null);
          if (target) await doSaveTab(target, { confirmedStale: true });
        }}
      />

      {/* 关闭脏 tab 三选弹窗（决策 Q5）：保存并关闭 / 放弃改动 / 取消；
          autoSave 偏好开启时默认「保存并关闭」；「保存并关闭」经
          saveAndCloseTab 兜底——弹窗待决期间已被自动保存落盘的 tab 直接关闭 */}
      <EditorCloseTabDialog
        open={closingTabPath !== null}
        tabPath={closingTabPath}
        autoSaveDefault={autoSavePref}
        onOpenChange={(open) => {
          if (!open) setClosingTabPath(null);
        }}
        onSaveAndClose={saveAndCloseTab}
        onDiscardAndClose={(path) => closeTabImmediate(path)}
      />

      {/* 409 冲突弹窗（动作作用于冲突归属 tab；复制逃生在点击时刻经
          store 现取该 tab 全文，避免页面订阅 content 导致按键重渲染） */}
      <ConflictDialog
        open={conflict !== null}
        onOpenChange={(open) => {
          if (!open) useEditorStore.getState().setConflict(null);
        }}
        conflict={conflict}
        onDiscardAndReload={handleDiscardAndReload}
        onOverwriteSave={handleOverwriteSave}
      />

      {/* 命令面板（categoryFilter 非 null 时作为专项浮层菜单，如 Ctrl+L 的 wrap 全集） */}
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        readOnly={readOnly}
        categoryFilter={paletteFilter}
        onInsert={handleInsertEntry}
      />
    </div>
  );
}
