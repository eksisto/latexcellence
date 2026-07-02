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
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" onClick={toggleTheme}>
          {resolved === "dark" ? (
            <Sun className="size-5" />
          ) : (
            <Moon className="size-5" />
          )}
          <span className="sr-only">切换主题</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {resolved === "dark" ? "切换为浅色模式" : "切换为深色模式"}
      </TooltipContent>
    </Tooltip>
  );
}
