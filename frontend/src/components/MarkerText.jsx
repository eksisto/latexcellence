import React, { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";
import "@/styles/code-highlight.css";
import { highlightHtml } from "@/utils/highlight";

const MARKER_COLORS = {
  MATH: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 border-blue-200 dark:border-blue-800",
  CMD: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200 border-purple-200 dark:border-purple-800",
  ENV: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200 border-orange-200 dark:border-orange-800",
  VERB: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200 border-gray-200 dark:border-gray-700",
  COM: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200 border-yellow-200 dark:border-yellow-800",
};

const MARKER_REGEX = /(__(?:MATH|CMD|ENV|VERB|COM)_\d{4,}__)/g;

/**
 * 行内代码片段：以统一 .code-inline 样式渲染，并就地执行 LaTeX 高亮。
 * 高亮 HTML 在渲染期派生（useMemo），每个非 marker 的纯文本 token 独立高亮，
 * 避免破坏 marker 徽章的结构。
 */
const HighlightedFragment = memo(function HighlightedFragment({ text }) {
  const html = useMemo(() => highlightHtml(text, "latex"), [text]);

  return (
    <code className="code-inline" dangerouslySetInnerHTML={{ __html: html }} />
  );
});

/**
 * 将含 marker（__MATH_0000__ 等）的 LaTeX 文本渲染为 marker 徽章 + 纯文本。
 *
 * @param {string} text     待展示文本
 * @param {"code"|"text"} variant
 *   code：纯文本片段以 .code-inline（等宽 + LaTeX 高亮）渲染，默认值；
 *   text：保持纯文本原样（用于 diff 等已有语义配色的场景，避免高亮覆盖红/绿）。
 */
function MarkerText({ text, variant = "code" }) {
  const { t } = useTranslation("components");
  const isCode = variant === "code";

  const markerLabels = useMemo(
    () => ({
      MATH: t("markerText.math"),
      CMD: t("markerText.cmd"),
      ENV: t("markerText.env"),
      VERB: t("markerText.verb"),
      COM: t("markerText.com"),
    }),
    [t],
  );

  const tokens = useMemo(() => (text ? text.split(MARKER_REGEX) : []), [text]);

  if (!text) return null;

  return (
    <>
      {tokens.map((token, index) => {
        const m = token.match(/^__(MATH|CMD|ENV|VERB|COM)_(\d{4,})__$/);
        if (m) {
          const type = m[1];
          const num = m[2];
          return (
            <span
              key={index}
              className={`inline-flex items-center px-1 py-0.5 rounded text-[10px] font-medium mx-0.5 border ${MARKER_COLORS[type]} align-middle shadow-sm`}
              title={token}
            >
              {markerLabels[type]}-{num}
            </span>
          );
        }
        if (!isCode) {
          return <React.Fragment key={index}>{token}</React.Fragment>;
        }
        return <HighlightedFragment key={index} text={token} />;
      })}
    </>
  );
}

export default memo(MarkerText);
