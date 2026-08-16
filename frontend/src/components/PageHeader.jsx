import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import ArrowLeft from "lucide-react/dist/esm/icons/arrow-left.mjs";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SECTION_BY_GROUP } from "@/lib/writingFlow";
import FloatingBackButton from "@/components/FloatingBackButton";

/**
 * 全站共享页头：返回链接 + [§编号 · 分组] kicker + 页面标题 + 可选描述 + 右侧操作位。
 * - 传 `groupKey`（welcome 分组 key）自动生成 "§3 · 文本审查与优化" 式 kicker；
 * - 或直接传 `kicker` 使用自定义文案。
 */
export default function PageHeader({
  title,
  description,
  groupKey,
  kicker,
  backTo = "/",
  className,
  children,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const sectionNo = groupKey && SECTION_BY_GROUP[groupKey];
  const kickerLabel = sectionNo
    ? `§${sectionNo} · ${t(`pages:welcome.groups.${groupKey}`)}`
    : kicker;

  return (
    <div className={cn("mb-8 flex items-start gap-4", className)}>
      <div className="pt-0.5">
        <Button
          variant="ghost"
          size="icon"
          nativeButton={false}
          render={<Link to={backTo} aria-label={t("common:back")} />}
        >
          <ArrowLeft />
        </Button>
      </div>
      <div className="min-w-0 flex-1">
        {kickerLabel && (
          <p className="font-display text-small-caps mb-1 text-xs text-muted-foreground">
            {kickerLabel}
          </p>
        )}
        <h1 className="truncate text-3xl font-bold tracking-tight leading-tight">
          {title}
        </h1>
        {description && (
          <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {children && (
        <div className="ml-auto flex shrink-0 items-center gap-2 pt-1">
          {children}
        </div>
      )}
      <FloatingBackButton to={backTo} />
    </div>
  );
}
