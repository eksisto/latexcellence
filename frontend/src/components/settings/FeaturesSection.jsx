import { memo, useEffect, useState } from "react";
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

// 每行一条："字体名 | 别名1, 别名2"（别名可省略）；note 不在编辑器中展示，
// 按字体名从原配置继承，避免覆盖默认清单的用途说明。
function formatFontWhitelistText(entries) {
  return (entries ?? [])
    .map((entry) => {
      const aliases = (entry.aliases ?? []).join(", ");
      return aliases ? `${entry.name} | ${aliases}` : entry.name;
    })
    .join("\n");
}

function parseFontWhitelistText(raw, previous) {
  const previousByName = new Map(
    (previous ?? []).map((entry) => [entry.name, entry]),
  );
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name = "", aliasesRaw = ""] = line
        .split("|")
        .map((part) => part.trim());
      const aliases = aliasesRaw
        ? aliasesRaw
            .split(/[,，]/)
            .map((alias) => alias.trim())
            .filter(Boolean)
        : [];
      return {
        name,
        aliases,
        note: previousByName.get(name)?.note ?? "",
      };
    })
    .filter((entry) => entry.name);
}

function FontWhitelistField({ value, onChange }) {
  const { t } = useTranslation("components");
  const [text, setText] = useState(() => formatFontWhitelistText(value));
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    // 本地无未保存编辑时跟随外部配置；失焦后按解析结果归一化显示
    if (!dirty) setText(formatFontWhitelistText(value));
  }, [value, dirty]);
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">
        {t("settings.templateGenFontWhitelist")}
      </Label>
      <Textarea
        rows={7}
        className="font-mono text-xs"
        value={text}
        placeholder={t("settings.templateGenFontWhitelistPlaceholder")}
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
          onChange(parseFontWhitelistText(e.target.value, value));
        }}
        onBlur={() => setDirty(false)}
      />
      <p className="text-xs text-muted-foreground">
        {t("settings.templateGenFontWhitelistHint")}
      </p>
    </div>
  );
}

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
          <FontWhitelistField
            value={features.template_gen.font_whitelist}
            onChange={(entries) =>
              onChange("features.template_gen.font_whitelist", entries)
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
      {/* 模板库（template-plan 阶段②/③）：enabled + 上传大小上限 */}
      <CheckboxField
        label={t("settings.featureTemplateLibrary")}
        checked={features.template_library.enabled}
        onChange={(v) => onChange("features.template_library.enabled", v)}
      />
      {features.template_library.enabled && (
        <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <div className="space-y-1">
            <Label className="text-xs">
              {t("settings.templateLibraryMaxUploadMb")}
            </Label>
            <Input
              type="number"
              min="1"
              value={features.template_library.max_upload_mb}
              onChange={(e) =>
                onChange(
                  "features.template_library.max_upload_mb",
                  parseInt(e.target.value, 10) || 20,
                )
              }
            />
            <p className="text-xs text-muted-foreground">
              {t("settings.templateLibraryMaxUploadMbHint")}
            </p>
          </div>
        </div>
      )}
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
        label={t("settings.featureGitManage")}
        checked={features.git_manage.enabled}
        onChange={(v) => onChange("features.git_manage.enabled", v)}
      />
      {features.git_manage.enabled && (
        <div className="ml-6 space-y-3 rounded-md border p-2.5 bg-muted/40">
          <div className="space-y-1.5">
            <Label className="text-xs">{t("settings.gitManageDirMaxMb")}</Label>
            <Input
              type="number"
              min="1"
              value={features.git_manage.git_dir_max_mb}
              onChange={(e) =>
                onChange(
                  "features.git_manage.git_dir_max_mb",
                  parseInt(e.target.value, 10) || 200,
                )
              }
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
        <div className="pl-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <CheckboxField
            label={t("settings.featureWritingChatWebSearch")}
            checked={features.writing_chat.web_search_enabled}
            onChange={(v) =>
              onChange("features.writing_chat.web_search_enabled", v)
            }
          />
          <CheckboxField
            label={t("settings.writingChatEvidenceBundle")}
            checked={features.writing_chat.evidence_bundle_enabled}
            onChange={(v) =>
              onChange("features.writing_chat.evidence_bundle_enabled", v)
            }
          />
          {features.writing_chat.evidence_bundle_enabled && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.writingChatEvidenceChars")}
                </Label>
                <Input
                  type="number"
                  min="2000"
                  max="100000"
                  value={features.writing_chat.evidence_max_chars}
                  onChange={(e) =>
                    onChange(
                      "features.writing_chat.evidence_max_chars",
                      parseInt(e.target.value, 10) || 2000,
                    )
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("settings.writingChatEvidenceItems")}
                </Label>
                <Input
                  type="number"
                  min="1"
                  max="50"
                  value={features.writing_chat.evidence_max_items}
                  onChange={(e) =>
                    onChange(
                      "features.writing_chat.evidence_max_items",
                      parseInt(e.target.value, 10) || 1,
                    )
                  }
                />
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 pt-1">
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.writingChatCompressionThreshold")}
              </Label>
              <Input
                type="number"
                min="0"
                value={features.writing_chat.compression_threshold_tokens}
                onChange={(e) =>
                  onChange(
                    "features.writing_chat.compression_threshold_tokens",
                    parseInt(e.target.value, 10) || 0,
                  )
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.writingChatHeadroom")}
              </Label>
              <Input
                type="number"
                min="0"
                value={features.writing_chat.compression_headroom_tokens}
                onChange={(e) =>
                  onChange(
                    "features.writing_chat.compression_headroom_tokens",
                    parseInt(e.target.value, 10) || 0,
                  )
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.writingChatKeepRecentTokens")}
              </Label>
              <Input
                type="number"
                min="0"
                value={features.writing_chat.compression_keep_recent_tokens}
                onChange={(e) =>
                  onChange(
                    "features.writing_chat.compression_keep_recent_tokens",
                    parseInt(e.target.value, 10) || 0,
                  )
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.writingChatKeepRecentTurns")}
              </Label>
              <Input
                type="number"
                min="1"
                value={features.writing_chat.compression_keep_recent_turns}
                onChange={(e) =>
                  onChange(
                    "features.writing_chat.compression_keep_recent_turns",
                    parseInt(e.target.value, 10) || 1,
                  )
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">
                {t("settings.writingChatSummaryBudget")}
              </Label>
              <Input
                type="number"
                min="1"
                value={features.writing_chat.summary_token_budget}
                onChange={(e) =>
                  onChange(
                    "features.writing_chat.summary_token_budget",
                    parseInt(e.target.value, 10) || 1,
                  )
                }
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">
              {t("settings.writingChatSummaryModel")}
            </Label>
            <Input
              value={features.writing_chat.summary_model}
              onChange={(e) =>
                onChange("features.writing_chat.summary_model", e.target.value)
              }
            />
          </div>
        </div>
      )}
      {/* 网页编辑器（editor-design §5.3 / 决策 Q17）：enabled + assistant_enabled */}
      <CheckboxField
        label={t("settings.featureEditor")}
        checked={features.editor.enabled}
        onChange={(v) => onChange("features.editor.enabled", v)}
      />
      {features.editor.enabled && (
        <div className="ml-6 space-y-2 rounded-md border p-2.5 bg-muted/40">
          <CheckboxField
            label={t("settings.featureEditorAssistant")}
            checked={features.editor.assistant_enabled}
            onChange={(v) => onChange("features.editor.assistant_enabled", v)}
          />
          <p className="text-xs text-muted-foreground">
            {t("settings.featureEditorAssistantHint")}
          </p>
        </div>
      )}
    </ConfigSection>
  );
}

export default memo(FeaturesSection);
