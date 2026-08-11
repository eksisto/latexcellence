import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import ColorField from "@/components/ColorField";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/api";
import useStore from "@/store/useStore";

function CheckboxField({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 cursor-pointer rounded border-input accent-primary"
      />
      <span className="text-sm">{label}</span>
    </label>
  );
}

function ConfigSection({ title, children }) {
  return (
    <fieldset className="space-y-3 rounded-lg border p-3">
      <legend className="px-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        {title}
      </legend>
      <div className="space-y-3">{children}</div>
    </fieldset>
  );
}

export default function SettingsDialog() {
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [diffOptions, setDiffOptions] = useState({
    markupTypes: [],
    mathMarkups: [],
  });
  const { t, i18n } = useTranslation("components");

  useEffect(() => {
    if (open) {
      setLoading(true);
      Promise.all([
        api.config.get(),
        api.gitDiff
          .getOptions()
          .catch(() => ({ markup_types: [], math_markups: [] })),
      ])
        .then(([configData, optionsData]) => {
          setConfig(configData);
          setDiffOptions({
            markupTypes: optionsData.markup_types || [],
            mathMarkups: optionsData.math_markups || [],
          });
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, [open]);

  function set(path, value) {
    setConfig((prev) => {
      const next = structuredClone(prev);
      const keys = path.split(".");
      let target = next;
      for (let i = 0; i < keys.length - 1; i++) {
        target = target[keys[i]];
      }
      target[keys[keys.length - 1]] = value;
      return next;
    });
  }

  async function handleSave() {
    setSaving(true);
    try {
      await api.config.update(config);
      // 保存成功后更新全局 Store 中的功能开启状态，使主页实时响应
      useStore.getState().setEnabledFeatures({
        workspace: true,
        formula_ocr: config.features.formula_ocr.enabled,
        table_ocr: config.features.table_ocr.enabled,
        template_gen: config.features.template_gen.enabled,
        typo_check: config.features.typo_check.enabled,
        expression_opt: config.features.expression_opt.enabled,
        code_review: config.features.code_review.enabled,
        compile: config.features.compile.enabled,
        git_diff: config.features.git_diff.enabled,
        format_converter: config.features.format_converter.enabled,
        style_modify: config.features.style_modify.enabled,
        terminology_check: config.features.terminology_check.enabled,
        paragraph_translate: config.features.paragraph_translate.enabled,
        writing_chat: config.features.writing_chat.enabled,
        bib_manager: config.features.bib_manager.enabled,
        plot_gen: config.features.plot_gen.enabled,
        project_tools: config.features.function_calling.project_tools.enabled,
        web_search: config.features.function_calling.web_search.enabled,
        scholarly_research:
          config.features.function_calling.scholarly_research.enabled,
      });
      setOpen(false);
      toast.success(t("settings.configSaved"));
    } catch (err) {
      toast.error(t("settings.configSaveFailed"), { description: err.message });
    } finally {
      setSaving(false);
    }
  }

  if (!config) {
    return (
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger render={<Button variant="ghost" size="icon" />}>
          <Settings className="size-5" />
          <span className="sr-only">{t("settings.title")}</span>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("settings.title")}</DialogTitle>
          </DialogHeader>
          <div className="flex items-center justify-center py-12">
            <span className="text-sm text-muted-foreground">
              {loading ? t("settings.loading") : t("settings.clickToLoad")}
            </span>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="ghost" size="icon" />}>
        <Settings className="size-5" />
        <span className="sr-only">{t("settings.title")}</span>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>{t("settings.title")}</DialogTitle>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto space-y-4 pr-1 -mr-1">
          {/* Server */}
          <ConfigSection title={t("settings.server")}>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.host")}</Label>
                <Input
                  value={config.server.host}
                  onChange={(e) => set("server.host", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.port")}</Label>
                <Input
                  type="number"
                  value={config.server.port}
                  onChange={(e) =>
                    set("server.port", parseInt(e.target.value, 10) || 8000)
                  }
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.viteDevUrl")}</Label>
              <Input
                value={config.server.vite_dev_url}
                onChange={(e) => set("server.vite_dev_url", e.target.value)}
              />
            </div>
            <CheckboxField
              label={t("settings.devMode")}
              checked={config.server.dev_mode}
              onChange={(v) => set("server.dev_mode", v)}
            />
          </ConfigSection>

          {/* LLM */}
          <ConfigSection title={t("settings.llm")}>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.apiKey")}</Label>
              <Input
                type="password"
                placeholder="sk-..."
                value={
                  config.llm.api_key && config.llm.api_key !== "****"
                    ? config.llm.api_key
                    : ""
                }
                onChange={(e) => set("llm.api_key", e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.baseUrl")}</Label>
              <Input
                value={config.llm.base_url}
                onChange={(e) => set("llm.base_url", e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.model")}</Label>
                <Input
                  value={config.llm.model}
                  onChange={(e) => set("llm.model", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.temperature")}</Label>
                <Input
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={config.llm.temperature}
                  onChange={(e) =>
                    set("llm.temperature", parseFloat(e.target.value) || 0.3)
                  }
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.maxTokens")}</Label>
                <Input
                  type="number"
                  value={config.llm.max_tokens}
                  onChange={(e) =>
                    set("llm.max_tokens", parseInt(e.target.value, 10) || 4096)
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.requestInterval")}
                </Label>
                <Input
                  type="number"
                  step="0.5"
                  min="0.5"
                  value={config.llm.request_interval}
                  onChange={(e) =>
                    set(
                      "llm.request_interval",
                      parseFloat(e.target.value) || 1.5,
                    )
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.maxConcurrency")}
                </Label>
                <Input
                  type="number"
                  min="1"
                  value={config.llm.max_concurrency}
                  onChange={(e) =>
                    set(
                      "llm.max_concurrency",
                      parseInt(e.target.value, 10) || 5,
                    )
                  }
                />
              </div>
            </div>
          </ConfigSection>

          {/* Features */}
          <ConfigSection title={t("settings.features")}>
            <CheckboxField
              label={t("settings.featureCompile")}
              checked={config.features.compile.enabled}
              onChange={(v) => set("features.compile.enabled", v)}
            />
            {config.features.compile.enabled && (
              <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.compileEngine")}
                  </Label>
                  <Select
                    value={config.features.compile.engine}
                    onValueChange={(v) => set("features.compile.engine", v)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">
                        {t("settings.compileEngineAuto")}
                      </SelectItem>
                      <SelectItem value="pdflatex">pdfLaTeX</SelectItem>
                      <SelectItem value="xelatex">XeLaTeX</SelectItem>
                      <SelectItem value="lualatex">LuaLaTeX</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <CheckboxField
                  label={t("settings.compilePdfa")}
                  checked={config.features.compile.pdfa}
                  onChange={(v) => set("features.compile.pdfa", v)}
                />
              </div>
            )}
            <CheckboxField
              label={t("settings.featureFormulaOcr")}
              checked={config.features.formula_ocr.enabled}
              onChange={(v) => set("features.formula_ocr.enabled", v)}
            />
            {config.features.formula_ocr.enabled && (
              <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
                <CheckboxField
                  label={t("settings.formulaOcrFormatLatex")}
                  checked={config.features.formula_ocr.format_latex}
                  onChange={(v) => set("features.formula_ocr.format_latex", v)}
                />
              </div>
            )}
            <CheckboxField
              label={t("settings.featureTableOcr")}
              checked={config.features.table_ocr.enabled}
              onChange={(v) => set("features.table_ocr.enabled", v)}
            />
            {config.features.table_ocr.enabled && (
              <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
                <CheckboxField
                  label={t("settings.tableOcrFormatLatex")}
                  checked={config.features.table_ocr.format_latex}
                  onChange={(v) => set("features.table_ocr.format_latex", v)}
                />
              </div>
            )}
            <CheckboxField
              label={t("settings.featureFormatConvert")}
              checked={config.features.format_converter.enabled}
              onChange={(v) => set("features.format_converter.enabled", v)}
            />
            <CheckboxField
              label={t("settings.featureTemplateGen")}
              checked={config.features.template_gen.enabled}
              onChange={(v) => set("features.template_gen.enabled", v)}
            />
            {config.features.template_gen.enabled && (
              <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label className="text-xs">
                      {t("settings.templateGenAgentMaxRounds")}
                    </Label>
                    <Input
                      type="number"
                      min="1"
                      max="40"
                      value={config.features.template_gen.agent_max_rounds}
                      onChange={(e) =>
                        set(
                          "features.template_gen.agent_max_rounds",
                          parseInt(e.target.value, 10) || 12,
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">
                      {t("settings.templateGenAgentMaxFiles")}
                    </Label>
                    <Input
                      type="number"
                      min="1"
                      max="200"
                      value={config.features.template_gen.agent_max_files}
                      onChange={(e) =>
                        set(
                          "features.template_gen.agent_max_files",
                          parseInt(e.target.value, 10) || 30,
                        )
                      }
                    />
                  </div>
                </div>
                <CheckboxField
                  label={t("settings.templateGenAgentCompileEnabled")}
                  checked={config.features.template_gen.agent_compile_enabled}
                  onChange={(v) =>
                    set("features.template_gen.agent_compile_enabled", v)
                  }
                />
                <div className="border-t pt-2 mt-1 space-y-2">
                  <CheckboxField
                    label={t("settings.templateGenCleanupEnabled")}
                    checked={config.features.template_gen.cleanup_enabled}
                    onChange={(v) =>
                      set("features.template_gen.cleanup_enabled", v)
                    }
                  />
                  {config.features.template_gen.cleanup_enabled && (
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1">
                        <Label className="text-xs">
                          {t("settings.templateGenCleanupRetentionDays")}
                        </Label>
                        <Input
                          type="number"
                          min="1"
                          max="365"
                          value={
                            config.features.template_gen.cleanup_retention_days
                          }
                          onChange={(e) =>
                            set(
                              "features.template_gen.cleanup_retention_days",
                              parseInt(e.target.value, 10) || 7,
                            )
                          }
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">
                          {t("settings.templateGenCleanupIntervalSeconds")}
                        </Label>
                        <Input
                          type="number"
                          min="60"
                          max="86400"
                          value={
                            config.features.template_gen
                              .cleanup_interval_seconds
                          }
                          onChange={(e) =>
                            set(
                              "features.template_gen.cleanup_interval_seconds",
                              parseInt(e.target.value, 10) || 3600,
                            )
                          }
                        />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
            <CheckboxField
              label={t("settings.featurePlotGen")}
              checked={config.features.plot_gen.enabled}
              onChange={(v) => set("features.plot_gen.enabled", v)}
            />
            {config.features.plot_gen.enabled && (
              <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label className="text-xs">
                      {t("settings.plotGenMaxRounds")}
                    </Label>
                    <Input
                      type="number"
                      min="1"
                      max="50"
                      value={config.features.plot_gen.agent_max_rounds}
                      onChange={(e) =>
                        set(
                          "features.plot_gen.agent_max_rounds",
                          Math.min(
                            Math.max(parseInt(e.target.value, 10) || 30, 1),
                            50,
                          ),
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">
                      {t("settings.plotGenExecTimeout")}
                    </Label>
                    <Input
                      type="number"
                      min="1"
                      value={config.features.plot_gen.sandbox_exec_timeout}
                      onChange={(e) =>
                        set(
                          "features.plot_gen.sandbox_exec_timeout",
                          parseInt(e.target.value, 10) || 60,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.plotGenDefaultStyle")}
                  </Label>
                  <Select
                    value={config.features.plot_gen.default_style}
                    onValueChange={(v) =>
                      set("features.plot_gen.default_style", v)
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {["nature", "science", "cell", "ieee", "acs"].map((s) => (
                        <SelectItem key={s} value={s}>
                          {s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <CheckboxField
                  label={t("settings.plotGenTikzCompile")}
                  checked={config.features.plot_gen.tikz_compile_enabled}
                  onChange={(v) =>
                    set("features.plot_gen.tikz_compile_enabled", v)
                  }
                />
                <CheckboxField
                  label={t("settings.plotGenCleanup")}
                  checked={config.features.plot_gen.cleanup_enabled}
                  onChange={(v) => set("features.plot_gen.cleanup_enabled", v)}
                />
              </div>
            )}
            <CheckboxField
              label={t("settings.featureBibManager")}
              checked={config.features.bib_manager.enabled}
              onChange={(v) => set("features.bib_manager.enabled", v)}
            />
            <CheckboxField
              label={t("settings.featureTypoCheck")}
              checked={config.features.typo_check.enabled}
              onChange={(v) => set("features.typo_check.enabled", v)}
            />
            <CheckboxField
              label={t("settings.featureExpressionOpt")}
              checked={config.features.expression_opt.enabled}
              onChange={(v) => set("features.expression_opt.enabled", v)}
            />
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.featureExpressionOptDefaultPrompt")}
              </Label>
              <Textarea
                rows={3}
                value={config.features.expression_opt.default_prompt}
                onChange={(e) =>
                  set("features.expression_opt.default_prompt", e.target.value)
                }
              />
            </div>
            <CheckboxField
              label={t("settings.featureTerminologyCheck")}
              checked={config.features.terminology_check.enabled}
              onChange={(v) => set("features.terminology_check.enabled", v)}
            />
            <CheckboxField
              label={t("settings.featureParagraphTranslate")}
              checked={config.features.paragraph_translate.enabled}
              onChange={(v) => set("features.paragraph_translate.enabled", v)}
            />
            <CheckboxField
              label={t("settings.featureCodeReview")}
              checked={config.features.code_review.enabled}
              onChange={(v) => set("features.code_review.enabled", v)}
            />
            <CheckboxField
              label={t("settings.featureStyleModify")}
              checked={config.features.style_modify.enabled}
              onChange={(v) => set("features.style_modify.enabled", v)}
            />
            {config.features.style_modify.enabled && (
              <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
                <CheckboxField
                  label={t("settings.styleModifyAgentEnabled")}
                  checked={config.features.style_modify.agent?.enabled ?? true}
                  onChange={(v) =>
                    set("features.style_modify.agent.enabled", v)
                  }
                />
                {config.features.style_modify.agent?.enabled && (
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <Label className="text-xs">
                        {t("settings.styleModifyAgentMaxRounds")}
                      </Label>
                      <Input
                        type="number"
                        min="1"
                        max="50"
                        value={config.features.style_modify.agent.max_rounds}
                        onChange={(e) =>
                          set(
                            "features.style_modify.agent.max_rounds",
                            Math.min(
                              Math.max(parseInt(e.target.value, 10) || 20, 1),
                              50,
                            ),
                          )
                        }
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">
                        {t("settings.styleModifyAgentCompileTimeout")}
                      </Label>
                      <Input
                        type="number"
                        min="1"
                        value={
                          config.features.style_modify.agent
                            .shadow_compile_timeout
                        }
                        onChange={(e) =>
                          set(
                            "features.style_modify.agent.shadow_compile_timeout",
                            parseInt(e.target.value, 10) || 120,
                          )
                        }
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            <CheckboxField
              label={t("settings.featureGitDiff")}
              checked={config.features.git_diff.enabled}
              onChange={(v) => set("features.git_diff.enabled", v)}
            />
            {config.features.git_diff.enabled && (
              <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("settings.gitDiffDefaultEngine")}
                  </Label>
                  <Select
                    value={config.features.git_diff.default_engine}
                    onValueChange={(v) =>
                      set("features.git_diff.default_engine", v)
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="difflib">
                        {t("settings.gitDiffDefaultEngineDifflib")}
                      </SelectItem>
                      <SelectItem value="latexdiff">
                        {t("settings.gitDiffDefaultEngineLatexdiff")}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {config.features.git_diff.default_engine === "latexdiff" && (
                  <>
                    <div className="space-y-1.5">
                      <Label className="text-xs">
                        {t("settings.gitDiffDefaultMarkupType")}
                      </Label>
                      <Select
                        value={config.features.git_diff.default_markup_type}
                        onValueChange={(v) =>
                          set("features.git_diff.default_markup_type", v)
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {diffOptions.markupTypes.map((type) => (
                            <SelectItem key={type} value={type}>
                              {type}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs">
                        {t("settings.gitDiffDefaultMathMarkup")}
                      </Label>
                      <Select
                        value={config.features.git_diff.default_math_markup}
                        onValueChange={(v) =>
                          set("features.git_diff.default_math_markup", v)
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {diffOptions.mathMarkups.map((opt) => (
                            <SelectItem key={opt} value={opt}>
                              {opt}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <ColorField
                    label={t("settings.gitDiffDefaultColorAdd")}
                    value={config.features.git_diff.default_color_add}
                    onChange={(v) =>
                      set("features.git_diff.default_color_add", v)
                    }
                    fallback="#D7E8B1"
                  />
                  <ColorField
                    label={t("settings.gitDiffDefaultColorDel")}
                    value={config.features.git_diff.default_color_del}
                    onChange={(v) =>
                      set("features.git_diff.default_color_del", v)
                    }
                    fallback="#FFA3A3"
                  />
                </div>
              </div>
            )}
            <CheckboxField
              label={t("settings.featureWritingChat")}
              checked={config.features.writing_chat.enabled}
              onChange={(v) => set("features.writing_chat.enabled", v)}
            />
            {config.features.writing_chat.enabled && (
              <div className="pl-6">
                <CheckboxField
                  label={t("settings.featureWritingChatWebSearch")}
                  checked={config.features.writing_chat.web_search_enabled}
                  onChange={(v) =>
                    set("features.writing_chat.web_search_enabled", v)
                  }
                />
              </div>
            )}
          </ConfigSection>

          {/* Function Calling 工具 */}
          <ConfigSection title={t("settings.functionCallingTools")}>
            <CheckboxField
              label={t("settings.featureProjectTools")}
              checked={config.features.function_calling.project_tools.enabled}
              onChange={(v) =>
                set("features.function_calling.project_tools.enabled", v)
              }
            />
            <CheckboxField
              label={t("settings.featureWebSearch")}
              checked={config.features.function_calling.web_search.enabled}
              onChange={(v) =>
                set("features.function_calling.web_search.enabled", v)
              }
            />
            {config.features.function_calling.web_search.enabled && (
              <div className="ml-6 space-y-3">
                {/* 搜索引擎配置 */}
                <fieldset className="rounded border p-2.5 space-y-3">
                  <legend className="px-1 text-xs font-medium">
                    {t("settings.searchEngineConfig")}
                  </legend>

                  <div className="space-y-1.5">
                    <Label className="text-xs">
                      {t("settings.defaultEngine")}
                    </Label>
                    <Select
                      value={
                        config.features.function_calling.web_search
                          .primary_engine
                      }
                      onValueChange={(v) =>
                        set(
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
                      </SelectContent>
                    </Select>
                  </div>

                  {/* DuckDuckGo */}
                  <fieldset className="rounded border p-2.5 space-y-2">
                    <legend className="px-1 text-xs font-medium">
                      DuckDuckGo
                    </legend>
                    <CheckboxField
                      label={t("settings.enabled")}
                      checked={
                        config.features.function_calling.web_search.engines
                          .duckduckgo.enabled
                      }
                      onChange={(v) =>
                        set(
                          "features.function_calling.web_search.engines.duckduckgo.enabled",
                          v,
                        )
                      }
                    />
                  </fieldset>

                  {/* SearXNG */}
                  <fieldset className="rounded border p-2.5 space-y-2">
                    <legend className="px-1 text-xs font-medium">
                      SearXNG
                    </legend>
                    <CheckboxField
                      label={t("settings.enabled")}
                      checked={
                        config.features.function_calling.web_search.engines
                          .searxng.enabled
                      }
                      onChange={(v) =>
                        set(
                          "features.function_calling.web_search.engines.searxng.enabled",
                          v,
                        )
                      }
                    />
                    {config.features.function_calling.web_search.engines.searxng
                      .enabled && (
                      <div className="space-y-1.5">
                        <Label className="text-xs">
                          {t("settings.instanceUrl")}
                        </Label>
                        <Input
                          placeholder="http://localhost:8080"
                          value={
                            config.features.function_calling.web_search.engines
                              .searxng.base_url
                          }
                          onChange={(e) =>
                            set(
                              "features.function_calling.web_search.engines.searxng.base_url",
                              e.target.value,
                            )
                          }
                        />
                      </div>
                    )}
                  </fieldset>
                </fieldset>

                {/* Deep Read */}
                <fieldset className="rounded border p-2.5 space-y-2">
                  <legend className="px-1 text-xs font-medium">
                    Deep Read
                  </legend>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label className="text-xs">
                        {t("settings.deepReadPages")}
                      </Label>
                      <Input
                        type="number"
                        min="0"
                        max="5"
                        value={
                          config.features.function_calling.web_search
                            .deep_read_pages
                        }
                        onChange={(e) =>
                          set(
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
                        value={
                          config.features.function_calling.web_search
                            .deep_read_max_chars
                        }
                        onChange={(e) =>
                          set(
                            "features.function_calling.web_search.deep_read_max_chars",
                            parseInt(e.target.value, 10) || 4000,
                          )
                        }
                      />
                    </div>
                  </div>
                </fieldset>

                {/* Query Rewriting */}
                <CheckboxField
                  label={t("settings.queryRewriting")}
                  checked={
                    config.features.function_calling.web_search.query_rewriting
                      .enabled
                  }
                  onChange={(v) =>
                    set(
                      "features.function_calling.web_search.query_rewriting.enabled",
                      v,
                    )
                  }
                />
              </div>
            )}
            <CheckboxField
              label={t("settings.featureScholarlyResearch")}
              checked={
                config.features.function_calling.scholarly_research.enabled
              }
              onChange={(v) =>
                set("features.function_calling.scholarly_research.enabled", v)
              }
            />
            {config.features.function_calling.scholarly_research.enabled && (
              <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
                {[
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
                  {
                    key: "crossref",
                    label: "Crossref",
                    hintKey: null,
                    hasKey: false,
                  },
                  {
                    key: "arxiv",
                    label: "arXiv",
                    hintKey: null,
                    hasKey: false,
                  },
                  {
                    key: "core",
                    label: "CORE",
                    hintKey: null,
                    hasKey: true,
                  },
                  {
                    key: "unpaywall",
                    label: "Unpaywall",
                    hintKey: "unpaywallApiKeyHint",
                    hasKey: true,
                  },
                  {
                    key: "pubmed",
                    label: "PubMed",
                    hintKey: "pubmedApiKeyHint",
                    hasKey: true,
                  },
                  {
                    key: "datacite",
                    label: "DataCite",
                    hintKey: null,
                    hasKey: false,
                  },
                ].map(({ key, label, hintKey, hasKey }) => (
                  <div key={key} className="space-y-1.5">
                    <CheckboxField
                      label={`${label}${!hasKey ? "" : ""}`}
                      checked={
                        config.features.function_calling.scholarly_research
                          .providers[key].enabled
                      }
                      onChange={(v) =>
                        set(
                          `features.function_calling.scholarly_research.providers.${key}.enabled`,
                          v,
                        )
                      }
                    />
                    {hasKey &&
                      config.features.function_calling.scholarly_research
                        .providers[key].enabled && (
                        <Input
                          type="password"
                          className="ml-5 mt-1 w-[calc(100%-1.25rem)]"
                          placeholder={
                            hintKey ? t(`settings.${hintKey}`) : "API Key"
                          }
                          value={
                            config.features.function_calling.scholarly_research
                              .providers[key].api_key || ""
                          }
                          onChange={(e) =>
                            set(
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

          {/* HTTP */}
          <ConfigSection title={t("settings.http")}>
            <div className="space-y-1.5">
              <Label className="text-xs">User-Agent</Label>
              <Input
                placeholder={t("settings.userAgentHint")}
                value={config.http?.user_agent ?? ""}
                onChange={(e) => set("http.user_agent", e.target.value)}
              />
            </div>
          </ConfigSection>

          {/* Logging */}
          <ConfigSection title={t("settings.logging")}>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">{t("settings.logLevel")}</Label>
                <Select
                  value={config.logging.level}
                  onValueChange={(v) => set("logging.level", v)}
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
                  value={config.logging.dir}
                  onChange={(e) => set("logging.dir", e.target.value)}
                />
              </div>
            </div>
          </ConfigSection>

          {/* Language */}
          <ConfigSection title={t("settings.language")}>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("settings.language")}</Label>
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
            </div>
          </ConfigSection>
        </div>
        <DialogFooter className="mt-4">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? t("settings.saving") : t("settings.saveConfig")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
