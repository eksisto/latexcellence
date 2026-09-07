/**
 * EditorPage —— /editor 全宽三栏布局（editor-design §4 + 多缓冲区基座，
 * editor-productivity-design §5.1）。
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
 *   - 大纲（阶段三 / Q6）：左栏「大纲」tab 解析激活缓冲区，编辑防抖
 *     300ms 重扫、切换 tab 立即重扫，点击条目滚动到行首并高亮；
 *   - 跨文件大纲（editor v3 阶段四 / Q10）：大纲面板增加当前文件 /
 *     本项目双视图，本项目视图按文件分组、激活文件置顶展开，点击条目
 *     复用错误跳转的开 tab + 行定位通路；公式固定预览卡（Q11）在编辑区
 *     底部、状态栏上方跟随光标渲染所在公式，无公式 / 渲染失败静默隐藏；
 *   - 刷新/重启恢复：localStorage 恢复全部 tab（含脏缓冲区，阶段八升级）+ toast；
 *     URL ?file= 指定者激活（恢复集合与 URL 参数合并）；
 *   - 编译联动（Q16）：编译前依次保存所有脏 tab，任一失败中断并切换
 *     到归属 tab 进入冲突/陈旧流程；
 *   - 编译错误跳转（阶段七 / Q10+Q19）：编译失败时右栏展示可点击错误
 *     列表（logFileStack 纯模块解析日志 `(./` 文件栈归属 `l.N` 错误到
 *     文件），点击联动打开/激活对应 tab 并滚动行首高亮；归属歧义回落
 *     发起编译的文件并标低置信角标；
 *   - 一键格式化（阶段五 / Q8）：工具栏按钮 + Ctrl/Cmd+Shift+F 调后端
 *     format-latex 纯转换端点，结果直接替换发起 tab 缓冲区并置脏
 *     （单事务整文档替换，Ctrl+Z 一步撤销；落盘仍走 save-file），
 *     viewer 禁用；
 *   - AI 助手（§7 / Q17）：建议卡片携带发送时来源 filePath，活跃 tab
 *     不匹配时禁用应用并提示切换；
 *   - 权限：viewer 可进入但只读（禁用保存与后端撤销），助手仅问答模式；
 *     判断项目已打开一律用 useIsOpened()（多用户看 projectId）；
 *   - 功能开关：editor.enabled=false 时路由守卫提示（决策 Q17）；
 *   - 刷新直达恢复：复用模块页恢复语义（多用户 openMultiUserProject
 *     preserveActiveFile，单用户 api.project.open），仅当 fileTree 为空时执行。
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
import FormulaPreviewCard from "@/components/editor/FormulaPreviewCard";
import EditorStatusBar from "@/components/editor/EditorStatusBar";
import CreateFileDialog from "@/components/editor/CreateFileDialog";

import { EditorView } from "@codemirror/view";
import FileCode from "lucide-react/dist/esm/icons/file-code.mjs";

/** 正向同步光标防抖（editor v3 决策 Q6：约 800ms 停顿才发查询） */
const SYNCTEX_FORWARD_DEBOUNCE_MS = 800;

