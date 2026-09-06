/**
 * LaTeXEditor —— CodeMirror 6 受控封装 + 多缓冲区换档（editor-design §9、
 * editor-productivity-design §4/§5.1，阶段一）。
 *
 * 设计要点：
 *   - 受控 value / onChange：激活 tab 的缓冲区由 editorSession 切片持有，
 *     本组件只做单向回写，不内部持有文本状态；
 *   - 多缓冲区（阶段一）：单 CM6 实例 + 每文件独立 EditorState 交换——
 *     tab 切换时把当前视图 state 按 filePath 收进 tabViews 登记表（撤销
 *     历史与选区随之冻结），换入目标 tab 存档；滚动位置不属于
 *     EditorState，由登记表单独记忆并在换回时恢复；无存档的 tab 按
 *     store 缓冲区重建（复刻 @uiw 包装层的扩展配置，含 basicSetup）；
 *   - 换档产生的整文档变更经 switchGuard 吸收，不误标脏标记；
 *   - 自有 update listener：换入/重建的 EditorState 不含 @uiw 包装层内部
 *     listener（其 listener 只存在于包装层创建的初始 state），doc 变更
 *     统一由本 listener 回写 store；与包装层 listener 并存时按「值相同
 *     只写一次」去重，保证每次实际变更恰好一次 store 写入；
 *   - 语言包经 lib/editor/language.js 抽象层引入（决策 Q2；editor v3
 *     阶段六a / 决策 Q14 起按激活 tab 文件后缀分流：.tex/.sty/.cls 走
 *     自研 Lezer 子集语法，.bib 沿用 stex 流式模式），本组件不直接
 *     import 语法实现，保证可整体替换；
 *   - 编辑基本功包（editor v2 阶段二）：包裹直包快捷键（Ctrl/Cmd+B/I/U/K，
 *     逻辑在 lib/editor/wrap.js）+ 多光标（basicSetup
 *     allowMultipleSelections 默认开启，无偏好开关）+ \begin{env} 打字机
 *     补 \end（lib/editor/beginClose.js，autoCloseEnv 偏好经 ref 读取）+
 *     查找替换面板主题化与三语（lib/editor/searchPanel.js，语言切换经
 *     setSearchLanguage 效果热更新）；
 *   - 缓冲区内撤销/重做交给 CM6 自身 history（basicSetup 内置，
 *     Ctrl/Cmd+Z / Shift+Ctrl/Cmd+Z，随 EditorState 按文件隔离），与后端
 *     撤销分属两层（方案 §8）；
 *   - Ctrl/Cmd+S 不在编辑器键映射内绑定：由页面级 window 监听统一拦截
 *     （焦点在编辑器内外行为一致，且避免双重触发），本组件只负责呈现；
 *   - 修改行色条（阶段八，借鉴 Notepad++ Change History）：基线经 props
 *     （savedText/openedText）同步进 changeHistoryField（效果 dispatch，
 *     集合在事务内重算），渲染用行装饰左侧内阴影条，零 gutter 列；
 *   - 底部状态栏统计（阶段八）：光标/选区/行数即时发布、字数尾随防抖，
 *     经 statsHub（发布-订阅）直通 EditorStatusBar，不经过页面 React 树；
 *   - 选中即高亮全部匹配（阶段八，selectionMatches 偏好）：@codemirror/
 *     search 的 highlightSelectionMatches 按偏好挂载（BASIC_SETUP 常驻
 *     false，避免模块级单例随偏好变化），切换经换档失配重建生效到各 tab；
 *   - 实时语法诊断（editor v3 阶段三 / 决策 Q9，liveDiagnostics 偏好）：
 *     @codemirror/lint 的 linter（delay 500ms）随偏好按需挂载，规则实现
 *     在纯模块 lib/editor/diagnostics.js（未闭合环境 error / 花括号失衡
 *     warning，注释、\verb、verbatim、转义与 URL 字面量豁免，200 条上限），
 *     消息经 i18n 文案表三语组装；挂载与失配重建同 selectionMatches 先例；
 *   - 代码折叠（editor v3 阶段六b / 决策 Q14，codeFolding 偏好）：折叠
 *     gutter 与折叠占位符（环境名 / 分节标题摘要）随偏好按需挂载，扩展组
 *     经 language.js 唯一入口取得（latexFoldingExtension，模块级单例）；
 *     折叠区间定义在 latexGrammar 包（环境 / 分节走 foldService、verbatim
 *     走 foldNodeProp），随 Lezer 语言只在 .tex/.sty/.cls 生效，.bib 无
 *     折叠标记零回归；挂载与失配重建同 selectionMatches 先例；
 *   - 高亮配色复用全局 --code-token-* 变量、底色复用主题变量，
 *     亮/暗主题切换零 JS 参与。
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
} from "react";
import CodeMirror, { getDefaultExtensions } from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches } from "@codemirror/search";
import { tags as t } from "@lezer/highlight";

import {
  latexLanguageExtension,
  latexFoldingExtension,
  fileSuffixOf,
} from "@/lib/editor/language";
import { latexCompletionExtension } from "@/lib/editor/latexCompletion";
import { snippetPlaceholderExtension } from "@/lib/editor/snippetInsert";
import { wrapKeymapExtension } from "@/lib/editor/wrap";
import { autoCloseEnvExtension } from "@/lib/editor/beginClose";
import { formulaHoverExtension } from "@/lib/editor/formulaHover";
import { createDiagnosticsLinter } from "@/lib/editor/liveLint";
import { createSearchPanelExtension } from "@/lib/editor/searchPanel";
import {
  changeHistoryExtension,
  getChangeBaselines,
  setOpenedTextEffect,
  setSavedTextEffect,
} from "@/lib/editor/changeHistory";
import { countDocStats } from "@/lib/editor/stats";
import tabViewRegistry from "@/lib/editor/tabViews";
import i18n from "@/i18n";
import { cn } from "@/lib/utils";

/** 亮/暗主题自适应基础主题（全部走 CSS 变量） */
const editorBaseTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "var(--background)",
    color: "var(--foreground)",
  },
  // 复刻 @uiw 包装层 scrollerTheme（未导出）：换入自建 EditorState 时
  // 滚动容器高度规则不缺位
  "& .cm-scroller": {
    height: "100%",
    fontFamily:
      "var(--font-mono), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    lineHeight: "1.75",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "var(--muted-foreground)",
    borderRight: "1px solid var(--border)",
  },
  ".cm-activeLine": {
    backgroundColor: "color-mix(in oklab, var(--muted) 55%, transparent)",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "color-mix(in oklab, var(--muted) 55%, transparent)",
  },
  // 选区跟随主题 --selection-bg（与全站 ::selection 同源同色，随主题切换）；
  // 回退值保留旧 primary 混色，防止个别主题漏定义该变量时选区不可见。
  // 注意：必须用深链选择器压过 CM 基础主题聚焦态的 5 类特异性规则
  // （&light.cm-focused > .cm-scroller > .cm-selectionLayer …，默认
  // 淡紫 #d7d4f0），否则编辑器聚焦时主题选区色会被它覆盖回紫色
  ".cm-selectionBackground": {
    backgroundColor:
      "var(--selection-bg, color-mix(in oklab, var(--primary) 22%, transparent))",
  },
  "&.cm-focused .cm-scroller .cm-selectionLayer .cm-selectionBackground": {
    backgroundColor:
      "var(--selection-bg, color-mix(in oklab, var(--primary) 22%, transparent))",
  },
  ".cm-cursor": { borderLeftColor: "var(--foreground)" },
});

