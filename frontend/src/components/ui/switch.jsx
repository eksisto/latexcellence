import { Switch as SwitchPrimitive } from "@base-ui/react/switch";

import { cn } from "@/lib/utils";

function Switch({ className, size = "default", ...props }) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      data-size={size}
      className={cn(
        "peer group/switch relative inline-flex shrink-0 items-center border border-transparent transition-all outline-none after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-[size=default]:h-[19px] data-[size=default]:w-[32px] data-[size=sm]:h-[15px] data-[size=sm]:w-[24px] dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 data-checked:bg-primary data-unchecked:bg-input dark:data-unchecked:bg-input/80 data-disabled:cursor-not-allowed data-disabled:opacity-50 rounded-[7px]",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          // 滑块（Thumb）几何一览（轨道内容盒已扣除 Root 的 1px 透明边框）：
          // - 常态尺寸 14px / 10px，left 2px / 1.5px，开启位移 13px / 10px（暗色与亮色一致）
          // - 仅亮色 + 开启做视觉微调，补偿白色滑块在深色背景上的光渗现象：
          //   缩小一号（13px / 9.5px）、圆角 6px → calc(var(--radius) * 0.5)、位移再右移 0.5px
          "pointer-events-none relative block bg-background ring-0 transition-[translate,width,height,background-color] rounded-sm" +
            " group-data-[size=default]/switch:size-3.5 group-data-[size=sm]/switch:size-2.5" +
            " group-data-[size=default]/switch:data-checked:translate-x-[13.5px] group-data-[size=sm]/switch:data-checked:translate-x-[10.5px]" +
            " dark:group-data-[size=default]/switch:data-checked:translate-x-[13px] dark:group-data-[size=sm]/switch:data-checked:translate-x-[10px]" +
            " group-data-[size=default]/switch:data-unchecked:translate-x-0 group-data-[size=sm]/switch:data-unchecked:translate-x-0" +
            " group-data-[size=default]/switch:data-checked:size-[13px] group-data-[size=sm]/switch:data-checked:size-[9.5px]" +
            " dark:group-data-[size=default]/switch:data-checked:size-3.5 dark:group-data-[size=sm]/switch:data-checked:size-2.5" +
            " group-data-[size=default]/switch:data-checked:rounded-[calc(var(--radius)*0.5)] group-data-[size=sm]/switch:data-checked:rounded-[calc(var(--radius)*0.5)]" +
            " dark:group-data-[size=default]/switch:data-checked:rounded-sm dark:group-data-[size=sm]/switch:data-checked:rounded-sm" +
            " dark:data-checked:bg-primary-foreground group-data-[size=default]/switch:left-[2px] group-data-[size=sm]/switch:left-[1.5px]",
        )}
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
