import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection from "@/components/settings/ConfigSection";

function SecuritySection({ security, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.security")}>
      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.accessToken")}</Label>
        <Input
          type="password"
          placeholder={t("settings.accessTokenPlaceholder")}
          value={security?.access_token || ""}
          onChange={(e) => onChange("security.access_token", e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          {t("settings.accessTokenHint")}
        </p>
      </div>
    </ConfigSection>
  );
}

export default memo(SecuritySection);
