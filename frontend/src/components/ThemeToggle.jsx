import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import Sun from "lucide-react/dist/esm/icons/sun.mjs";
import Moon from "lucide-react/dist/esm/icons/moon.mjs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTheme } from "@/hooks/useTheme";

export default function ThemeToggle() {
  const { t } = useTranslation("components");
  const { resolved, toggleTheme } = useTheme();

  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button variant="ghost" size="icon" onClick={toggleTheme} />}
      >
        {resolved === "dark" ? (
          <Sun className="size-5" />
        ) : (
          <Moon className="size-5" />
        )}
        <span className="sr-only">{t("themeToggle.switchDarkLight")}</span>
      </TooltipTrigger>
      <TooltipContent>
        {resolved === "dark"
          ? t("themeToggle.switchToLight")
          : t("themeToggle.switchToDark")}
      </TooltipContent>
    </Tooltip>
  );
}
