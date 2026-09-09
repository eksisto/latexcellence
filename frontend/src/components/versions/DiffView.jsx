import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/** hunk 头 "@@ -3,7 +3,8 @@ …" → 起始行号（旧/新） */
const HUNK_HEADER_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

const LINE_STYLES = {
  add: {
    row: "bg-emerald-500/10",
    marker: "text-emerald-600 dark:text-emerald-400",
    markerChar: "+",
  },
  del: {
    row: "bg-red-500/10",
    marker: "text-red-600 dark:text-red-400",
    markerChar: "−",
  },
  context: { row: "", marker: "text-muted-foreground/40", markerChar: " " },
};

/**
 * 单文件 unified diff 渲染（自研轻量实现，行数据由后端结构化给出）。
 *
 * - selectable=true 时每个 hunk 头部渲染勾选框（工作区恢复模式）；
 * - 行号列按 hunk 头起始行号在客户端推算；
 * - 内容换行展示（LaTeX 长段落友好），等宽字体保持对齐。
 */
function DiffView({ diff, selectable = false, selected, onToggle }) {
  const { t } = useTranslation(["pages"]);

  const hunks = diff?.hunks ?? [];

  // 每 hunk 的渲染元数据（起始行号客户端推算，避免后端冗余）
  const decorated = useMemo(
    () =>
      hunks.map((hunk) => {
        const match = HUNK_HEADER_RE.exec(hunk.header || "");
        let oldNo = match ? Number(match[1]) : 1;
        let newNo = match ? Number(match[2]) : 1;
        const lines = (hunk.lines ?? []).map((line) => {
          const meta = LINE_STYLES[line.type] ?? LINE_STYLES.context;
          const row = {
            key: `o${oldNo}n${newNo}-${line.type}`,
            oldNo: line.type === "add" ? null : oldNo,
            newNo: line.type === "del" ? null : newNo,
            ...meta,
            text: line.text,
          };
          if (line.type !== "add") oldNo += 1;
          if (line.type !== "del") newNo += 1;
          return row;
        });
        return { hunk, lines };
      }),
    [hunks],
  );

  if (decorated.length === 0) return null;

  return (
    <div className="font-mono text-xs leading-5">
      {decorated.map(({ hunk, lines }, index) => {
        const checked = selectable && selected?.has(index);
        return (
          <div key={hunk.header + index}>
            <div
              className={cn(
                "flex items-center gap-2 border-y bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground",
                selectable && checked && "bg-primary/10",
              )}
            >
              {selectable && (
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => onToggle?.(index)}
                  aria-label={t("pages:versionManage.selectHunk")}
                  className="size-3.5"
                />
              )}
              <span className="truncate" title={hunk.header}>
                {hunk.header}
              </span>
              <span className="ml-auto shrink-0 tabular-nums">
                {hunk.additions > 0 && (
                  <span className="text-emerald-600 dark:text-emerald-400">
                    +{hunk.additions}
                  </span>
                )}
                {hunk.additions > 0 && hunk.deletions > 0 && " "}
                {hunk.deletions > 0 && (
                  <span className="text-red-600 dark:text-red-400">
                    −{hunk.deletions}
                  </span>
                )}
              </span>
            </div>
            {lines.map((line) => (
              <div key={line.key} className={cn("flex items-start", line.row)}>
                <span className="w-10 shrink-0 pr-1.5 text-right tabular-nums text-muted-foreground/50 select-none">
                  {line.oldNo ?? ""}
                </span>
                <span className="w-10 shrink-0 pr-1.5 text-right tabular-nums text-muted-foreground/50 select-none">
                  {line.newNo ?? ""}
                </span>
                <span
                  aria-hidden
                  className={cn("w-3 shrink-0 text-center", line.marker)}
                >
                  {line.markerChar}
                </span>
                <span className="min-w-0 flex-1 [overflow-wrap:anywhere] whitespace-pre-wrap">
                  {line.text || " "}
                </span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export default memo(DiffView);
