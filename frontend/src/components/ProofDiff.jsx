import { memo } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/**
 * 「原文 → 修改后」对照行。
 * 原文用衬线体加删除线（像校对稿上的删改），修改后用加粗。
 * modified 可传字符串或任意 ReactNode（如术语检查里的自定义 Input）。
 *
 * memo（rerender-memo）：结果卡片列表场景下，页面级 state（检查进度、
 * 提示消息、搜索过滤）高频变化时不因父级重渲染而重渲染。
 */
const ProofDiff = memo(function ProofDiff({
  original,
  modified,
  originalLabel,
  modifiedLabel,
  className,
  code = true,
}) {
  const { t } = useTranslation("common");
  return (
    <div
      className={cn(
        "grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm",
        className,
      )}
    >
      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
        {originalLabel ?? t("original")}
      </span>
      <span
        className={cn(
          "font-display line-through decoration-1 text-foreground/55 whitespace-pre-wrap break-all mt-0.5",
          code && "font-mono",
        )}
      >
        {original}
      </span>
      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
        {modifiedLabel ?? t("modified")}
      </span>
      <div className="mt-0.5 min-w-0 whitespace-pre-wrap break-all">
        {typeof modified === "string" ? (
          <span className={cn("font-medium", code && "font-mono")}>
            {modified}
          </span>
        ) : (
          modified
        )}
      </div>
    </div>
  );
});

export default ProofDiff;
