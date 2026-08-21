import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import Languages from "lucide-react/dist/esm/icons/languages.mjs";
import Check from "lucide-react/dist/esm/icons/check.mjs";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import i18n from "@/i18n";
import { normalizeLanguageCode } from "@/i18n/language";

/** 各语言选项的自称（native name），不随当前界面语言翻译 */
const LANGUAGE_OPTIONS = [
  { code: "zh", label: "简体中文" },
  { code: "zh-TW", label: "繁體中文" },
  { code: "en", label: "English" },
];

/**
 * 界面语言切换器（纯前端设置，与后端配置无关）。
 * 常驻右上角工具栏，后端未连接 / 未登录时也可切换；
 * 选择结果由 i18next LanguageDetector 持久化到 localStorage。
 */
export default function LanguageSwitcher() {
  const { t } = useTranslation("components");
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

  const current = normalizeLanguageCode(i18n.language);

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
          <Languages className="size-5" />
          <span className="sr-only">
            {t("languageSwitcher.switchLanguage")}
          </span>
        </TooltipTrigger>
        <TooltipContent>{t("languageSwitcher.switchLanguage")}</TooltipContent>
      </Tooltip>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-44 rounded-lg border bg-popover p-1 shadow-lg z-50">
          <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
            {t("languageSwitcher.languageLabel")}
          </div>
          {LANGUAGE_OPTIONS.map(({ code, label }) => (
            <button
              key={code}
              onClick={() => {
                i18n.changeLanguage(code);
                setOpen(false);
              }}
              className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <span className="flex-1 text-left">{label}</span>
              {current === code && <Check className="size-4 text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
