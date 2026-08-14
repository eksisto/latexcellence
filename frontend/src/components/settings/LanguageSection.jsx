import { memo } from "react";
import { useTranslation } from "react-i18next";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import ConfigSection from "@/components/settings/ConfigSection";

function LanguageSection() {
  const { t, i18n } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.language")}>
      <Select
        value={i18n.language}
        onValueChange={(v) => i18n.changeLanguage(v)}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="zh">中文</SelectItem>
          <SelectItem value="en">English</SelectItem>
        </SelectContent>
      </Select>
    </ConfigSection>
  );
}

export default memo(LanguageSection);
