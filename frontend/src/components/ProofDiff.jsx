import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/**
 * 「原文 → 修改后」对照行。
 * 原文用衬线体加删除线（像校对稿上的删改），修改后用加粗。
 * modified 可传字符串或任意 ReactNode（如术语检查里的自定义 Input）。
 */
export default function ProofDiff({
  original,
  modified,
  originalLabel,
  modifiedLabel,
  className,
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
      <span className="font-display line-through decoration-1 text-foreground/55 whitespace-pre-wrap break-all mt-0.5">
        {original}
      </span>
      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
        {modifiedLabel ?? t("modified")}
      </span>
      <div className="mt-0.5 min-w-0 whitespace-pre-wrap break-all">
        {typeof modified === "string" ? (
          <span className="font-medium">{modified}</span>
        ) : (
          modified
        )}
      </div>
    </div>
  );
}
