import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Palette, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useThemeManager } from "@/theme/useThemeManager";

export default function ThemeSwitcher() {
  const { t } = useTranslation("components");
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
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
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
          <span className="sr-only">{t("themeSwitcher.switchTheme")}</span>
        </TooltipTrigger>
        <TooltipContent>{t("themeSwitcher.switchTheme")}</TooltipContent>
      </Tooltip>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-44 rounded-lg border bg-popover p-1 shadow-lg z-50">
          <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
            {t("themeSwitcher.themeLabel")}
          </div>
          {themes.map((name) => (
            <button
              key={name}
              onClick={() => {
                setCurrentTheme(name);
                setOpen(false);
              }}
              className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <span className="flex-1 text-left">
                {t(`themeNames.${name}`, { defaultValue: name })}
              </span>
              {currentTheme === name && (
                <Check className="size-4 text-primary" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
