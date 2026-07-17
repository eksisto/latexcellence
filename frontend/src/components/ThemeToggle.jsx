import { Button } from "@/components/ui/button";
import { Sun, Moon } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTheme } from "@/hooks/useTheme";

export default function ThemeToggle() {
  const { resolved, toggleTheme } = useTheme();

  return (
    <Tooltip>
      <TooltipTrigger render={<Button variant="ghost" size="icon" onClick={toggleTheme} />}>
        {resolved === "dark" ? (
          <Sun className="size-5" />
        ) : (
          <Moon className="size-5" />
        )}
        <span className="sr-only">切换亮暗模式</span>
      </TooltipTrigger>
      <TooltipContent>
        {resolved === "dark" ? "切换为亮色模式" : "切换为暗色模式"}
      </TooltipContent>
    </Tooltip>
  );
}
