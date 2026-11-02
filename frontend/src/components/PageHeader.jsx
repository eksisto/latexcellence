import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import ArrowLeft from "lucide-react/dist/esm/icons/arrow-left.mjs";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import FloatingBackButton from "@/components/FloatingBackButton";
import WorkspaceBadge from "@/components/WorkspaceBadge";

/**
 * 全站共享页头：返回链接 + 页面标题 + 可选描述 + 右侧操作位。
 * - `showWorkspace` 为依赖项目的功能页开启「当前工作区」小字（未打开项目时自动隐藏）。
 */
export default function PageHeader({
  title,
  description,
  showWorkspace = false,
  backTo = "/",
  className,
  children,
}) {
  const { t } = useTranslation("common");

  return (
    <div className={cn("mb-8 flex items-start gap-4", className)}>
      <div className="pt-0.5">
        <Button
          variant="ghost"
          size="icon"
          nativeButton={false}
          render={<Link to={backTo} aria-label={t("back")} />}
        >
          <ArrowLeft />
        </Button>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="min-w-0 truncate text-3xl font-bold tracking-tight leading-tight">
            {title}
          </h1>
          {showWorkspace && <WorkspaceBadge />}
        </div>
        {description && (
          <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {/* mt-6：右侧操作位（ModelBadge 等）避开 AppToolbar 固定工具条
          （top-4 + 32px 图标 = 底缘 48px），徽标顶部落在 56px 留 8px 间隙，
          且与 h1 标题行（52px 起）大致垂直居中 */}
      {children && (
        <div className="ml-auto mt-6 flex shrink-0 items-center gap-2">
          {children}
        </div>
      )}
      <FloatingBackButton to={backTo} />
    </div>
  );
}
