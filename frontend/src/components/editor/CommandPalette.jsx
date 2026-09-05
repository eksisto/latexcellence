/**
 * CommandPalette —— 命令面板（editor-design §6.3 入口二 / 决策 Q8、Q12）。
 *
 * `Ctrl/Cmd+/` 或工具栏按钮唤起的居中面板：搜索框 + 分类列表。
 *   - 搜索与行内补全共用 lib/editor/matcher.js 同一索引与打分（§6.2 一致性）；
 *   - 空查询按数据库默认顺序分类浏览；有查询时按得分归一排序平铺，
 *     条目上标注所属类目；
 *   - 自定义片段（editor v3 阶段二 / 决策 Q8）经 customCommands 合并层
 *     追加进匹配索引，归入独立 "custom" 分组置尾；
 *   - 带 grid 配置的条目进入 GridSizePicker 二级步骤选定行列后插入；
 *   - 插入动作经 onInsert 回调交由页面执行（snippetInsert 占位符模式 +
 *     宏包缺失联动），返回 false 表示拒绝（viewer 只读 / 未打开文件），
 *     面板保持打开；
 *   - viewer 只读角色可浏览搜索，但插入被禁用（§4 权限：可浏览不可插入）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import GridSizePicker from "@/components/editor/GridSizePicker";

import Search from "lucide-react/dist/esm/icons/search.mjs";
import Table from "lucide-react/dist/esm/icons/table.mjs";

import { LATEX_COMMANDS as LATEX_COMMANDS_BASE } from "@/data/latexCommands/index.js";

import {
  buildCommandIndex,
  getExtendedMatcherIndex,
  listCommands,
  searchCommands,
} from "@/lib/editor/matcher.js";
import { getCommandZhDisplay } from "@/lib/editor/commandZh.js";
import {
  groupCommandsWithCustom,
  withCustomCommands,
} from "@/lib/editor/customCommands.js";
import useEditorStore from "@/store/editorStore";
import { useShallow } from "zustand/react/shallow";

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {(open: boolean) => void} props.onOpenChange
 * @param {boolean} [props.readOnly=false] viewer 只读：仅浏览不可插入
 * @param {string|null} [props.categoryFilter=null] 类目过滤（非空时面板只
 *   展示/搜索该类目，作为专项浮层菜单使用，如 Ctrl+L 的 wrap 全集）
 * @param {(entry: object, size: {rows:number,cols:number}|null) => boolean} props.onInsert
 */
