import { Fragment, memo, useMemo } from "react";
import { cn } from "@/lib/utils";
// 代码块 chrome 与语法 token 颜色（独立文件，随本组件按需加载）
import "@/styles/code-highlight.css";
import {
  escapeHtml,
  highlightHtml,
  highlightToHtmlLines,
} from "@/utils/highlight";

/**
 * 共享的代码编辑输入样式（Textarea / textarea 通用）。
 * 与 CodeBlock 一起构成"统一控制代码展示与编辑"的两个入口。
 */
export const latexEditorClass =
  "font-mono text-sm text-foreground bg-background border border-border rounded-md px-3 py-2 whitespace-pre-wrap resize-y focus:outline-none focus:ring-1 focus:ring-ring";

/**
 * 统一代码块展示组件。
 *
 * 所有只读 LaTeX / TikZ / BibTeX / 日志等代码展示都应使用本组件，
 * 以便统一外观并复用高亮逻辑（改样式只改 .code-block 类或 --code-* 变量）。
 *
 * @param {string} code      展示的源码文本
 * @param {string} language  高亮语言（latex / bibtex / python / json / plaintext 等）
 * @param {"block"|"bare"} variant  block=带背景/边框/圆角；bare=仅排版（用于已有卡片容器）
 * @param {boolean} highlight 是否启用语法高亮
 * @param {boolean} lineNumbers 是否显示左侧行号（默认开启）
 * @param {string} className 附加类名（如尺寸、外边距；同属性 utility 会覆盖默认值）
 */
function CodeBlock({
  code = "",
  language = "latex",
  variant = "block",
  highlight = true,
  lineNumbers = true,
  className,
}) {
  // 行号模式：直接渲染按行拆分的 HTML，不依赖 DOM 就地高亮
  const lineHtmls = useMemo(() => {
    if (!lineNumbers) return null;
    if (!highlight) {
      const lines = String(code)
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n")
        .split("\n");
      if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
      return lines.map(escapeHtml);
    }
    return highlightToHtmlLines(code, language);
  }, [code, language, highlight, lineNumbers]);

  // 非行号模式：渲染期派生高亮 HTML（hljs 输出已转义），
  // 不依赖 effect 就地改 DOM，避免每次渲染后重跑与着色闪烁。
  const html = useMemo(() => {
    if (!highlight || lineNumbers) return null;
    return highlightHtml(code, language);
  }, [code, language, highlight, lineNumbers]);

  if (lineNumbers) {
    return (
      <pre
        className={cn(
          variant === "bare" ? "code-block-bare" : "code-block",
          className,
        )}
      >
        <code className="code-lines">
          {(lineHtmls || []).map((lineHtml, i) => (
            <Fragment key={i}>
              <span className="code-line-num" aria-hidden="true">
                {i + 1}
              </span>
              <span
                className="code-line-text"
                dangerouslySetInnerHTML={{ __html: lineHtml }}
              />
            </Fragment>
          ))}
        </code>
      </pre>
    );
  }

  return (
    <pre
      className={cn(
        variant === "bare" ? "code-block-bare" : "code-block",
        className,
      )}
    >
      {highlight ? (
        <code dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <code>{code}</code>
      )}
    </pre>
  );
}

export default memo(CodeBlock);