import useStore from "@/store/useStore";
import useEditorStore, {
  activeBuffer,
  activeFilePath,
  activeOpenedBuffer,
  activeSavedBuffer,
  findTab,
} from "@/store/editorStore";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import useIsOpened from "@/hooks/useIsOpened";
import useCanWrite from "@/hooks/useCanWrite";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import useEditorBuffer from "@/hooks/useEditorBuffer";
import useEditorCompile from "@/hooks/useEditorCompile";
import useEditorFormat from "@/hooks/useEditorFormat";
import { isFormatShortcut } from "@/lib/editor/formatApply";
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
import { createStatsHub } from "@/lib/editor/stats";
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
function SessionEditor({
  readOnly,
  statsHub,
  onCreateEditor,
  onCompletionApply,
}) {
  const buffer = useEditorStore(activeBuffer);
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const softWrap = useEditorStore((s) => s.softWrap);
  const fontSize = useEditorStore((s) => s.fontSize);
  const autoCloseEnv = useEditorStore((s) => s.autoCloseEnv);
  const formulaHover = useEditorStore((s) => s.formulaHover);
  // 修改行色条基线（阶段八）：保存基线随 markTabSaved 变化（橙→绿分界），
  // 打开基线仅会话内存态；字符串按值比较，键入期间不会触发重渲染
  const savedText = useEditorStore(activeSavedBuffer);
  const openedText = useEditorStore(activeOpenedBuffer);
  const selectionMatches = useEditorStore((s) => s.selectionMatches);
  // 实时语法诊断（editor v3 阶段三 / 决策 Q9）
  const liveDiagnostics = useEditorStore((s) => s.liveDiagnostics);
  // 代码折叠（editor v3 阶段六b / 决策 Q14）
  const codeFolding = useEditorStore((s) => s.codeFolding);
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
  // 自定义片段读取器（editor v3 阶段二 / 决策 Q8）：补全触发时实时求值，
  // 偏好更新即时生效且引用稳定，无须重建编辑器扩展
  const getCustomSnippets = useCallback(
    () => useEditorStore.getState().customSnippets,
    [],
  );
  return (
    <LaTeXEditor
      value={buffer}
      activeKey={activeTabPath}
      onChange={setBufferContent}
      softWrap={softWrap}
      fontSize={fontSize}
      readOnly={readOnly}
      autoCloseEnv={autoCloseEnv}
      formulaHover={formulaHover}
      savedText={savedText}
      openedText={openedText}
      selectionMatches={selectionMatches}
      liveDiagnostics={liveDiagnostics}
      codeFolding={codeFolding}
      statsHub={statsHub}
      onCreateEditor={onCreateEditor}
      onCompletionApply={onCompletionApply}
      getRefSymbols={getRefSymbols}
      getCustomSnippets={getCustomSnippets}
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

  // ── 左栏双 tab（决策 Q6）：文件树 ↔ 大纲 ─────────────────────────
  const [leftTab, setLeftTab] = useState("files");
  // 大纲面板视图（editor v3 阶段四 / Q10）：当前文件 ↔ 本项目；
  // 状态提升到页面，左栏 tab 切换卸载面板时视图选择不丢失
  const [outlineView, setOutlineView] = useState("file");
  // 文件树新建弹窗：开关 + 目标目录（null = 项目根，由树节点决定）
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [createDialogDir, setCreateDialogDir] = useState(null);
  // 文件树删除确认弹窗的归属路径（null = 弹窗关闭）
  const [deleteConfirmPath, setDeleteConfirmPath] = useState(null);

  // ── CM6 视图实例 ────────────────────────────────────────────────
  const editorViewRef = useRef(null);

  // ── 状态栏统计 hub（阶段八）：LaTeXEditor 发布 / EditorStatusBar 订阅，
  //    统计状态不经过页面 React 树（键入只重渲染状态栏自身） ──────────
  const statsHub = useMemo(() => createStatsHub(), []);

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
    handleCreateFile,
    handleDeleteFile,
    handleSetMainFile,
  } = useEditorBuffer({
    workspaceId,
    canWrite,
    t,
    setFilePreviewPath,
    setFilePreviewUrl,
  });

  /* ── 一键格式化（阶段五 / 决策 Q8）：结果直接替换发起 tab 缓冲区并置
     脏（落盘仍走 save-file 单一路径），Ctrl+Z 一步撤销；viewer 禁用 */
  const { formatting, handleFormat } = useEditorFormat({
    workspaceId,
    t,
    getView: useCallback(() => editorViewRef.current, []),
  });
  // 快捷键经 ref 解引用：handleFormat 依赖 t / workspaceId，直接闭包会
  // 随语言切换 / 项目切换重建监听器（与 Ctrl+S 的 saveRef 同模式）
  const formatRef = useRef(handleFormat);
  useLayoutEffect(() => {
    formatRef.current = handleFormat;
  }, [handleFormat]);

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

  /* ── 文件树删除执行：成功后收起可能正展示该文件的只读预览覆盖层 ──── */
  const handleDeleteConfirmed = useCallback(
    async (filePath) => {
      const ok = await handleDeleteFile(filePath);
      if (ok && filePreviewPath === filePath) closeFilePreview();
      return ok;
    },
    [handleDeleteFile, filePreviewPath, closeFilePreview],
  );

  /* ── URL ?file= 参数驱动打开（打开/激活；与会话恢复集合合并：
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

  /* ── 编译联动（返回值新增 compileTarget，阶段七错误列表低置信回落用） ── */
  const { compiling, compileResult, compileTarget, pdfUrl, handleCompile } =
    useEditorCompile({
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

  /* ── 一键格式化快捷键 Ctrl/Cmd+Shift+F（阶段五 / Q8；阶段二已占用
     Ctrl+B/I/U/K/L、Ctrl+/，CM6 内置查找面板为 Ctrl/Cmd+F——Shift 变体
     无冲突；viewer 只读 / 无激活 tab 时不触发，在途守卫在 hook 内） ── */
  useEffect(() => {
    const handler = (e) => {
      if (!isFormatShortcut(e)) return;
      e.preventDefault();
      if (!canWrite) return;
      if (!useEditorStore.getState().activeTabPath) return;
      formatRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [canWrite]);

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

  /* ── 跨文件「开 tab + 行定位」跳转通路（阶段七 Q10+Q19 编译错误 /
     editor v3 阶段四 Q10 本项目大纲共用）：点击目标 → 目标文件 tab 已
     存在则激活、未打开则打开 → 滚动定位到该行行首并高亮。
     跨 tab 跳转经 pendingErrorJumpRef 延迟到 activeTabPath 换档 effect
     之后（LaTeXEditor 已完成 EditorState 交换）再执行定位；
     低置信条目同样可点（回落 compileTarget 定位），UI 已标低置信角标 */
  const pendingErrorJumpRef = useRef(null);

  const handleJumpToError = useCallback(
    (entry) => {
      if (!entry?.file || !Number.isInteger(entry.line) || entry.line < 1) {
        return;
      }
      const cur = useEditorStore.getState();
      if (cur.activeTabPath === entry.file) {
        handleJumpToLine(entry.line);
        return;
      }
      pendingErrorJumpRef.current = entry;
      if (findTab(cur, entry.file)) {
        cur.activateTab(entry.file);
        return;
      }
      doOpenFile(entry.file).then((ok) => {
        // 打开失败（路径漂移 / 白名单外）：清掉待决跳转，避免迟到误定位
        if (!ok) pendingErrorJumpRef.current = null;
      });
    },
    [handleJumpToLine, doOpenFile],
  );

  useEffect(() => {
    const pending = pendingErrorJumpRef.current;
    if (!pending || !filePath || filePath !== pending.file) return;
    pendingErrorJumpRef.current = null;
    handleJumpToLine(pending.line);
  }, [filePath, handleJumpToLine]);

  /* ── SyncTeX 正反向同步（editor v3 阶段一 / 决策 Q4+Q5+Q6） ─────
     正向：stats hub 光标通道防抖驱动（快照只进 ref，键入零重渲染；
     Q4：缓冲区行号直发，脏缓冲区偏差容忍不补偿；Q6：仅预览可见且有
     编译产物时查询，失败静默，不做偏好开关）。反向：双击取点后查询，
     命中即复用 handleJumpToError 的开 tab + 行定位通路（未打开文件经
     pendingErrorJumpRef 延迟到换档后）。 */
  const [forwardSync, setForwardSync] = useState(null);
  const forwardSeqRef = useRef(0);
  const forwardTimerRef = useRef(null);
  const cursorStatsRef = useRef(null);

  const previewVisible =
    previewFullscreen || (panelTab === "compile" && !panelCollapsed);

  const previewVisibleRef = useRef(previewVisible);
  useLayoutEffect(() => {
    previewVisibleRef.current = previewVisible;
  }, [previewVisible]);

  const pdfUrlRef = useRef(pdfUrl);
  useLayoutEffect(() => {
    pdfUrlRef.current = pdfUrl;
  }, [pdfUrl]);

  const compileTargetRef = useRef(compileTarget);
  useLayoutEffect(() => {
    compileTargetRef.current = compileTarget;
  }, [compileTarget]);

  const requestForwardSync = useCallback(async () => {
    if (!previewVisibleRef.current || !workspaceId || !pdfUrlRef.current)
      return;
    const target = compileTargetRef.current || mainFile;
    const activePath = useEditorStore.getState().activeTabPath;
    const cursor = cursorStatsRef.current;
    // synctex 源文件白名单与编辑器可编辑白名单同口径（.tex/.bib/.sty/.cls）
    if (!target || !activePath || !cursor || !isEditableFilePath(activePath)) {
      return;
    }
    const seq = ++forwardSeqRef.current;
    try {
      const data = await api.compile.synctexForward({
        workspaceId,
        mainFile: target,
        filePath: activePath,
        line: cursor.line,
        col: Math.max(0, (cursor.col ?? 1) - 1), // synctex 0 基列号
      });
      if (seq !== forwardSeqRef.current) return; // 过期响应丢弃
      const records = Array.isArray(data?.records) ? data.records : [];
      setForwardSync({ records });
    } catch {
      // Q6：正向同步是行为无侵入的跟随能力，失败静默不打断写作
    }
  }, [workspaceId, mainFile]);

  // stats hub 订阅（光标通道）：快照进 ref + 尾随防抖约 800ms 发查询
  useEffect(() => {
    const schedule = () => {
      if (forwardTimerRef.current !== null) {
        window.clearTimeout(forwardTimerRef.current);
      }
      forwardTimerRef.current = window.setTimeout(() => {
        forwardTimerRef.current = null;
        requestForwardSync();
      }, SYNCTEX_FORWARD_DEBOUNCE_MS);
    };
    // hub 契约：监听器无参调用，快照须经 getSnapshot() 回读
    // （与 EditorStatusBar 的 useSyncExternalStore 同源约定）
    const unsubscribe = statsHub.subscribe(() => {
      cursorStatsRef.current = statsHub.getSnapshot();
      schedule();
    });
    return () => {
      unsubscribe();
      if (forwardTimerRef.current !== null) {
        window.clearTimeout(forwardTimerRef.current);
        forwardTimerRef.current = null;
      }
    };
  }, [statsHub, requestForwardSync]);

  // 预览变为可见（切回编译 tab / 展开面板 / 进全屏）时立即补发一次
  useEffect(() => {
    if (previewVisible) requestForwardSync();
  }, [previewVisible, requestForwardSync]);

  const handleBackwardSync = useCallback(
    async ({ page, x, y }) => {
      const target = compileTargetRef.current || mainFile;
      if (!workspaceId || !target) return;
      try {
        const data = await api.compile.synctexBackward({
          workspaceId,
          mainFile: target,
          page,
          x,
          y,
        });
        if (data?.found && data.file_path) {
          handleJumpToError({ file: data.file_path, line: data.line });
        }
      } catch (e) {
        toast.error(t("components:editor.synctexFailed"), {
          description: translateError({ error: e.code, message: e.message }),
        });
      }
    },
    [workspaceId, mainFile, handleJumpToError, t],
  );

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
    <div className="h-screen flex flex-col gap-3 pt-8 px-4 pb-6 animate-tab-enter">
      {/* 返回箭头与左栏卡片左缘对齐：卡片左缘 = px-4 + aside ml-8 = 48px，
          PageHeader ml-6 使按钮盒落在 40px，箭头图标（size-8 盒内居中，
          左内边距 8px）左缘恰好重合 48px */}
      <PageHeader
        title={t("pages:editor.title")}
        groupKey="aiAssist"
        className="mb-1 ml-6 shrink-0"
      />

      <EditorToolbar
        filePath={filePath}
        saving={saving}
        readOnly={readOnly}
        onSave={doSaveActive}
        onReloadBuffer={handleReloadBuffer}
        onOpenPalette={() => setPaletteOpen(true)}
        formatting={formatting}
        onFormat={handleFormat}
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
                <OutlinePanel
                  view={outlineView}
                  onViewChange={setOutlineView}
                  onJumpToLine={handleJumpToLine}
                  onJumpToFileLine={handleJumpToError}
                />
              ) : (
                <ProjectExplorer
                  tree={fileTree}
                  onSelect={handleSelectTreeNode}
                  canWrite={canWrite}
                  mainFile={mainFile}
                  onCreateFile={(dirPath) => {
                    setCreateDialogDir(dirPath);
                    setCreateDialogOpen(true);
                  }}
                  onDeleteFile={setDeleteConfirmPath}
                  onSetMainFile={handleSetMainFile}
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
              <div className="flex min-h-0 flex-1 flex-col">
                {/* relative 供公式固定预览卡（editor v3 阶段四 / Q11）在
                    编辑区底部、状态栏上方悬浮 */}
                <div className="relative min-h-0 flex-1">
                  <SessionEditor
                    readOnly={readOnly}
                    statsHub={statsHub}
                    onCreateEditor={handleCreateEditor}
                    onCompletionApply={handleCompletionApplyEntry}
                  />
                  <FormulaPreviewCard hub={statsHub} />
                </div>
                <EditorStatusBar hub={statsHub} />
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
          compileTarget={compileTarget}
          pdfUrl={pdfUrl}
          previewFullscreen={previewFullscreen}
          onEnterFullscreen={() => setPreviewFullscreen(true)}
          onJumpToError={handleJumpToError}
          forwardTarget={forwardSync}
          onBackwardSync={handleBackwardSync}
          readOnly={readOnly}
          getEditorContext={getEditorContext}
          onApplySnippet={handleApplySnippet}
          onApplyDiffText={handleApplyDiffText}
        />
      </div>

      {/* 编译预览全屏覆盖层（正向高亮/反向双击通道同步透传） */}
      {previewFullscreen && pdfUrl && (
        <FullscreenPDFPreview
          url={pdfUrl}
          onClose={() => setPreviewFullscreen(false)}
          forwardTarget={forwardSync}
          onBackwardSync={handleBackwardSync}
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

      {/* 文件树新建文件弹窗：目标目录由触发入口决定（头部按钮 = 项目根，
          目录行 hover 按钮 = 该目录）；成功后自动打开新 tab 并刷新文件树 */}
      <CreateFileDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        dirPath={createDialogDir}
        onCreate={handleCreateFile}
      />

      {/* 文件树删除确认（destructive）：目标存在脏 tab 时追加未保存警示，
          确认后该文件全部 tab 一并关闭 */}
      <ConfirmDialog
        open={deleteConfirmPath !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteConfirmPath(null);
        }}
        title={t("pages:editor.deleteFileTitle")}
        description={
          deleteConfirmPath
            ? [
                t("pages:editor.deleteConfirmDescription", {
                  path: deleteConfirmPath,
                }),
                useEditorStore
                  .getState()
                  .tabs.some(
                    (tab) => tab.filePath === deleteConfirmPath && tab.dirty,
                  )
                  ? t("pages:editor.deleteConfirmDirty")
                  : null,
              ]
                .filter(Boolean)
                .join(" ")
            : ""
        }
        confirmText={t("pages:editor.deleteConfirmProceed")}
        variant="destructive"
        onConfirm={async () => {
          const target = deleteConfirmPath;
          setDeleteConfirmPath(null);
          if (target) await handleDeleteConfirmed(target);
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
