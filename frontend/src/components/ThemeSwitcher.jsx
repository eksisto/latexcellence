import { useState, useRef, useEffect, useCallback } from "react";
import { Palette, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useThemeManager, getThemeDisplayName } from "@/theme/useThemeManager";

export default function ThemeSwitcher() {
  const { currentTheme, setCurrentTheme, themes } = useThemeManager();
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);

  const handleClickOutside = useCallback((e) => {
    if (containerRef.current && !containerRef.current.contains(e.target)) {
      setOpen(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open, handleClickOutside]);

  return (
    <div ref={containerRef} className="relative">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setOpen((v) => !v)}
            />
          }
        >
          <Palette className="size-5" />
          <span className="sr-only">切换配色主题</span>
        </TooltipTrigger>
        <TooltipContent>切换配色主题</TooltipContent>
      </Tooltip>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-44 rounded-lg border bg-popover p-1 shadow-lg z-50">
          <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
            配色主题
          </div>
          {themes.map((name) => (
            <button
              key={name}
              onClick={() => {
                setCurrentTheme(name);
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <span className="flex-1 text-left">{getThemeDisplayName(name)}</span>
              {currentTheme === name && <Check className="size-4 text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