export default function CommandPalette({
  open,
  onOpenChange,
  readOnly = false,
  categoryFilter = null,
  onInsert,
}) {
  const { t, i18n } = useTranslation("components");
  const [query, setQuery] = useState("");
  const [indexRows, setIndexRows] = useState(null);
  /** 二级步骤：当前进入网格选择器的条目（null 表示列表态） */
  const [pickerEntry, setPickerEntry] = useState(null);
  const [activeIndex, setActiveIndex] = useState(0);
  /** 中文名展示函数（按界面语言；null = 隐藏或未就绪，渲染为空） */
  const [zhDisplay, setZhDisplay] = useState(null);
  // 自定义片段（editor v3 阶段二 / 决策 Q8）：随偏好更新重取派生索引
  const { customSnippets } = useEditorStore(
    useShallow((s) => ({ customSnippets: s.customSnippets })),
  );

  /* 中文名本地化跟随界面语言（en → null 隐藏；zh-TW → 简转繁懒加载） */
  useEffect(() => {
    let cancelled = false;
    getCommandZhDisplay(i18n.language).then((fn) => {
      // 函数值入 state 必须包一层 updater，否则会被 React 当作惰性更新执行
      if (!cancelled) setZhDisplay(() => fn);
    });
    return () => {
      cancelled = true;
    };
  }, [i18n.language]);

  const inputRef = useRef(null);
  const itemRefs = useRef(new Map());

  /* 打开时重置并确保索引就绪（pinyin-pro 动态 chunk 首载；customSnippets
     变更时重取派生索引——面板关闭期间编辑片段，下次打开即生效） */
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setPickerEntry(null);
    setActiveIndex(0);
    let cancelled = false;
    getExtendedMatcherIndex(customSnippets)
      .then((index) => {
        if (!cancelled) setIndexRows(index);
      })
      .catch(() => {
        // 拼音资源加载失败：退化为无拼音索引（中文子串/英文两路仍可检索）
        if (!cancelled) {
          setIndexRows(
            buildCommandIndex(
              withCustomCommands(LATEX_COMMANDS_BASE, customSnippets),
            ),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, customSnippets]);

  /* 输入框聚焦 */
  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [open]);

  /**
   * 可见结果：
   *   - 有查询：共享匹配器打分排序的平铺列表；
   *   - 无查询：按类目分组的默认列表（顺序即 COMMAND_CATEGORIES）。
   */
  const { groups, flatItems } = useMemo(() => {
    if (!indexRows) return { groups: [], flatItems: [] };
    const inCategory = (entries) =>
      categoryFilter
        ? entries.filter((e) => e.category === categoryFilter)
        : entries;
    const trimmed = query.trim();
    if (trimmed) {
      const hits = inCategory(searchCommands(indexRows, trimmed));
      return { groups: null, flatItems: hits };
    }
    const cats = groupCommandsWithCustom(
      inCategory(listCommandRows(indexRows)),
    );
    return {
      groups: cats,
      flatItems: cats.flatMap((g) => g.entries),
    };
  }, [indexRows, query, categoryFilter]);

  /* 键盘导航范围变化时归位 */
  useEffect(() => {
    setActiveIndex(0);
  }, [query, indexRows, categoryFilter]);

  useEffect(() => {
    const el = itemRefs.current.get(activeIndex);
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const handleSelect = useCallback(
    (entry) => {
      if (!entry) return;
      if (entry.grid) {
        setPickerEntry(entry);
        return;
      }
      if (onInsert(entry, null)) {
        onOpenChange(false);
      }
    },
    [onInsert, onOpenChange],
  );

  const handleKeyDown = (event) => {
    // 网格选择器二级步骤内不劫持方向键 / 回车（由 GridSizePicker 自行处理）
    if (pickerEntry) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, Math.max(0, flatItems.length - 1)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const entry = flatItems[activeIndex];
      if (readOnly && entry) {
        toast.info(t("commandPalette.viewerHint"));
        return;
      }
      handleSelect(entry);
    }
  };

  const renderRow = (entry, globalIdx) => (
    <button
      key={`${entry.category}-${entry.name}`}
      type="button"
      ref={(el) => itemRefs.current.set(globalIdx, el)}
      onMouseEnter={() => setActiveIndex(globalIdx)}
      onClick={() => {
        if (readOnly) {
          toast.info(t("commandPalette.viewerHint"));
          return;
        }
        handleSelect(entry);
      }}
      className={[
        "flex w-full items-center gap-3 rounded-md px-2.5 py-1.5 text-left",
        globalIdx === activeIndex ? "bg-accent text-accent-foreground" : "",
      ].join(" ")}
    >
      <code className="shrink-0 font-mono text-sm font-medium">
        \{entry.name}
      </code>
      <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
        {zhDisplay && entry.zh ? zhDisplay(entry.zh) : ""}
      </span>
      {entry.pkg && (
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
          {entry.pkg}
        </span>
      )}
      {entry.grid && (
        <Table className="size-3.5 shrink-0 text-muted-foreground" />
      )}
      <span className="hidden shrink-0 text-xs text-muted-foreground/70 sm:inline">
        {t(`commandPalette.categories.${entry.category}`)}
      </span>
    </button>
  );

  let cursor = -1;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="top-[12%] translate-y-0 gap-0 sm:max-w-lg"
        onKeyDown={handleKeyDown}
      >
        <DialogHeader>
          <DialogTitle>
            {categoryFilter
              ? t(`commandPalette.categories.${categoryFilter}`)
              : t("commandPalette.title")}
          </DialogTitle>
          <DialogDescription>
            {categoryFilter
              ? t("commandPalette.categoryFilterDescription")
              : t("commandPalette.description")}
          </DialogDescription>
        </DialogHeader>

        {pickerEntry ? (
          <GridSizePicker
            entry={pickerEntry}
            onBack={() => setPickerEntry(null)}
            onConfirm={(size) => {
              // 与列表行一致：viewer 只读可浏览不可插入（§4 权限）
              if (readOnly) {
                toast.info(t("commandPalette.viewerHint"));
                return;
              }
              if (onInsert(pickerEntry, size)) onOpenChange(false);
            }}
          />
        ) : (
          <div className="flex min-h-0 flex-col gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("commandPalette.searchPlaceholder")}
                className="pl-8"
                aria-label={t("commandPalette.searchPlaceholder")}
              />
            </div>

            {readOnly && (
              <p className="rounded-md bg-muted px-2.5 py-1.5 text-xs text-muted-foreground">
                {t("commandPalette.viewerHint")}
              </p>
            )}

            <div className="h-72 overflow-y-auto pr-1" role="listbox">
              {!indexRows && (
                <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">
                  {t("commandPalette.loading")}
                </p>
              )}
              {indexRows && flatItems.length === 0 && (
                <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">
                  {t("commandPalette.empty")}
                </p>
              )}
              {indexRows &&
                groups &&
                groups.map((group) => (
                  <div key={group.id} className="mb-1">
                    <p className="sticky top-0 z-10 bg-popover/95 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                      {t(`commandPalette.categories.${group.id}`)}
                      <span className="ml-1 font-normal opacity-70">
                        {group.entries.length}
                      </span>
                    </p>
                    {group.entries.map((entry) => {
                      cursor += 1;
                      return renderRow(entry, cursor);
                    })}
                  </div>
                ))}
              {indexRows &&
                !groups &&
                flatItems.map((entry) => {
                  cursor += 1;
                  return renderRow(entry, cursor);
                })}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * listCommands 返回的是条目数组；浏览分组前先按原始顺序取全量再交给
 * groupCommandsByCategory（其输出组内保持传入顺序）。
 */
function listCommandRows(indexRows) {
  return listCommands(indexRows, { limit: Number.MAX_SAFE_INTEGER });
}
