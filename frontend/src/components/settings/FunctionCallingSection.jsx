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
          <fieldset className="rounded border bg-muted/40 p-2.5 space-y-3">
            <legend className="px-1 text-xs font-medium">
              {t("settings.searchEngineConfig")}
            </legend>

            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.searchStrategy")}</Label>
              <Select
                value={webSearch.strategy}
                onValueChange={(v) =>
                  onChange("features.function_calling.web_search.strategy", v)
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="legacy">
                    {t("settings.searchStrategyLegacy")}
                  </SelectItem>
                  <SelectItem value="aggregate">
                    {t("settings.searchStrategyAggregate")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

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
            <fieldset className="rounded border bg-muted/40 p-2.5 space-y-2">
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
            <fieldset className="rounded border bg-muted/40 p-2.5 space-y-2">
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
            <fieldset className="rounded border bg-muted/40 p-2.5 space-y-2">
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

          <fieldset className="rounded border bg-muted/40 p-2.5 space-y-3">
            <legend className="px-1 text-xs font-medium">
              {t("settings.searchAggregation")}
            </legend>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.searchRanking")}</Label>
              <Select
                value={webSearch.ranking.mode}
                onValueChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.ranking.mode",
                    v,
                  )
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="rrf">RRF</SelectItem>
                  <SelectItem value="legacy">
                    {t("settings.searchRankingLegacy")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {[
                ["overall_timeout_seconds", "searchOverallTimeout", 1, 120],
                ["per_engine_timeout_seconds", "searchEngineTimeout", 0.5, 60],
                ["max_parallel_queries", "searchParallelQueries", 1, 8],
                ["domain_result_cap", "searchDomainCap", 1, 20],
              ].map(([key, labelKey, min, max]) => (
                <div className="space-y-1.5" key={key}>
                  <Label className="text-xs">{t(`settings.${labelKey}`)}</Label>
                  <Input
                    type="number"
                    min={min}
                    max={max}
                    step={key.includes("timeout") ? 0.5 : 1}
                    value={webSearch[key]}
                    onChange={(e) =>
                      onChange(
                        `features.function_calling.web_search.${key}`,
                        Number(e.target.value),
                      )
                    }
                  />
                </div>
              ))}
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {[
                ["government", "searchBoostGovernment"],
                ["official_docs", "searchBoostOfficialDocs"],
                ["academic", "searchBoostAcademic"],
              ].map(([key, labelKey]) => (
                <div className="space-y-1.5" key={key}>
                  <Label className="text-xs">{t(`settings.${labelKey}`)}</Label>
                  <Input
                    type="number"
                    min="-1"
                    max="1"
                    step="0.005"
                    value={webSearch.ranking.source_type_boosts[key]}
                    onChange={(e) =>
                      onChange(
                        `features.function_calling.web_search.ranking.source_type_boosts.${key}`,
                        Number(e.target.value),
                      )
                    }
                  />
                </div>
              ))}
            </div>
          </fieldset>

          {/* Deep Read */}
          <fieldset className="rounded border bg-muted/40 p-2.5 space-y-2">
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
            <fieldset className="space-y-3 rounded border bg-muted/40 p-2.5">
              <legend className="px-1 text-xs font-medium">
                {t("settings.pageFetchResilience")}
              </legend>
              <div className="grid grid-cols-2 gap-3">
                {[
                  ["max_retries", "pageMaxRetries", 0, 5, 1],
                  ["max_redirects", "pageMaxRedirects", 0, 10, 1],
                  ["per_domain_concurrency", "pageDomainConcurrency", 1, 8, 1],
                  [
                    "min_domain_interval_ms",
                    "pageDomainInterval",
                    0,
                    10000,
                    50,
                  ],
                  [
                    "circuit_failure_threshold",
                    "pageCircuitThreshold",
                    1,
                    20,
                    1,
                  ],
                  [
                    "circuit_cooldown_seconds",
                    "pageCircuitCooldown",
                    1,
                    600,
                    1,
                  ],
                ].map(([key, labelKey, min, max, step]) => (
                  <div className="space-y-1.5" key={key}>
                    <Label className="text-xs">
                      {t(`settings.${labelKey}`)}
                    </Label>
                    <Input
                      type="number"
                      min={min}
                      max={max}
                      step={step}
                      value={webSearch.page_reading[key]}
                      onChange={(e) =>
                        onChange(
                          `features.function_calling.web_search.page_reading.${key}`,
                          Number(e.target.value),
                        )
                      }
                    />
                  </div>
                ))}
              </div>
            </fieldset>
            <fieldset className="space-y-3 rounded border bg-muted/40 p-2.5">
              <legend className="px-1 text-xs font-medium">
                {t("settings.pageBrowserFallback")}
              </legend>
              <CheckboxField
                label={t("settings.pageBrowserEnabled")}
                checked={webSearch.page_reading.browser.enabled}
                onChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.page_reading.browser.enabled",
                    v,
                  )
                }
              />
              {webSearch.page_reading.browser.enabled && (
                <div className="grid grid-cols-2 gap-3">
                  {[
                    ["pool_size", "pageBrowserPoolSize", 1, 4],
                    ["page_timeout_seconds", "pageBrowserTimeout", 3, 60],
                    ["max_pages_per_request", "pageBrowserPageLimit", 1, 3],
                    ["stable_window_ms", "pageBrowserStableWindow", 200, 5000],
                  ].map(([key, labelKey, min, max]) => (
                    <div className="space-y-1.5" key={key}>
                      <Label className="text-xs">
                        {t(`settings.${labelKey}`)}
                      </Label>
                      <Input
                        type="number"
                        min={min}
                        max={max}
                        value={webSearch.page_reading.browser[key]}
                        onChange={(e) =>
                          onChange(
                            `features.function_calling.web_search.page_reading.browser.${key}`,
                            Number(e.target.value),
                          )
                        }
                      />
                    </div>
                  ))}
                </div>
              )}
            </fieldset>
          </fieldset>

          <fieldset className="rounded border bg-muted/40 p-2.5 space-y-3">
            <legend className="px-1 text-xs font-medium">
              {t("settings.pageReading")}
            </legend>
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.pageReadingStrategy")}
              </Label>
              <Select
                value={webSearch.page_reading.strategy}
                onValueChange={(v) =>
                  onChange(
                    "features.function_calling.web_search.page_reading.strategy",
                    v,
                  )
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="legacy">
                    {t("settings.pageReadingLegacy")}
                  </SelectItem>
                  <SelectItem value="structured">
                    {t("settings.pageReadingStructured")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <CheckboxField
              label={t("settings.pageCacheEnabled")}
              checked={webSearch.page_reading.cache_enabled}
              onChange={(v) =>
                onChange(
                  "features.function_calling.web_search.page_reading.cache_enabled",
                  v,
                )
              }
            />
            {webSearch.page_reading.cache_enabled && (
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2 space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.pageCacheDir")}
                  </Label>
                  <Input
                    value={webSearch.page_reading.cache_dir}
                    onChange={(e) =>
                      onChange(
                        "features.function_calling.web_search.page_reading.cache_dir",
                        e.target.value,
                      )
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.pageCacheTtl")}
                  </Label>
                  <Input
                    type="number"
                    min="60"
                    max="604800"
                    value={webSearch.page_reading.cache_ttl_seconds}
                    onChange={(e) =>
                      onChange(
                        "features.function_calling.web_search.page_reading.cache_ttl_seconds",
                        Number(e.target.value),
                      )
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.pageResponseLimit")}
                  </Label>
                  <Input
                    type="number"
                    min="1"
                    max="50"
                    value={webSearch.page_reading.max_response_mb}
                    onChange={(e) =>
                      onChange(
                        "features.function_calling.web_search.page_reading.max_response_mb",
                        Number(e.target.value),
                      )
                    }
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.pageCacheEntryLimit")}
                  </Label>
                  <Input
                    type="number"
                    min="1"
                    max="50"
                    value={webSearch.page_reading.max_cache_entry_mb}
                    onChange={(e) =>
                      onChange(
                        "features.function_calling.web_search.page_reading.max_cache_entry_mb",
                        Number(e.target.value),
                      )
                    }
                  />
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.pageExtractorVersion")}
                </Label>
                <Input
                  value={webSearch.page_reading.extractor_version}
                  onChange={(e) =>
                    onChange(
                      "features.function_calling.web_search.page_reading.extractor_version",
                      e.target.value,
                    )
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.pageQualityThreshold")}
                </Label>
                <Input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={webSearch.page_reading.quality_threshold}
                  onChange={(e) =>
                    onChange(
                      "features.function_calling.web_search.page_reading.quality_threshold",
                      Number(e.target.value),
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
          <div className="space-y-1.5">
            <Label className="text-xs">
              {t("settings.scholarlyRankingMode")}
            </Label>
            <Select
              value={scholarly.ranking_mode}
              onValueChange={(v) =>
                onChange(
                  "features.function_calling.scholarly_research.ranking_mode",
                  v,
                )
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="legacy">
                  {t("settings.scholarlyRankingLegacy")}
                </SelectItem>
                <SelectItem value="rrf">RRF</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <CheckboxField
            label={t("settings.scholarlyFullText")}
            checked={scholarly.full_text_enabled}
            onChange={(v) =>
              onChange(
                "features.function_calling.scholarly_research.full_text_enabled",
                v,
              )
            }
          />
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
