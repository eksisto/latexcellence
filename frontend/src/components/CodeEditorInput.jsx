import { memo, useCallback, useLayoutEffect, useMemo, useRef } from "react";
import { cn } from "@/lib/utils";
// 语法 token 颜色（独立文件，随本组件按需加载）
import "@/styles/code-highlight.css";
import { highlightEditorHtml } from "@/utils/highlight";
import { latexEditorClass } from "@/components/CodeBlock";

/**
 * 可编辑代码输入框（高亮版）。
 *
 * 采用"高亮层 + 透明 textarea 叠加"方案：底层 <pre> 渲染 highlight.js 高亮结果，
 * 上层 textarea 文字设为透明（仅保留光标/选区/滚动条），两者共用同一套字体、
 * 行高、内边距与换行规则，保证逐字对齐；滚动时同步高亮层位置。
 * 与 CodeBlock 一起构成"统一控制代码展示与编辑"的两个入口。
 *
 * @param {string} value     受控值（与 textarea 相同用法）
 * @param {string} language  高亮语言（默认 latex）
 * @param {string} className 附加类名（尺寸等，与 latexEditorClass 冲突时覆盖它）
 * @param {object} props     其余 props 透传给 textarea（onChange / placeholder 等）
 */
function CodeEditorInput({
  value = "",
  language = "latex",
  className,
  ...props
}) {
  const textareaRef = useRef(null);
  const highlightRef = useRef(null);

  // 渲染期派生高亮 HTML（hljs 输出已转义，可安全注入）。
  const html = useMemo(() => {
    return highlightEditorHtml(value, language);
  }, [value, language]);

  const syncScroll = useCallback(() => {
    const textarea = textareaRef.current;
    const highlight = highlightRef.current;
    if (!textarea || !highlight) return;
    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
  }, []);

  // value 变化后（粘贴/清空/外部重置）textarea 滚动位置可能变化，
  // 每次渲染后同步一次高亮层。
  useLayoutEffect(() => {
    syncScroll();
  });

  return (
    <div className="relative">
      <pre
        ref={highlightRef}
        aria-hidden="true"
        className={cn(
          latexEditorClass,
          className,
          "pointer-events-none absolute inset-0 m-0 resize-none overflow-hidden whitespace-pre-wrap break-all [scrollbar-gutter:stable]",
        )}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      <textarea
        ref={textareaRef}
        value={value}
        onScroll={syncScroll}
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        autoCorrect="off"
        className={cn(
          latexEditorClass,
          className,
          "relative block w-full field-sizing-content bg-transparent text-transparent caret-foreground resize-none overflow-auto whitespace-pre-wrap break-all placeholder:text-muted-foreground [scrollbar-gutter:stable]",
        )}
        {...props}
      />
    </div>
  );
}

export default memo(CodeEditorInput);
