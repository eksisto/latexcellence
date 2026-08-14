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
import { Textarea } from "@/components/ui/textarea";
import ColorField from "@/components/ColorField";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

function FeaturesSection({ features, onChange, diffOptions }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.features")}>
      <CheckboxField
        label={t("settings.featureCompile")}
        checked={features.compile.enabled}
        onChange={(v) => onChange("features.compile.enabled", v)}
      />
      {features.compile.enabled && (
        <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
          <div className="space-y-1.5">
            <Label className="text-xs">{t("settings.compileEngine")}</Label>
            <Select
              value={features.compile.engine}
              onValueChange={(v) => onChange("features.compile.engine", v)}
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
            checked={features.compile.pdfa}
            onChange={(v) => onChange("features.compile.pdfa", v)}
          />
        </div>
      )}
      <CheckboxField
        label={t("settings.featureFormulaOcr")}
        checked={features.formula_ocr.enabled}
        onChange={(v) => onChange("features.formula_ocr.enabled", v)}
      />
      {features.formula_ocr.enabled && (
        <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <CheckboxField
            label={t("settings.formulaOcrFormatLatex")}
            checked={features.formula_ocr.format_latex}
            onChange={(v) => onChange("features.formula_ocr.format_latex", v)}
          />
        </div>
      )}
      <CheckboxField
        label={t("settings.featureTableOcr")}
        checked={features.table_ocr.enabled}
        onChange={(v) => onChange("features.table_ocr.enabled", v)}
      />
      {features.table_ocr.enabled && (
        <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <CheckboxField
            label={t("settings.tableOcrFormatLatex")}
            checked={features.table_ocr.format_latex}
            onChange={(v) => onChange("features.table_ocr.format_latex", v)}
          />
        </div>
      )}
      <CheckboxField
        label={t("settings.featureFormatConvert")}
        checked={features.format_converter.enabled}
        onChange={(v) => onChange("features.format_converter.enabled", v)}
      />
      <CheckboxField
        label={t("settings.featureTemplateGen")}
        checked={features.template_gen.enabled}
        onChange={(v) => onChange("features.template_gen.enabled", v)}
      />
      {features.template_gen.enabled && (
        <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">
                {t("settings.templateGenAgentMaxRounds")}
              </Label>
              <Input
                type="number"
                min="1"
                max="50"
                value={features.template_gen.agent_max_rounds}
                onChange={(e) =>
                  onChange(
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
                value={features.template_gen.agent_max_files}
                onChange={(e) =>
                  onChange(
                    "features.template_gen.agent_max_files",
                    parseInt(e.target.value, 10) || 30,
                  )
                }
              />
            </div>
          </div>
          <CheckboxField
            label={t("settings.templateGenAgentCompileEnabled")}
            checked={features.template_gen.agent_compile_enabled}
            onChange={(v) =>
              onChange("features.template_gen.agent_compile_enabled", v)
            }
          />
          <div className="border-t pt-2 mt-1 space-y-2">
            <CheckboxField
              label={t("settings.templateGenCleanupEnabled")}
              checked={features.template_gen.cleanup_enabled}
              onChange={(v) =>
                onChange("features.template_gen.cleanup_enabled", v)
              }
            />
            {features.template_gen.cleanup_enabled && (
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label className="text-xs">
                    {t("settings.templateGenCleanupRetentionDays")}
                  </Label>
                  <Input
                    type="number"
                    min="1"
                    max="365"
                    value={features.template_gen.cleanup_retention_days}
                    onChange={(e) =>
                      onChange(
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
                    value={features.template_gen.cleanup_interval_seconds}
                    onChange={(e) =>
                      onChange(
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
        checked={features.plot_gen.enabled}
        onChange={(v) => onChange("features.plot_gen.enabled", v)}
      />
      {features.plot_gen.enabled && (
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
                value={features.plot_gen.agent_max_rounds}
                onChange={(e) =>
                  onChange(
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
                value={features.plot_gen.sandbox_exec_timeout}
                onChange={(e) =>
                  onChange(
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
              value={features.plot_gen.default_style}
              onValueChange={(v) =>
                onChange("features.plot_gen.default_style", v)
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
            checked={features.plot_gen.tikz_compile_enabled}
            onChange={(v) =>
              onChange("features.plot_gen.tikz_compile_enabled", v)
            }
          />
          <CheckboxField
            label={t("settings.plotGenCleanup")}
            checked={features.plot_gen.cleanup_enabled}
            onChange={(v) => onChange("features.plot_gen.cleanup_enabled", v)}
          />
        </div>
      )}
      <CheckboxField
        label={t("settings.featureBibManager")}
        checked={features.bib_manager.enabled}
        onChange={(v) => onChange("features.bib_manager.enabled", v)}
      />
      <CheckboxField
        label={t("settings.featureTypoCheck")}
        checked={features.typo_check.enabled}
        onChange={(v) => onChange("features.typo_check.enabled", v)}
      />
      <CheckboxField
        label={t("settings.featureExpressionOpt")}
        checked={features.expression_opt.enabled}
        onChange={(v) => onChange("features.expression_opt.enabled", v)}
      />
      <div className="space-y-1.5">
        <Label className="text-xs">
          {t("settings.featureExpressionOptDefaultPrompt")}
        </Label>
        <Textarea
          rows={3}
          value={features.expression_opt.default_prompt}
          onChange={(e) =>
            onChange("features.expression_opt.default_prompt", e.target.value)
          }
        />
      </div>
      <CheckboxField
        label={t("settings.featureTerminologyCheck")}
        checked={features.terminology_check.enabled}
        onChange={(v) => onChange("features.terminology_check.enabled", v)}
      />
      <CheckboxField
        label={t("settings.featureParagraphTranslate")}
        checked={features.paragraph_translate.enabled}
        onChange={(v) => onChange("features.paragraph_translate.enabled", v)}
      />
      <CheckboxField
        label={t("settings.featureCodeReview")}
        checked={features.code_review.enabled}
        onChange={(v) => onChange("features.code_review.enabled", v)}
      />
      <CheckboxField
        label={t("settings.featureStyleModify")}
        checked={features.style_modify.enabled}
        onChange={(v) => onChange("features.style_modify.enabled", v)}
      />
      {features.style_modify.enabled && (
        <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <CheckboxField
            label={t("settings.styleModifyAgentEnabled")}
            checked={features.style_modify.agent?.enabled ?? true}
            onChange={(v) => onChange("features.style_modify.agent.enabled", v)}
          />
          {features.style_modify.agent?.enabled && (
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">
                  {t("settings.styleModifyAgentMaxRounds")}
                </Label>
                <Input
                  type="number"
                  min="1"
                  max="50"
                  value={features.style_modify.agent.max_rounds}
                  onChange={(e) =>
                    onChange(
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
                  value={features.style_modify.agent.shadow_compile_timeout}
                  onChange={(e) =>
                    onChange(
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
        checked={features.git_diff.enabled}
        onChange={(v) => onChange("features.git_diff.enabled", v)}
      />
      {features.git_diff.enabled && (
        <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
          <div className="space-y-1.5">
            <Label className="text-xs">
              {t("settings.gitDiffDefaultEngine")}
            </Label>
            <Select
              value={features.git_diff.default_engine}
              onValueChange={(v) =>
                onChange("features.git_diff.default_engine", v)
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
          {features.git_diff.default_engine === "latexdiff" && (
            <>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.gitDiffDefaultMarkupType")}
                </Label>
                <Select
                  value={features.git_diff.default_markup_type}
                  onValueChange={(v) =>
                    onChange("features.git_diff.default_markup_type", v)
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
                  value={features.git_diff.default_math_markup}
                  onValueChange={(v) =>
                    onChange("features.git_diff.default_math_markup", v)
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
              value={features.git_diff.default_color_add}
              onChange={(v) =>
                onChange("features.git_diff.default_color_add", v)
              }
              fallback="#D7E8B1"
            />
            <ColorField
              label={t("settings.gitDiffDefaultColorDel")}
              value={features.git_diff.default_color_del}
              onChange={(v) =>
                onChange("features.git_diff.default_color_del", v)
              }
              fallback="#FFA3A3"
            />
          </div>
        </div>
      )}
      <CheckboxField
        label={t("settings.featureWritingChat")}
        checked={features.writing_chat.enabled}
        onChange={(v) => onChange("features.writing_chat.enabled", v)}
      />
      {features.writing_chat.enabled && (
        <div className="pl-6">
          <CheckboxField
            label={t("settings.featureWritingChatWebSearch")}
            checked={features.writing_chat.web_search_enabled}
            onChange={(v) =>
              onChange("features.writing_chat.web_search_enabled", v)
            }
          />
        </div>
      )}
    </ConfigSection>
  );
}

export default memo(FeaturesSection);
