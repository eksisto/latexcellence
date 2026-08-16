import { cn } from "@/lib/utils";

/**
 * 共享空状态：细环图标 + 标题 + 描述 + 可选 CTA。
 * 用于「无项目 / 恢复中 / 空结果 / 未生成」等需要方向而非情绪的时刻。
 */
export default function EmptyState({
  icon: Icon,
  title,
  description,
  children,
  className,
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 py-14 text-center",
        className,
      )}
    >
      {Icon && (
        <div className="grid size-14 place-items-center rounded-full border border-border bg-card text-muted-foreground ring-1 ring-foreground/5">
          <Icon className="size-5" />
        </div>
      )}
      <div className="space-y-1.5">
        <h3 className="font-heading text-base font-medium">{title}</h3>
        {description && (
          <p className="mx-auto max-w-sm text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {children ? <div className="mt-1">{children}</div> : null}
    </div>
  );
}
