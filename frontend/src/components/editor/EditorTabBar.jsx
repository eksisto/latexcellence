/**
 * EditorTabBar —— 多缓冲区标签条（editor-productivity-plan §5.1 / 决策 Q3，
 * 阶段一）。中栏顶部：标签 + 脏标记圆点 + 陈旧「磁盘已变更」标记 + 关闭
 * 按钮；多于一个脏 tab 时提供「全部保存」入口（§4）。
 *
 * A2 渲染解耦：tab 列表用 useShallow 投影为路径数组（键入不改路径集合，
 * 不随每次按键重渲染）；每个 tab 项各自订阅自身的 dirty / stale 原始值，
 * 键入只在 dirty 翻转（首键）时触及本条。
 */

import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import Save from "lucide-react/dist/esm/icons/save.mjs";
import TriangleAlert from "lucide-react/dist/esm/icons/alert-triangle.mjs";
import X from "lucide-react/dist/esm/icons/x.mjs";

import useEditorStore, { countDirtyTabs } from "@/store/editorStore";
import { cn } from "@/lib/utils";

/** 单个标签项：自身 dirty / stale 原始值订阅，键入不牵连整条重渲染 */
function TabItem({ path, active, onSelect, onClose }) {
  const { t } = useTranslation("components");
  const dirty = useEditorStore(
    (s) => s.tabs.find((tab) => tab.filePath === path)?.dirty ?? false,
  );
  const stale = useEditorStore(
    (s) => s.tabs.find((tab) => tab.filePath === path)?.stale ?? false,
  );
  const name = useMemo(() => path.split("/").pop() || path, [path]);

  return (
    <div
      role="tab"
      aria-selected={active}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group flex max-w-52 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1",
        "text-xs outline-none transition-colors select-none",
        "focus-visible:ring-2 focus-visible:ring-ring/50",
        active
          ? "border-border bg-background text-foreground shadow-sm"
          : "border-transparent bg-muted/40 text-muted-foreground hover:bg-muted/70 hover:text-foreground",
      )}
      title={path}
    >
      {stale && (
        <Tooltip>
          <TooltipTrigger
            render={
              <TriangleAlert className="size-3 shrink-0 text-amber-500" />
            }
          >
            <span className="sr-only">{t("editor.tabStaleBadge")}</span>
          </TooltipTrigger>
          <TooltipContent>{t("editor.tabStaleBadge")}</TooltipContent>
        </Tooltip>
      )}
      <span className="min-w-0 truncate font-mono">{name}</span>
      {dirty && (
        <span
          className="inline-block size-1.5 shrink-0 rounded-full bg-primary"
          title={t("editor.unsavedDot")}
        />
      )}
      <button
        type="button"
        className={cn(
          "-mr-1 shrink-0 rounded-sm p-0.5 text-muted-foreground/70",
          "hover:bg-foreground/10 hover:text-foreground",
        )}
        aria-label={t("editor.closeTab")}
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

/**
 * @param {object} props
 * @param {(path: string) => void} props.onActivateTab 激活 tab
 * @param {(path: string) => void} props.onRequestCloseTab 关闭入口（脏转三选）
 * @param {() => void} props.onSaveAll 全部保存
 */
export default function EditorTabBar({
  onActivateTab,
  onRequestCloseTab,
  onSaveAll,
}) {
  const { t } = useTranslation("components");
  // useShallow 投影为路径数组：键入不改集合 → 不触发本条重渲染
  const tabPaths = useEditorStore(
    useShallow((s) => s.tabs.map((tab) => tab.filePath)),
  );
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const dirtyCount = useEditorStore((s) => countDirtyTabs(s.tabs));

  const handleSaveAll = useCallback(() => onSaveAll(), [onSaveAll]);

  if (tabPaths.length === 0) return null;

  return (
    <div className="flex items-center gap-2">
      <div
        role="tablist"
        aria-label={t("editor.tabBarLabel")}
        className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto pb-0.5"
      >
        {tabPaths.map((path) => (
          <TabItem
            key={path}
            path={path}
            active={path === activeTabPath}
            onSelect={() => onActivateTab(path)}
            onClose={() => onRequestCloseTab(path)}
          />
        ))}
      </div>
      {dirtyCount > 1 && (
        <Button
          size="xs"
          variant="outline"
          className="shrink-0"
          onClick={handleSaveAll}
        >
          <Save />
          {t("editor.saveAll")}
        </Button>
      )}
    </div>
  );
}
