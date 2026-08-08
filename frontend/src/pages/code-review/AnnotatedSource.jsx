import React from "react";
import MarkerText from "@/components/MarkerText";

const ADDED_MARK_CLASS =
  "text-blue-600 dark:text-blue-400 underline decoration-1 underline-offset-2";

/**
 * 按 Tab 切块渲染带文字装饰的文本，Tab 块单独去掉 text-decoration。
 * Chromium 在鼠标选中文字时会补画 Tab 空白上的下划线/删除线，
 * 将 Tab 拆成独立行内盒即可避免该行为。
 */
const renderTabSafeText = (text, keyPrefix, markClass) => {
  const chunks = text.split(/(\t+)/);
  return chunks.map((chunk, j) => {
    if (!chunk) return null;
    const isTab = /^\t+$/.test(chunk);
    return (
      <span
        key={`${keyPrefix}-c${j}`}
        className={isTab ? "no-underline" : markClass}
      >
        <MarkerText text={chunk} variant="text" />
      </span>
    );
  });
};

/**
 * 校对样张：把格式化差异渲染成 latexdiff 风格的内联标记
 * （新增=蓝色下划线，删除=红色删除线），并在建议锚点处插入批注编号。
 * anchors 必须已按 offset 升序排列（由 buildAnchors 保证）。
 */
const AnnotatedSource = React.memo(
  ({ parts, anchors = [], showMarks = true }) => {
    const elements = [];
    let fmtOffset = 0;
    let anchorIdx = 0;

    parts?.forEach((part, i) => {
      if (part.removed) {
        if (showMarks) {
          elements.push(
            ...renderTabSafeText(
              part.value,
              `r${i}`,
              "text-red-600 dark:text-red-400 line-through decoration-1",
            ),
          );
        }
        return;
      }

      const len = part.value.length;
      let cursor = 0;
      while (
        anchorIdx < anchors.length &&
        anchors[anchorIdx].offset <= fmtOffset + len
      ) {
        const rel = anchors[anchorIdx].offset - fmtOffset;
        if (rel > cursor) {
          const sliceText = part.value.slice(cursor, rel);
          if (showMarks && part.added) {
            elements.push(
              ...renderTabSafeText(
                sliceText,
                `${i}-${cursor}`,
                ADDED_MARK_CLASS,
              ),
            );
          } else {
            elements.push(
              <span key={`${i}-${cursor}`}>
                <MarkerText text={sliceText} variant="text" />
              </span>,
            );
          }
        }
        elements.push(
          <sup
            key={`a${anchorIdx}`}
            title={anchors[anchorIdx].description}
            className="ml-0.5 cursor-default select-none text-[10px] leading-none font-semibold text-amber-600 dark:text-amber-400"
          >
            {anchors[anchorIdx].index}
          </sup>,
        );
        cursor = rel;
        anchorIdx += 1;
      }
      if (cursor < len) {
        const sliceText = part.value.slice(cursor);
        if (showMarks && part.added) {
          elements.push(
            ...renderTabSafeText(sliceText, `${i}-end`, ADDED_MARK_CLASS),
          );
        } else {
          elements.push(
            <span key={`${i}-end`}>
              <MarkerText text={sliceText} variant="text" />
            </span>,
          );
        }
      }
      fmtOffset += len;
    });

    return <>{elements}</>;
  },
);

export default AnnotatedSource;
