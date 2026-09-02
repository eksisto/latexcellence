/**
 * LaTeXEditor —— CodeMirror 6 受控封装 + 多缓冲区换档（editor-plan §9、
 * editor-productivity-plan §4/§5.1，阶段一）。
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
 *   - 语言包经 lib/editor/language.js 抽象层引入（决策 Q2），本组件不直接
 *     import legacy-modes，保证将来可整体替换 AGPL 语法包；
 *   - 缓冲区内撤销/重做交给 CM6 自身 history（basicSetup 内置，
 *     Ctrl/Cmd+Z / Shift+Ctrl/Cmd+Z，随 EditorState 按文件隔离），与后端
 *     撤销分属两层（方案 §8）；
 *   - Ctrl/Cmd+S 不在编辑器键映射内绑定：由页面级 window 监听统一拦截
 *     （焦点在编辑器内外行为一致，且避免双重触发），本组件只负责呈现；
 *   - 高亮配色复用全局 --code-token-* 变量、底色复用主题变量，
 *     亮/暗主题切换零 JS 参与。
 */

import { useCallback, useLayoutEffect, useMemo, useRef } from "react";
import CodeMirror, { getDefaultExtensions } from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

import { latexLanguageExtension } from "@/lib/editor/language";
import { latexCompletionExtension } from "@/lib/editor/latexCompletion";
import { snippetPlaceholderExtension } from "@/lib/editor/snippetInsert";
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

/** LaTeX 语法高亮：token 颜色对齐 CodeBlock 的 --code-token-* 变量 */
const latexHighlightStyle = HighlightStyle.define([
  { tag: t.comment, color: "var(--code-token-comment)" },
  { tag: t.keyword, color: "var(--code-token-keyword)" },
  { tag: t.atom, color: "var(--code-token-built-in)" },
  { tag: t.number, color: "var(--code-token-number)" },
  { tag: t.special(t.variableName), color: "var(--code-token-string)" },
  { tag: t.tagName, color: "var(--code-token-title)" },
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
  allowMultipleSelections: false,
};

/**
 * @param {object} props
 * @param {string} props.value                 激活 tab 的缓冲区全文
 * @param {string|null} props.activeKey        激活 tab 的 filePath（换档键）
 * @param {(value: string) => void} props.onChange
 * @param {boolean} [props.softWrap=true]      软换行（默认开启）
 * @param {number} [props.fontSize=14]         字号 px
 * @param {boolean} [props.readOnly=false]     viewer 只读
 * @param {(view: EditorView) => void} [props.onCreateEditor]
 *   CM6 视图实例回调（命令面板等外部插入入口经此持有视图）
 * @param {(entry: object) => void} [props.onCompletionApply]
 *   行内补全应用后回调（宏包缺失联动）
 */
export default function LaTeXEditor({
  value,
  activeKey,
  onChange,
  softWrap = true,
  fontSize = 14,
  readOnly = false,
  onCreateEditor,
  onCompletionApply,
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

  const handleDocChange = useCallback((nextValue) => {
    if (switchGuardRef.current) return;
    // 去重：@uiw 包装层 listener 与自有 listener 对同一事务都会通知，
    // 值相同只写一次（不影响连续两次真实变更——值必然不同）
    if (nextValue === lastSyncedRef.current) return;
    lastSyncedRef.current = nextValue;
    onChangeRef.current(nextValue);
  }, []);

  const extensions = useMemo(() => {
    const base = [
      latexLanguageExtension(),
      syntaxHighlighting(latexHighlightStyle),
      editorBaseTheme,
      // 阶段三：`\` / `\begin{` 行内补全 + 模板占位符跳转模式；
      // getLanguage 供中文名 detail 按界面语言本地化（每次触发时求值）
      latexCompletionExtension({
        onApplyEntry: onCompletionApply,
        getLanguage: () => i18n.language,
      }),
      snippetPlaceholderExtension(),
      // 自有 doc listener：换档后视图 state 不含包装层内部 listener，
      // doc 变更回写 store 统一走本入口（详见 handleDocChange 去重）
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          handleDocChange(update.state.doc.toString());
        }
      }),
    ];
    if (softWrap) base.push(EditorView.lineWrapping);
    return base;
  }, [softWrap, onCompletionApply, handleDocChange]);

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
    tabViewRegistry.setState(prevKey, view.state, { softWrap, readOnly });
    tabViewRegistry.setScroll(prevKey, view.scrollDOM.scrollTop);
    switchGuardRef.current = true;
    try {
      let state = tabViewRegistry.getState(activeKey);
      const config = tabViewRegistry.getConfig(activeKey);
      if (
        !state ||
        state.doc.toString() !== value ||
        config?.softWrap !== softWrap ||
        config?.readOnly !== readOnly
      ) {
        // 无存档，或存档与 store 缓冲区/外观失配（外部整缓冲区替换、
        // 偏好变更后未经过换档）→ 按当前缓冲区重建（撤销历史复位）
        state = buildState(value);
        tabViewRegistry.setScroll(activeKey, 0);
      }
      tabViewRegistry.setState(activeKey, state, { softWrap, readOnly });
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
    extensions,
    buildState,
    restoreScroll,
  ]);

  /* ── 外部整缓冲区替换（冲突放弃重载 / 后端撤销刷新）：重建激活 tab ── */
  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view || view.destroyed || !activeKey) return;
    if (currentKeyRef.current !== activeKey) return; // 换档 effect 负责
    if (view.state.doc.toString() === value) return; // 键入回显，已同步
    const state = buildState(value);
    tabViewRegistry.setState(activeKey, state, { softWrap, readOnly });
    tabViewRegistry.setScroll(activeKey, 0);
    switchGuardRef.current = true;
    try {
      view.setState(state);
    } finally {
      switchGuardRef.current = false;
    }
    lastSyncedRef.current = value;
  }, [activeKey, value, softWrap, readOnly, buildState]);

  return (
    <div
      className="h-full overflow-hidden rounded-lg border border-border bg-background"
      style={{ fontSize: `${fontSize}px` }}
    >
      <CodeMirror
        value={value}
        onChange={handleDocChange}
        onCreateEditor={(view) => {
          viewRef.current = view;
          onCreateEditor?.(view);
        }}
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
