import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection from "@/components/settings/ConfigSection";

function LLMSection({ llm, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.llm")}>
      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.apiKey")}</Label>
        <Input
          type="password"
          placeholder="sk-..."
          value={llm.api_key && llm.api_key !== "****" ? llm.api_key : ""}
          onChange={(e) => onChange("llm.api_key", e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.baseUrl")}</Label>
        <Input
          value={llm.base_url}
          onChange={(e) => onChange("llm.base_url", e.target.value)}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.model")}</Label>
          <Input
            value={llm.model}
            onChange={(e) => onChange("llm.model", e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.temperature")}</Label>
          <Input
            type="number"
            step="0.1"
            min="0"
            max="2"
            value={llm.temperature}
            onChange={(e) =>
              onChange("llm.temperature", parseFloat(e.target.value) || 0.3)
            }
          />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.maxTokens")}</Label>
          <Input
            type="number"
            value={llm.max_tokens}
            onChange={(e) =>
              onChange("llm.max_tokens", parseInt(e.target.value, 10) || 8192)
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.requestInterval")}</Label>
          <Input
            type="number"
            step="0.5"
            min="0.5"
            value={llm.request_interval}
            onChange={(e) =>
              onChange(
                "llm.request_interval",
                parseFloat(e.target.value) || 1.5,
              )
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.maxConcurrency")}</Label>
          <Input
            type="number"
            min="1"
            value={llm.max_concurrency}
            onChange={(e) =>
              onChange("llm.max_concurrency", parseInt(e.target.value, 10) || 5)
            }
          />
        </div>
      </div>
    </ConfigSection>
  );
}

export default memo(LLMSection);
