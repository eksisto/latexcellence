/**
 * OutlinePanel —— 左栏「大纲」tab 内容（editor-productivity-plan §5.3
 * 阶段三 / 决策 Q6）。
 *
 * 展示当前激活缓冲区的大纲：`\section` / `\subsection` /
 * `\subsubsection` / `\paragraph` 四级缩进（§4 实现层既定决策），
 * 点击条目滚动 CM6 到对应行首并高亮该行（定位回调由页面注入）。
 *
 * 数据由 useEditorOutline 提供（仅解析激活缓冲区，编辑防抖 300ms 重扫，
 * 切换 tab 立即重扫）；本组件只订阅 activeTabPath 布尔派生与扫描结果，
 * 键入期间不参与重渲染。空态两分支：无激活 tab / 无大纲条目，文案三语。
 */

import { useTranslation } from "react-i18next";

import useEditorStore from "@/store/editorStore";
import useEditorOutline from "@/hooks/useEditorOutline";
import { cn } from "@/lib/utils";

/** 四级缩进：section 0 缩进、subsection 一级……（Tailwind 4 动态间距） */
const LEVEL_INDENT_CLASSES = ["pl-2", "pl-6", "pl-10", "pl-[3.25rem]"];

/** 单个大纲条目按钮 */
function OutlineItem({ entry, onJump }) {
  return (
    <button
      type="button"
      role="listitem"
      onClick={() => onJump(entry.line)}
      title={entry.title}
      className={cn(
        "flex w-full items-baseline rounded-md py-1 pr-2 text-left text-xs",
        "text-foreground/80 outline-none transition-colors select-none",
        "hover:bg-muted/70 hover:text-foreground",
        "focus-visible:bg-muted/70 focus-visible:ring-2 focus-visible:ring-ring/50",
        LEVEL_INDENT_CLASSES[entry.level] ?? LEVEL_INDENT_CLASSES[0],
        entry.level === 3 && "text-muted-foreground",
      )}
    >
      <span className="min-w-0 truncate">{entry.title}</span>
    </button>
  );
}

/**
 * @param {object} props
 * @param {(line: number) => void} props.onJumpToLine 点击定位（行首滚动 + 高亮）
 */
export default function OutlinePanel({ onJumpToLine }) {
  const { t } = useTranslation("components");
  const entries = useEditorOutline();
  const hasActiveTab = useEditorStore((s) => s.activeTabPath !== null);

  if (!hasActiveTab) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-3 text-center">
        <p className="text-sm text-muted-foreground">
          {t("editor.outline.noFileTitle")}
        </p>
        <p className="text-xs text-muted-foreground/70">
          {t("editor.outline.noFileDescription")}
        </p>
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-3 text-center">
        <p className="text-sm text-muted-foreground">
          {t("editor.outline.emptyTitle")}
        </p>
        <p className="text-xs text-muted-foreground/70">
          {t("editor.outline.emptyDescription")}
        </p>
      </div>
    );
  }

  return (
    <div
      role="list"
      aria-label={t("editor.outline.label")}
      className="space-y-0.5 pb-1"
    >
      {entries.map((entry, index) => (
        <OutlineItem
          key={`${entry.line}-${index}`}
          entry={entry}
          onJump={onJumpToLine}
        />
      ))}
    </div>
  );
}
