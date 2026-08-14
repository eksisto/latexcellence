import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import ConfigSection from "@/components/settings/ConfigSection";

function LoggingSection({ logging, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.logging")}>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.logLevel")}</Label>
          <Select
            value={logging.level}
            onValueChange={(v) => onChange("logging.level", v)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => (
                <SelectItem key={l} value={l}>
                  {l}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.logDir")}</Label>
          <Input
            value={logging.dir}
            onChange={(e) => onChange("logging.dir", e.target.value)}
          />
        </div>
      </div>
    </ConfigSection>
  );
}

export default memo(LoggingSection);