/** LaTeX 语法高亮：token 颜色对齐 CodeBlock 的 --code-token-* 变量；
    heading（分节命令 tag，editor v3 阶段六a）映射到与 tagName 相同的
    --code-token-title，与 stex 分节命令的视觉表现保持一致 */
const latexHighlightStyle = HighlightStyle.define([
  { tag: t.comment, color: "var(--code-token-comment)" },
  { tag: t.keyword, color: "var(--code-token-keyword)" },
  { tag: t.atom, color: "var(--code-token-built-in)" },
  { tag: t.number, color: "var(--code-token-number)" },
  { tag: t.special(t.variableName), color: "var(--code-token-string)" },
  { tag: t.tagName, color: "var(--code-token-title)" },
  { tag: t.heading, color: "var(--code-token-title)" },
  { tag: t.bracket, color: "var(--code-token-punctuation)" },
  { tag: t.invalid, color: "var(--destructive)" },
]);

/**
 * basicSetup 覆盖项必须保持模块级单例：@uiw/react-codemirror 把该 prop
 * 放进 reconfigure effect 的依赖数组，内联字面量每次渲染新身份，而本
 * 组件随每次键入重渲染 —— 等于每敲一个键触发一次 CM6 全量扩展重配置。
 */
const BASIC_SETUP = {
  foldGutter: false,
  highlightSelectionMatches: false,
  // 多光标默认开启（editor v2 §4 决策：无偏好开关；Alt+Click 等默认行为可用）
  allowMultipleSelections: true,
};

