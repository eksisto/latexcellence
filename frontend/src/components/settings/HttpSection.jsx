import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection from "@/components/settings/ConfigSection";

function HttpSection({ http, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.http")}>
      <div className="space-y-1.5">
        <Label className="text-xs">User-Agent</Label>
        <Input
          placeholder={t("settings.userAgentHint")}
          value={http?.user_agent ?? ""}
          onChange={(e) => onChange("http.user_agent", e.target.value)}
        />
      </div>
    </ConfigSection>
  );
}

export default memo(HttpSection);
