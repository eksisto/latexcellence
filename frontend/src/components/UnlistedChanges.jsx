import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import MarkerText from "./MarkerText";

/**
 * 「模型修改了全文但未列出具体条目」（unlisted_changes）的展示组件，
 * typo_check / expression_opt 两页共用。
 *
 * - UnlistedChangesBadge：卡片头部的琥珀色标记
 * - UnlistedChangesBody：正文琥珀色提示 + 全文修改预览 + 应用/忽略操作
 *
 * 传入的 text 是含 marker（__MATH_0000__ 等）的 corrected / optimized
 * 字段，必须经 MarkerText 渲染；应用时写回的也是这份含 marker 的原始字段。
 */

export function UnlistedChangesBadge({ label }) {
  return (
    <span className="rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 px-1.5 py-0.5 text-xs font-medium">
      {label}
    </span>
  );
}

export function UnlistedChangesBody({
  text,
  message,
  applyLabel,
  onApply,
  onIgnore,
}) {
  const { t } = useTranslation("common");
  return (
    <>
      <div className="rounded border border-amber-500/30 bg-amber-500/10 p-3">
        <p className="text-sm text-amber-700 dark:text-amber-300">{message}</p>
        <div className="mt-2 max-h-40 overflow-y-auto font-sans text-sm leading-relaxed whitespace-pre-wrap text-foreground/80">
          <MarkerText text={text} />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={onApply}>
          {applyLabel}
        </Button>
        <Button size="sm" variant="outline" onClick={onIgnore}>
          {t("ignore")}
        </Button>
      </div>
    </>
  );
}