/** 直包快捷键映射（模块级单例，同 basicSetup 的稳定性要求） */
const WRAP_KEYMAP = wrapKeymapExtension();

/** 查找替换面板扩展 + 语言热更新效果（翻译函数闭包 i18n，语言无关键值） */
const SEARCH_PANEL = createSearchPanelExtension({
  getLanguage: () => i18n.language,
  translate: (key) => i18n.t(key),
});

/** 修改行色条扩展组（模块级单例：字段/插件对象稳定，重配置零副作用） */
const CHANGE_HISTORY = changeHistoryExtension();

/** 选中即高亮全部匹配（模块级单例；按 selectionMatches 偏好条件挂载） */
const SELECTION_MATCHES = highlightSelectionMatches({
  minSelectionLength: 2,
  maxMatches: 100,
});

/** 实时语法诊断（editor v3 阶段三 / 决策 Q9；模块级单例条件挂载，同
    SELECTION_MATCHES 先例：liveDiagnostics 偏好开 → 挂载，关 → 摘除，
    tab 存档 config 失配重建生效到各 tab；文案 i18n 每次诊断运行时求值） */
const DIAGNOSTICS_LINTER = createDiagnosticsLinter({
  translate: (key, params) => i18n.t(key, params),
});

/** 代码折叠（editor v3 阶段六b / 决策 Q14；模块级单例条件挂载，同
    liveDiagnostics 先例：codeFolding 偏好开 → 挂载 foldGutter + 折叠
    占位符，关 → 摘除；折叠区间随 Lezer 语言仅 .tex/.sty/.cls 生效） */
const FOLDING_EXTENSIONS = latexFoldingExtension();

/** 状态栏字数统计的尾随防抖（大文档逐键全量统计不可取） */
const DOC_STATS_DEBOUNCE_MS = 250;

/**
 * @param {object} props
 * @param {string} props.value                 激活 tab 的缓冲区全文
 * @param {string|null} props.activeKey        激活 tab 的 filePath（换档键）
 * @param {(value: string) => void} props.onChange
 * @param {boolean} [props.softWrap=true]      软换行（默认开启）
 * @param {number} [props.fontSize=14]         字号 px
 * @param {boolean} [props.readOnly=false]     viewer 只读
 * @param {boolean} [props.autoCloseEnv=true]  \begin{env} 自动补 \end 开关
 *   （autoCloseEnv 偏好；经 ref 读取，切换无须重配置扩展）
 * @param {boolean} [props.formulaHover=true]  KaTeX 公式悬停预览开关
 *   （formulaHover 偏好，阶段六；经 ref 读取，切换无须重配置扩展）
 * @param {string|null} [props.savedText=null] 激活 tab 的保存内容基线
 *   （tab.savedBuffer，阶段八色条橙基线；null = 无基线不显示色条）
 * @param {string|null} [props.openedText=null] 激活 tab 的打开内容基线
 *   （tab.openedBuffer，阶段八色条绿基线）
 * @param {boolean} [props.selectionMatches=true] 选中即高亮全部匹配
 *   （阶段八偏好；换档失配重建生效到各存档 tab）
 * @param {boolean} [props.liveDiagnostics=true] 实时语法诊断
 *   （editor v3 阶段三 / 决策 Q9 偏好；换档失配重建生效到各存档 tab）
 * @param {boolean} [props.codeFolding=true] 代码折叠
 *   （editor v3 阶段六b / 决策 Q14 偏好；换档失配重建生效到各存档 tab）
 * @param {object|null} [props.statsHub=null]  状态栏统计 hub
 *   （lib/editor/stats.js 发布-订阅；缺省不发布）
 * @param {(view: EditorView) => void} [props.onCreateEditor]
 *   CM6 视图实例回调（命令面板等外部插入入口经此持有视图）
 * @param {(entry: object) => void} [props.onCompletionApply]
 *   行内补全应用后回调（宏包缺失联动）
 * @param {() => Promise<object|null>} [props.getRefSymbols]
 *   引用补全（\ref/\label/\cite，阶段四）的项目符号数据读取器；
 *   缺省时不挂引用 source
 * @param {() => Array} [props.getCustomSnippets]
 *   自定义片段读取器（editor v3 阶段二 / 决策 Q8）；每次补全触发时求值，
 *   偏好更新即时生效，无须重建扩展
 */
