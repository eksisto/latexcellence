/**
 * OutlinePanel —— 左栏「大纲」tab 内容（editor-productivity-design §5.3
 * 阶段三 / 决策 Q6；editor-experience-design §4.4 / 决策 Q10 阶段四双视图）。
 *
 * 「当前文件」视图（行为不变）：解析激活缓冲区的 `\section` /
 * `\subsection` / `\subsubsection` / `\paragraph` 四级缩进条目，点击条目
 * 滚动 CM6 到对应行首并高亮该行（定位回调由页面注入）。
 *
 * 「本项目」视图（阶段四）：数据来自后端 GET /api/project/outline 全项目
 * .tex 分节扫描（首轮按文件平铺分组，\input/\include 树状排序不做）。
 * 激活文件所在组置顶并默认展开，其余组折叠、点击文件头切换展开；
 * 点击条目联动打开/激活对应 tab 并行首定位（复用编译错误跳转的
 * pendingErrorJumpRef 换档机制，由页面注入回调）。
 *
 * 数据由 useEditorOutline 提供（当前文件：编辑防抖 300ms 重扫、切换 tab
 * 立即重扫；本项目：切入视图立即拉取、保存后防抖重拉）。视图切换状态
 * 由页面持有（左栏 tab 切换卸载本组件时不丢失视图选择）。空态分支：
 * 当前文件视图的无激活 tab / 无条目、本项目视图的暂无条目，文案三语。
 */

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import ChevronDown from "lucide-react/dist/esm/icons/chevron-down.mjs";
import ChevronRight from "lucide-react/dist/esm/icons/chevron-right.mjs";

import useEditorStore from "@/store/editorStore";
import useEditorOutline, {
  OUTLINE_VIEW_FILE,
  OUTLINE_VIEW_PROJECT,
} from "@/hooks/useEditorOutline";
import { groupProjectOutline } from "@/lib/editor/outlineProject";
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

/** 本项目视图的单文件分组（文件头可折叠 + 条目缩进） */
function FileGroup({ group, open, onToggle, onJumpToEntry }) {
  const { t } = useTranslation("components");
  // 展示 basename（相对路径完整形态进 title）
  const name = group.file_path.split("/").pop();
  return (
    <div className="space-y-0.5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        title={group.file_path}
        className={cn(
          "flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-left text-xs",
          "outline-none transition-colors select-none",
          "hover:bg-muted/70 focus-visible:bg-muted/70",
          "focus-visible:ring-2 focus-visible:ring-ring/50",
          group.active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {open ? (
          <ChevronDown className="size-3 shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight className="size-3 shrink-0" aria-hidden="true" />
        )}
        <span className="min-w-0 truncate font-medium">{name}</span>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/70">
          {t("editor.outline.entryCount", { count: group.entries.length })}
        </span>
      </button>
      {open &&
        group.entries.map((entry, index) => (
          <OutlineItem
            key={`${entry.line}-${index}`}
            entry={entry}
            onJump={(line) => onJumpToEntry({ file: group.file_path, line })}
          />
        ))}
    </div>
  );
}

/**
 * @param {object} props
 * @param {(line: number) => void} props.onJumpToLine 当前文件视图点击定位（行首滚动 + 高亮）
 * @param {({file: string, line: number}) => void} props.onJumpToFileLine
 *   本项目视图点击定位：开 tab + 行首定位（复用错误跳转换档机制）
 * @param {string} props.view 大纲视图（OUTLINE_VIEW_FILE / OUTLINE_VIEW_PROJECT）
 * @param {(view: string) => void} props.onViewChange 视图切换回调（状态由页面持有）
 */
export default function OutlinePanel({
  onJumpToLine,
  onJumpToFileLine,
  view,
  onViewChange,
}) {
  const { t } = useTranslation("components");
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const hasActiveTab = activeTabPath !== null;
  const { entries, files, loading } = useEditorOutline(view);
  const groups = useMemo(
    () =>
      view === OUTLINE_VIEW_PROJECT
        ? groupProjectOutline(files, activeTabPath)
        : [],
    [view, files, activeTabPath],
  );

  // 组展开状态：null = 跟随默认（仅激活组展开）；数据重拉后回落默认
  const [expandedPaths, setExpandedPaths] = useState(null);
  useEffect(() => {
    setExpandedPaths(null);
  }, [files]);

  if (view === OUTLINE_VIEW_PROJECT) {
    return (
      <div className="flex h-full flex-col gap-1">
        <ViewToggle view={view} onViewChange={onViewChange} />
        <div className="min-h-0 flex-1 overflow-auto pb-1">
          {loading && groups.length === 0 ? (
            <p className="px-2 py-1 text-xs text-muted-foreground">
              {t("editor.outline.projectLoading")}
            </p>
          ) : groups.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-1 px-3 text-center">
              <p className="text-sm text-muted-foreground">
                {t("editor.outline.projectEmptyTitle")}
              </p>
              <p className="text-xs text-muted-foreground/70">
                {t("editor.outline.projectEmptyDescription")}
              </p>
            </div>
          ) : (
            <div role="list" aria-label={t("editor.outline.label")}>
              {groups.map((group) => {
                const open = expandedPaths
                  ? expandedPaths.has(group.file_path)
                  : group.active;
                return (
                  <FileGroup
                    key={group.file_path}
                    group={group}
                    open={open}
                    onToggle={() =>
                      setExpandedPaths((prev) => {
                        const base =
                          prev ??
                          new Set(
                            groups
                              .filter((g) => g.active)
                              .map((g) => g.file_path),
                          );
                        const next = new Set(base);
                        if (next.has(group.file_path)) {
                          next.delete(group.file_path);
                        } else {
                          next.add(group.file_path);
                        }
                        return next;
                      })
                    }
                    onJumpToEntry={onJumpToFileLine}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-1">
      <ViewToggle view={view} onViewChange={onViewChange} />
      {!hasActiveTab ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-1 px-3 text-center">
          <p className="text-sm text-muted-foreground">
            {t("editor.outline.noFileTitle")}
          </p>
          <p className="text-xs text-muted-foreground/70">
            {t("editor.outline.noFileDescription")}
          </p>
        </div>
      ) : entries.length === 0 ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-1 px-3 text-center">
          <p className="text-sm text-muted-foreground">
            {t("editor.outline.emptyTitle")}
          </p>
          <p className="text-xs text-muted-foreground/70">
            {t("editor.outline.emptyDescription")}
          </p>
        </div>
      ) : (
        <div
          role="list"
          aria-label={t("editor.outline.label")}
          className="min-h-0 flex-1 space-y-0.5 overflow-auto pb-1"
        >
          {entries.map((entry, index) => (
            <OutlineItem
              key={`${entry.line}-${index}`}
              entry={entry}
              onJump={onJumpToLine}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** 当前文件 / 本项目 双视图切换（状态由页面持有） */
function ViewToggle({ view, onViewChange }) {
  const { t } = useTranslation("components");
  return (
    <Tabs value={view} onValueChange={onViewChange}>
      <TabsList className="h-7 w-full">
        <TabsTrigger
          value={OUTLINE_VIEW_FILE}
          className="h-6 flex-1 px-2 text-xs"
        >
          {t("editor.outline.viewFile")}
        </TabsTrigger>
        <TabsTrigger
          value={OUTLINE_VIEW_PROJECT}
          className="h-6 flex-1 px-2 text-xs"
        >
          {t("editor.outline.viewProject")}
        </TabsTrigger>
      </TabsList>
    </Tabs>
  );
}
