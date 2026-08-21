import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox";
import Check from "lucide-react/dist/esm/icons/check.mjs";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui 风格勾选框（Base UI Checkbox）。
 * 用主题 CSS 变量着色（bg-background/border-input/checked:bg-primary），
 * 颜色变化走全局 300ms transition，亮暗模式切换时与其他元素同步渐变；
 * 对钩指示器常驻挂载，以同曲线透明度过渡与底色渐变保持同步。
 */
function Checkbox({ className, ...props }) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "peer inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-[4px] border border-input bg-background outline-none",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        "data-checked:border-primary data-checked:bg-primary data-checked:text-primary-foreground",
        "data-disabled:cursor-not-allowed data-disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        keepMounted
        data-slot="checkbox-indicator"
        className={cn(
          "pointer-events-none flex items-center justify-center text-current",
          "data-checked:opacity-100 data-unchecked:opacity-0",
          // 对钩淡入淡出与全局背景色过渡同曲线同时长（300ms cubic-bezier(0.4,0,0.2,1)），
          // 避免默认单帧挂载/卸载导致对钩相对底色渐变显得滞后或突兀
          "transition-[opacity] duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]",
        )}
      >
        <Check className="size-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
