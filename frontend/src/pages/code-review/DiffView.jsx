import React from "react";
import MarkerText from "@/components/MarkerText";
import { cn } from "@/lib/utils";

/** 行级 diff 视图：+ / − 前缀 + 行着色，用于展示用户对定稿的修改。 */
const DiffView = React.memo(({ parts }) => (
  <div className="font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
    {parts?.map((part, i) => {
      const lines = part.value.split("\n");
      return lines.map((line, j) => {
        if (j === lines.length - 1 && line === "") return null;
        return (
          <div
            key={`${i}-${j}`}
            className={cn(
              "flex px-3 py-px",
              part.added && "bg-green-500/10",
              part.removed && "bg-red-500/10",
            )}
          >
            <span
              className={cn(
                "w-5 shrink-0 select-none",
                part.added && "text-green-600 dark:text-green-400",
                part.removed && "text-red-600 dark:text-red-400",
                !part.added && !part.removed && "text-muted-foreground/40",
              )}
            >
              {part.added ? "+" : part.removed ? "−" : " "}
            </span>
            <span
              className={cn(
                "whitespace-pre-wrap break-all",
                part.added && "text-green-800 dark:text-green-200",
                part.removed && "text-red-800 dark:text-red-200",
              )}
            >
              <MarkerText text={line} />
            </span>
          </div>
        );
      });
    })}
  </div>
));

export default DiffView;