export default function LaTeXEditor({
  value,
  activeKey,
  onChange,
  softWrap = true,
  fontSize = 14,
  readOnly = false,
  autoCloseEnv = true,
  formulaHover = true,
  savedText = null,
  openedText = null,
  selectionMatches = true,
  liveDiagnostics = true,
  codeFolding = true,
  statsHub = null,
  onCreateEditor,
  onCompletionApply,
  getRefSymbols,
  getCustomSnippets,
}) {
  const viewRef = useRef(null);
  /** 当前装载在视图中的 tab（null = 尚未完成首次装载） */
  const currentKeyRef = useRef(null);
  /** 换档/重建期间吸收 doc 变更回写（防止程序性整文档替换误标脏） */
  const switchGuardRef = useRef(false);
  /** 最近一次已回写 store 的文档全文（listener 双路通知去重） */
  const lastSyncedRef = useRef(null);

  // onChange 经 ref 解引用：EditorState 内嵌的 update listener 在创建时
  // 闭包，直接捕获会过期；经 ref 永远调用最新回调。
  // 写 ref 用 useLayoutEffect 对齐（不用 render 体直写，规避并发渲染
  // 把"未来值"提前写入的既有仓库约定）
  const onChangeRef = useRef(onChange);
  useLayoutEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  // autoCloseEnv 偏好经 ref 供事务过滤器在打字时刻读取：偏好切换只改
  // ref，不进入扩展依赖数组，避免每次切换触发 CM6 全量重配置
  const autoCloseEnvRef = useRef(autoCloseEnv);
  useLayoutEffect(() => {
    autoCloseEnvRef.current = autoCloseEnv;
  }, [autoCloseEnv]);

  // formulaHover 偏好经 ref 供 hoverTooltip 回调在悬停时刻读取：偏好切换
  // 只改 ref，不进入扩展依赖数组，避免每次切换触发 CM6 全量重配置
  const formulaHoverRef = useRef(formulaHover);
  useLayoutEffect(() => {
    formulaHoverRef.current = formulaHover;
  }, [formulaHover]);

  // statsHub 经 ref 供扩展内 update listener 在事务时刻读取（hub 身份
  // 稳定，防御调用方未来改为动态创建时触发扩展重配置）
  const statsHubRef = useRef(statsHub);
  useLayoutEffect(() => {
    statsHubRef.current = statsHub;
  }, [statsHub]);

  /** 文档统计缓存：字数经尾随防抖刷新，光标/行数发布时即时取值 */
  const docWordsCacheRef = useRef({ words: 0 });
  const docStatsTimerRef = useRef(null);

  /** 发布一次状态栏快照（全数值，hub 内浅比较去重） */
  const publishStats = useCallback((state) => {
    const hub = statsHubRef.current;
    if (!hub) return;
    const sel = state.selection.main;
    const headLine = state.doc.lineAt(sel.head);
    hub.publish({
      line: headLine.number,
      col: sel.head - headLine.from + 1,
      selectedChars: sel.to - sel.from,
      lines: state.doc.lines,
      words: docWordsCacheRef.current.words,
    });
  }, []);

  /** 字数尾随防抖：停改 250ms 后全量统计并补发一次快照 */
  const scheduleDocStats = useCallback(() => {
    if (docStatsTimerRef.current !== null) return;
    docStatsTimerRef.current = window.setTimeout(() => {
      docStatsTimerRef.current = null;
      const view = viewRef.current;
      if (!view || view.destroyed) return;
      docWordsCacheRef.current = {
        words: countDocStats(view.state.doc.toString()).totalWords,
      };
      publishStats(view.state);
    }, DOC_STATS_DEBOUNCE_MS);
  }, [publishStats]);

  // 卸载清理防抖定时器
  useEffect(
    () => () => {
      if (docStatsTimerRef.current !== null) {
        window.clearTimeout(docStatsTimerRef.current);
      }
    },
    [],
  );

  const handleDocChange = useCallback((nextValue) => {
    if (switchGuardRef.current) return;
    // 去重：@uiw 包装层 listener 与自有 listener 对同一事务都会通知，
    // 值相同只写一次（不影响连续两次真实变更——值必然不同）
    if (nextValue === lastSyncedRef.current) return;
    lastSyncedRef.current = nextValue;
    onChangeRef.current(nextValue);
  }, []);

  // 语言按激活 tab 文件后缀分流（editor v3 阶段六a / 决策 Q14）：.tex/
  // .sty/.cls 走自研 Lezer 子集语法，.bib 沿用 stex 流式模式。后缀仅随
  // activeKey 变化，进入依赖数组不会在键入期间触发扩展重配置
  const activeSuffix = fileSuffixOf(activeKey);

  const extensions = useMemo(() => {
    const base = [
      latexLanguageExtension(activeSuffix),
      syntaxHighlighting(latexHighlightStyle),
      editorBaseTheme,
      // 阶段三：`\` / `\begin{` 行内补全 + 模板占位符跳转模式；
      // getLanguage 供中文名 detail 按界面语言本地化（每次触发时求值）；
      // 阶段四：getRefSymbols 供 \ref/\label/\cite 引用补全取项目符号；
      // editor v3 阶段二：getCustomSnippets 供自定义片段合并进匹配索引
      latexCompletionExtension({
        onApplyEntry: onCompletionApply,
        getLanguage: () => i18n.language,
        getRefSymbols,
        getCustomSnippets,
      }),
      snippetPlaceholderExtension(),
      // 包裹命令直包快捷键（Ctrl/Cmd+B/I/U/K，editor v2 决策 Q13）
      WRAP_KEYMAP,
      // \begin{env} 打字机补 \end（editor v2 决策 Q12；开关经 ref 实时读取）
      autoCloseEnvExtension({ isEnabled: () => autoCloseEnvRef.current }),
      // KaTeX 公式悬停预览（editor v2 阶段六 / 决策 Q9；开关经 ref 实时
      // 读取，katex 动态分块 import 首屏零加载）
      formulaHoverExtension({
        isEnabled: () => formulaHoverRef.current,
        translate: (key) => i18n.t(key),
      }),
      // 查找替换面板主题化 + 面板文案三语（editor v2 阶段二）
      SEARCH_PANEL.extension,
      // 修改行色条（阶段八）：基线经 setSavedText/setOpenedText 效果注入
      CHANGE_HISTORY,
      // 选中即高亮全部匹配（阶段八偏好；模块级单例条件挂载）
      ...(selectionMatches ? [SELECTION_MATCHES] : []),
      // 实时语法诊断（editor v3 阶段三 / 决策 Q9 偏好；模块级单例条件挂载，
      // 编辑停顿 500ms 后运行，规则与豁免口径见 lib/editor/diagnostics.js）
      ...(liveDiagnostics ? [DIAGNOSTICS_LINTER] : []),
      // 代码折叠（editor v3 阶段六b / 决策 Q14 偏好；模块级单例条件挂载，
      // 折叠区间由 latexGrammar 包随语言提供，占位符显示环境名/标题摘要）
      ...(codeFolding ? FOLDING_EXTENSIONS : []),
      // 自有 doc listener：换档后视图 state 不含包装层内部 listener，
      // doc 变更回写 store 统一走本入口（详见 handleDocChange 去重）；
      // 状态栏统计同 listener 发布（docChanged 走字数防抖）
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          handleDocChange(update.state.doc.toString());
          scheduleDocStats();
        }
        if (statsHubRef.current && (update.docChanged || update.selectionSet)) {
          publishStats(update.state);
        }
      }),
    ];
    if (softWrap) base.push(EditorView.lineWrapping);
    return base;
  }, [
    activeSuffix,
    softWrap,
    selectionMatches,
    liveDiagnostics,
    codeFolding,
    onCompletionApply,
    handleDocChange,
    scheduleDocStats,
    publishStats,
    getRefSymbols,
    getCustomSnippets,
  ]);

  /* ── 界面语言切换：热更新查找面板短语（phrases facet 依赖语言字段） ── */
  useEffect(() => {
    const handler = (lang) => {
      const view = viewRef.current;
      if (view && !view.destroyed) {
        view.dispatch({ effects: SEARCH_PANEL.setSearchLanguage.of(lang) });
      }
    };
    i18n.on("languageChanged", handler);
    return () => {
      i18n.off("languageChanged", handler);
    };
  }, []);

  /**
   * 自建 EditorState 的扩展配置：复刻 @uiw 包装层 getExtensions 的组装
   * （getDefaultExtensions 同参 + scrollerTheme 已并入基础主题），确保
   * 换档后的 tab 拥有与初始 state 一致的 basicSetup（history/键映射/
   * 编辑能力）与外观，不因换档丢失语法包与撤销能力。
   */
  const buildState = useCallback(
    (doc) =>
      EditorState.create({
        doc,
        extensions: [
          ...getDefaultExtensions({
            indentWithTab: true,
            editable: !readOnly,
            readOnly,
            theme: "none",
            basicSetup: BASIC_SETUP,
          }),
          ...extensions,
        ],
      }),
    [extensions, readOnly],
  );

  /** 换档后恢复滚动位置（setState 的 DOM 收尾在度量帧完成，rAF 落位） */
  const restoreScroll = useCallback((path) => {
    const top = tabViewRegistry.getScroll(path);
    if (top <= 0) return;
    const view = viewRef.current;
    if (!view || view.destroyed) return;
    view.scrollDOM.scrollTop = top;
    view.requestMeasure();
    requestAnimationFrame(() => {
      if (view.destroyed) return;
      view.scrollDOM.scrollTop = top;
    });
  }, []);

  /* ── tab 换档：收起旧 state → 换入存档 / 按当前缓冲区重建 ─────────── */
  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view || view.destroyed || !activeKey) return;
    const prevKey = currentKeyRef.current;
    if (prevKey === activeKey) return;
    // 首次装载：包装层已按 value 建好初始 state，无需换档
    if (!prevKey) {
      lastSyncedRef.current = value;
      currentKeyRef.current = activeKey;
      return;
    }
    // 收起旧 tab：完整 EditorState（撤销历史/选区）入登记表并记忆滚动
    tabViewRegistry.setState(prevKey, view.state, {
      softWrap,
      readOnly,
      selectionMatches,
      liveDiagnostics,
      codeFolding,
    });
    tabViewRegistry.setScroll(prevKey, view.scrollDOM.scrollTop);
    switchGuardRef.current = true;
    try {
      let state = tabViewRegistry.getState(activeKey);
      const config = tabViewRegistry.getConfig(activeKey);
      if (
        !state ||
        state.doc.toString() !== value ||
        config?.softWrap !== softWrap ||
        config?.readOnly !== readOnly ||
        config?.selectionMatches !== selectionMatches ||
        config?.liveDiagnostics !== liveDiagnostics ||
        config?.codeFolding !== codeFolding
      ) {
        // 无存档，或存档与 store 缓冲区/外观失配（外部整缓冲区替换、
        // 偏好变更后未经过换档）→ 按当前缓冲区重建（撤销历史复位）
        state = buildState(value);
        tabViewRegistry.setScroll(activeKey, 0);
      }
      tabViewRegistry.setState(activeKey, state, {
        softWrap,
        readOnly,
        selectionMatches,
        liveDiagnostics,
        codeFolding,
      });
      view.setState(state);
    } finally {
      switchGuardRef.current = false;
    }
    lastSyncedRef.current = value;
    currentKeyRef.current = activeKey;
    restoreScroll(activeKey);
    view.focus();
  }, [
    activeKey,
    value,
    softWrap,
    readOnly,
    selectionMatches,
    liveDiagnostics,
    codeFolding,
    extensions,
    buildState,
    restoreScroll,
  ]);

  /* ── 修改行色条基线同步（阶段八）：tab 激活（换档/重建）与保存回写
     （store 的 savedBuffer 变化）时对齐当前视图的基线字段；与字段现值
     相同则跳过 dispatch（键入期间 props 恒定，零额外事务）。
     注意：@uiw 包装层的 CM6 视图在挂载后异步创建（onCreateEditor 晚于
     本组件首个 passive effect），挂载期 effect 会因 view 未就绪空转——
     因此 onCreateEditor 回调里必须补跑一次，见 handleEditorCreated ── */
  const syncBaselinesFor = useCallback((view, key, saved, opened) => {
    if (!view || view.destroyed) return;
    const current = getChangeBaselines(view.state);
    if (
      current &&
      current.savedText === saved &&
      current.openedText === opened
    ) {
      return;
    }
    view.dispatch({
      effects: [setSavedTextEffect.of(saved), setOpenedTextEffect.of(opened)],
    });
  }, []);

  const baselinePropsRef = useRef({ activeKey, savedText, openedText });
  useLayoutEffect(() => {
    baselinePropsRef.current = { activeKey, savedText, openedText };
  });

  useEffect(() => {
    syncBaselinesFor(viewRef.current, activeKey, savedText, openedText);
  }, [activeKey, savedText, openedText, syncBaselinesFor]);

  /* ── 状态栏统计对齐（阶段八）：首装 / 换档 / 外部整缓冲区替换后同步
     计算一次字数并立即发布（不等防抖）；挂载期由 handleEditorCreated
     补跑（视图晚于首个 passive effect 就绪，同基线同步） ───────────── */
  useEffect(() => {
    const view = viewRef.current;
    if (!view || view.destroyed || !statsHubRef.current) return;
    docWordsCacheRef.current = {
      words: countDocStats(view.state.doc.toString()).totalWords,
    };
    publishStats(view.state);
  }, [activeKey, publishStats]);

  /** CM6 视图就绪回调：补跑挂载期因视图未就绪而空转的一次性对齐 */
  const handleEditorCreated = useCallback(
    (view) => {
      viewRef.current = view;
      const props = baselinePropsRef.current;
      syncBaselinesFor(
        view,
        props.activeKey,
        props.savedText,
        props.openedText,
      );
      if (statsHubRef.current) {
        docWordsCacheRef.current = {
          words: countDocStats(view.state.doc.toString()).totalWords,
        };
        publishStats(view.state);
      }
      onCreateEditor?.(view);
    },
    [onCreateEditor, syncBaselinesFor, publishStats],
  );

  /* ── 外部整缓冲区替换（冲突放弃重载 / 后端撤销刷新）：重建激活 tab ── */
  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view || view.destroyed || !activeKey) return;
    if (currentKeyRef.current !== activeKey) return; // 换档 effect 负责
    if (view.state.doc.toString() === value) return; // 键入回显，已同步
    const state = buildState(value);
    tabViewRegistry.setState(activeKey, state, {
      softWrap,
      readOnly,
      selectionMatches,
      liveDiagnostics,
      codeFolding,
    });
    tabViewRegistry.setScroll(activeKey, 0);
    switchGuardRef.current = true;
    try {
      view.setState(state);
    } finally {
      switchGuardRef.current = false;
    }
    lastSyncedRef.current = value;
  }, [
    activeKey,
    value,
    softWrap,
    readOnly,
    selectionMatches,
    liveDiagnostics,
    codeFolding,
    buildState,
  ]);

  return (
    <div
      className="h-full overflow-hidden rounded-lg border border-border bg-background"
      style={{ fontSize: `${fontSize}px` }}
    >
      <CodeMirror
        value={value}
        onChange={handleDocChange}
        onCreateEditor={handleEditorCreated}
        extensions={extensions}
        editable={!readOnly}
        readOnly={readOnly}
        theme="none"
        basicSetup={BASIC_SETUP}
        className={cn("h-full text-inherit", readOnly && "opacity-90")}
      />
    </div>
  );
}
