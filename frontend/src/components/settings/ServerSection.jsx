import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

function ServerSection({ server, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.server")}>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.host")}</Label>
          <Input
            value={server.host}
            onChange={(e) => onChange("server.host", e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.port")}</Label>
          <Input
            type="number"
            value={server.port}
            onChange={(e) =>
              onChange("server.port", parseInt(e.target.value, 10) || 8000)
            }
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.viteDevUrl")}</Label>
        <Input
          value={server.vite_dev_url}
          onChange={(e) => onChange("server.vite_dev_url", e.target.value)}
        />
      </div>
      <CheckboxField
        label={t("settings.devMode")}
        checked={server.dev_mode}
        onChange={(v) => onChange("server.dev_mode", v)}
      />
      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.allowedOrigins")}</Label>
        <Input
          placeholder="https://latexcellence.example.com"
          value={(server.allowed_origins || []).join(", ")}
          onChange={(e) =>
            onChange(
              "server.allowed_origins",
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        />
        <p className="text-xs text-muted-foreground">
          {t("settings.allowedOriginsHint")}
        </p>
      </div>
    </ConfigSection>
  );
}

export default memo(ServerSection);
