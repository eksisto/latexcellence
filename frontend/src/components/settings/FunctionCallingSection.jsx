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
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

const SCHOLARLY_PROVIDERS = [
  {
    key: "openalex",
    label: "OpenAlex",
    hintKey: "openAlexApiKeyHint",
    hasKey: true,
  },
  {
    key: "semantic_scholar",
    label: "Semantic Scholar",
    hintKey: "semanticScholarApiKeyHint",
    hasKey: true,
  },
  { key: "crossref", label: "Crossref", hintKey: null, hasKey: false },
  { key: "arxiv", label: "arXiv", hintKey: null, hasKey: false },
  { key: "core", label: "CORE", hintKey: null, hasKey: true },
  {
    key: "unpaywall",
    label: "Unpaywall",
    hintKey: "unpaywallApiKeyHint",
    hasKey: true,
  },
  { key: "pubmed", label: "PubMed", hintKey: "pubmedApiKeyHint", hasKey: true },
  { key: "datacite", label: "DataCite", hintKey: null, hasKey: false },
];

function FunctionCallingSection({ functionCalling, onChange }) {
  const { t } = useTranslation("components");
  const webSearch = functionCalling.web_search;
  const scholarly = functionCalling.scholarly_research;

  return (
    <ConfigSection title={t("settings.functionCallingTools")}>
      <CheckboxField
        label={t("settings.featureProjectTools")}
        checked={functionCalling.project_tools.enabled}
        onChange={(v) =>
          onChange("features.function_calling.project_tools.enabled", v)
        }
      />
      <CheckboxField
        label={t("settings.featureWebSearch")}
        checked={webSearch.enabled}
        onChange={(v) =>
          onChange("features.function_calling.web_search.enabled", v)
        }
      />
      {webSearch.enabled && (
        <div className="ml-6 space-y-3">
          {/* 搜索引擎配置 */}
          <fieldset className="rounded border p-2.5 space-y-3">
            <legend className="px-1 text-xs font-medium">
              {t("settings.searchEngineConfig")}
            </legend>

            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.defaultEngine")}</Label>
              <Select
                value={webSearch.primary_engine}
                onValueChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.primary_engine",
                    v,
                  )
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="duckduckgo">DuckDuckGo</SelectItem>
                  <SelectItem value="searxng">SearXNG</SelectItem>
                  <SelectItem value="tavily">Tavily</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* DuckDuckGo */}
            <fieldset className="rounded border p-2.5 space-y-2">
              <legend className="px-1 text-xs font-medium">DuckDuckGo</legend>
              <CheckboxField
                label={t("settings.enabled")}
                checked={webSearch.engines.duckduckgo.enabled}
                onChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.engines.duckduckgo.enabled",
                    v,
                  )
                }
              />
            </fieldset>

            {/* SearXNG */}
            <fieldset className="rounded border p-2.5 space-y-2">
              <legend className="px-1 text-xs font-medium">SearXNG</legend>
              <CheckboxField
                label={t("settings.enabled")}
                checked={webSearch.engines.searxng.enabled}
                onChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.engines.searxng.enabled",
                    v,
                  )
                }
              />
              {webSearch.engines.searxng.enabled && (
                <div className="space-y-1.5">
                  <Label className="text-xs">{t("settings.instanceUrl")}</Label>
                  <Input
                    placeholder="http://localhost:8080"
                    value={webSearch.engines.searxng.base_url}
                    onChange={(e) =>
                      onChange(
                        "features.function_calling.web_search.engines.searxng.base_url",
                        e.target.value,
                      )
                    }
                  />
                </div>
              )}
            </fieldset>

            {/* Tavily */}
            <fieldset className="rounded border p-2.5 space-y-2">
              <legend className="px-1 text-xs font-medium">Tavily</legend>
              <CheckboxField
                label={t("settings.enabled")}
                checked={webSearch.engines.tavily.enabled}
                onChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.engines.tavily.enabled",
                    v,
                  )
                }
              />
              {webSearch.engines.tavily.enabled && (
                <div className="space-y-1.5">
                  <Label className="text-xs">{t("settings.apiKey")}</Label>
                  <Input
                    type="password"
                    placeholder={t("settings.tavilyApiKeyHint")}
                    value={webSearch.engines.tavily.api_key}
                    onChange={(e) =>
                      onChange(
                        "features.function_calling.web_search.engines.tavily.api_key",
                        e.target.value,
                      )
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("settings.tavilyKeylessHint")}
                  </p>
                </div>
              )}
            </fieldset>
          </fieldset>

          {/* Deep Read */}
          <fieldset className="rounded border p-2.5 space-y-2">
            <legend className="px-1 text-xs font-medium">Deep Read</legend>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.deepReadPages")}</Label>
                <Input
                  type="number"
                  min="0"
                  max="5"
                  value={webSearch.deep_read_pages}
                  onChange={(e) =>
                    onChange(
                      "features.function_calling.web_search.deep_read_pages",
                      parseInt(e.target.value, 10) || 0,
                    )
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.deepReadMaxChars")}
                </Label>
                <Input
                  type="number"
                  min="500"
                  max="20000"
                  step="500"
                  value={webSearch.deep_read_max_chars}
                  onChange={(e) =>
                    onChange(
                      "features.function_calling.web_search.deep_read_max_chars",
                      parseInt(e.target.value, 10) || 10000,
                    )
                  }
                />
              </div>
            </div>
          </fieldset>

          {/* Query Rewriting */}
          <CheckboxField
            label={t("settings.queryRewriting")}
            checked={webSearch.query_rewriting.enabled}
            onChange={(v) =>
              onChange(
                "features.function_calling.web_search.query_rewriting.enabled",
                v,
              )
            }
          />
        </div>
      )}
      <CheckboxField
        label={t("settings.featureScholarlyResearch")}
        checked={scholarly.enabled}
        onChange={(v) =>
          onChange("features.function_calling.scholarly_research.enabled", v)
        }
      />
      {scholarly.enabled && (
        <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
          {SCHOLARLY_PROVIDERS.map(({ key, label, hintKey, hasKey }) => (
            <div key={key} className="space-y-1.5">
              <CheckboxField
                label={label}
                checked={scholarly.providers[key].enabled}
                onChange={(v) =>
                  onChange(
                    `features.function_calling.scholarly_research.providers.${key}.enabled`,
                    v,
                  )
                }
              />
              {hasKey && scholarly.providers[key].enabled && (
                <Input
                  type="password"
                  className="ml-5 mt-1 w-[calc(100%-1.25rem)]"
                  placeholder={hintKey ? t(`settings.${hintKey}`) : "API Key"}
                  value={scholarly.providers[key].api_key || ""}
                  onChange={(e) =>
                    onChange(
                      `features.function_calling.scholarly_research.providers.${key}.api_key`,
                      e.target.value,
                    )
                  }
                />
              )}
            </div>
          ))}
        </div>
      )}
    </ConfigSection>
  );
}

export default memo(FunctionCallingSection);
