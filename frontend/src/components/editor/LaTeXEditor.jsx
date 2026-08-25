/**
 * LaTeXEditor —— CodeMirror 6 受控封装（editor-plan §9）。
 *
 * 设计要点：
 *   - 受控 value / onChange：缓冲区内容由 editorSession 切片持有，
 *     本组件只做单向回写，不内部持有文本状态；
 *   - 语言包经 lib/editor/language.js 抽象层引入（决策 Q2），本组件不直接
 *     import legacy-modes，保证将来可整体替换 AGPL 语法包；
 *   - 缓冲区内撤销/重做交给 CM6 自身 history（basicSetup 内置，
 *     Ctrl/Cmd+Z / Shift+Ctrl/Cmd+Z），与后端撤销分属两层（方案 §8）；
 *   - Ctrl/Cmd+S 不在编辑器键映射内绑定：由页面级 window 监听统一拦截
 *     （焦点在编辑器内外行为一致，且避免双重触发），本组件只负责呈现；
 *   - 高亮配色复用全局 --code-token-* 变量、底色复用主题变量，
 *     亮/暗主题切换零 JS 参与。
 */

import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";

import { latexLanguageExtension } from "@/lib/editor/language";
import { latexCompletionExtension } from "@/lib/editor/latexCompletion";
import { snippetPlaceholderExtension } from "@/lib/editor/snippetInsert";
import { cn } from "@/lib/utils";

/** 亮/暗主题自适应基础主题（全部走 CSS 变量） */
const editorBaseTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "var(--background)",
    color: "var(--foreground)",
  },
  ".cm-scroller": {
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
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "color-mix(in oklab, var(--primary) 22%, transparent)",
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
 * @param {object} props
 * @param {string} props.value                 缓冲区全文
 * @param {(value: string) => void} props.onChange
 * @param {boolean} [props.softWrap=true]      软换行（默认开启）
 * @param {number} [props.fontSize=14]         字号 px
 * @param {boolean} [props.readOnly=false]     viewer 只读
 * @param {(view: EditorView) => void} [props.onCreateEditor]
 *   CM6 视图实例回调（命令面板等外部插入入口经此持有视图；阶段三接线）
 * @param {(entry: object) => void} [props.onCompletionApply]
 *   行内补全应用后回调（宏包缺失联动；阶段三接线）
 */
export default function LaTeXEditor({
  value,
  onChange,
  softWrap = true,
  fontSize = 14,
  readOnly = false,
  onCreateEditor,
  onCompletionApply,
}) {
  const extensions = useMemo(() => {
    const base = [
      latexLanguageExtension(),
      syntaxHighlighting(latexHighlightStyle),
      editorBaseTheme,
      // 阶段三：`\` / `\begin{` 行内补全 + 模板占位符跳转模式
      latexCompletionExtension({ onApplyEntry: onCompletionApply }),
      snippetPlaceholderExtension(),
    ];
    if (softWrap) base.push(EditorView.lineWrapping);
    return base;
  }, [softWrap, onCompletionApply]);

  return (
    <div
      className="h-full overflow-hidden rounded-lg border border-border bg-background"
      style={{ fontSize: `${fontSize}px` }}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        onCreateEditor={onCreateEditor}
        extensions={extensions}
        editable={!readOnly}
        readOnly={readOnly}
        theme="none"
        basicSetup={{
          foldGutter: false,
          highlightSelectionMatches: false,
          allowMultipleSelections: false,
        }}
        className={cn("h-full text-inherit", readOnly && "opacity-90")}
      />
    </div>
  );
}
