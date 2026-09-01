import { useCallback, useRef, useState, useEffect } from "react";
import { toast } from "sonner";
import Settings from "lucide-react/dist/esm/icons/settings.mjs";
import { useTranslation } from "react-i18next";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/api";
import ConnectionPanel from "@/components/ConnectionPanel";
import ServerSection from "@/components/settings/ServerSection";
import SecuritySection from "@/components/settings/SecuritySection";
import LLMSection from "@/components/settings/LLMSection";
import ModelManagementSection from "@/components/settings/ModelManagementSection";
import FeaturesSection from "@/components/settings/FeaturesSection";
import FunctionCallingSection from "@/components/settings/FunctionCallingSection";
import HttpSection from "@/components/settings/HttpSection";
import LoggingSection from "@/components/settings/LoggingSection";
import MultiUserSection from "@/components/settings/MultiUserSection";
import SandboxSection from "@/components/settings/SandboxSection";
import SmtpSection from "@/components/settings/SmtpSection";
import useStore from "@/store/useStore";
import useUiStore, { SETTINGS_SECTION_MODELS } from "@/store/uiStore";
import { isMultiUser } from "@/api/connection";

/** 多用户模式：连接（基地址/令牌）由服务器统一管理，隐藏设置入口 */
function ConnectionSettingsSlot({ open }) {
  const { t } = useTranslation("components");
  if (!isMultiUser()) return <ConnectionPanel open={open} />;
  return (
    <div className="rounded-lg border border-border bg-muted/40 p-3">
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t("settings.connection")}
      </span>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {t("settings.multiUserConnectionNote")}
      </p>
    </div>
  );
}

export default function SettingsDialog() {
  // 开关状态收口在 uiStore：ModelBadge 等深层组件可程序化打开并定位分区
  const open = useUiStore((s) => s.settingsDialog.open);
  const pendingSection = useUiStore((s) => s.settingsDialog.section);
  const setOpen = useUiStore((s) => s.setSettingsDialogOpen);
  const clearSettingsSection = useUiStore((s) => s.clearSettingsSection);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [diffOptions, setDiffOptions] = useState({
    markupTypes: [],
    mathMarkups: [],
  });
  const [configLoadFailed, setConfigLoadFailed] = useState(false);
  const modelsSectionRef = useRef(null);
  const { t } = useTranslation("components");

  useEffect(() => {
    if (open) {
      // 每次打开都重取配置并重置草稿：分区组件（含模型管理的服务端
      // 档案 id 快照）随最新服务端数据重新挂载，避免跨会话陈旧草稿
      setConfig(null);
      setConfigLoadFailed(false);
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
        .catch(() => setConfigLoadFailed(true))
        .finally(() => setLoading(false));
    }
  }, [open]);

  // 程序化打开时定位到指定分区（multi-model-plan §7.2）：等配置与分区
  // 渲染完成后再滚动，随后清除标记，避免之后常规打开重复定位
  useEffect(() => {
    if (!open || pendingSection !== SETTINGS_SECTION_MODELS || !config) {
      return undefined;
    }
    const raf = requestAnimationFrame(() => {
      modelsSectionRef.current?.scrollIntoView({
        block: "start",
        behavior: "smooth",
      });
      clearSettingsSection();
    });
    return () => cancelAnimationFrame(raf);
  }, [open, pendingSection, config, clearSettingsSection]);

  const set = useCallback((path, value) => {
    setConfig((prev) => {
      // 只复制变更路径上的分支，未触及的分区保持引用不变，
      // 使 memo 化的分区组件在输入其他分区时跳过重渲染
      const keys = path.split(".");
      const next = { ...prev };
      let target = next;
      for (let i = 0; i < keys.length - 1; i++) {
        target = target[keys[i]] = { ...target[keys[i]] };
      }
      target[keys[keys.length - 1]] = value;
      return next;
    });
  }, []);

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
        template_library: config.features.template_library.enabled,
        typo_check: config.features.typo_check.enabled,
        expression_opt: config.features.expression_opt.enabled,
        code_review: config.features.code_review.enabled,
        compile: config.features.compile.enabled,
        git_diff: config.features.git_diff.enabled,
        git_manage: config.features.git_manage.enabled,
        format_converter: config.features.format_converter.enabled,
        style_modify: config.features.style_modify.enabled,
        terminology_check: config.features.terminology_check.enabled,
        paragraph_translate: config.features.paragraph_translate.enabled,
        writing_chat: config.features.writing_chat.enabled,
        bib_manager: config.features.bib_manager.enabled,
        plot_gen: config.features.plot_gen.enabled,
        editor: config.features.editor.enabled,
        editor_assistant: config.features.editor.assistant_enabled,
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
          {configLoadFailed ? (
            <div className="space-y-4 py-2">
              <p className="text-sm text-muted-foreground">
                {t("settings.configLoadFailed")}
              </p>
              <ConnectionSettingsSlot open={open} />
            </div>
          ) : (
            <div className="flex items-center justify-center py-12">
              <span className="text-sm text-muted-foreground">
                {loading ? t("settings.loading") : t("settings.clickToLoad")}
              </span>
            </div>
          )}
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

        <div className="mt-2 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1 -mr-1">
          {/* 后端连接（多用户模式隐藏编辑入口） */}
          <ConnectionSettingsSlot open={open} />

          {/* 后端配置：标签 + 分隔线 */}
          <div className="flex items-center gap-3 pl-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("settings.backendConfig")}
            </span>
            <div className="h-px flex-1 bg-border" />
          </div>

          <ServerSection server={config.server} onChange={set} />
          <SecuritySection security={config.security} onChange={set} />
          <LLMSection llm={config.llm} onChange={set} />
          <div ref={modelsSectionRef} className="scroll-mt-4">
            <ModelManagementSection llm={config.llm} onChange={set} />
          </div>
          <FeaturesSection
            features={config.features}
            onChange={set}
            diffOptions={diffOptions}
          />
          <FunctionCallingSection
            functionCalling={config.features.function_calling}
            onChange={set}
          />
          <HttpSection http={config.http} onChange={set} />
          <LoggingSection logging={config.logging} onChange={set} />
          <MultiUserSection multiUser={config.multi_user} onChange={set} />
          <SandboxSection sandbox={config.sandbox} onChange={set} />
          <SmtpSection smtp={config.smtp} onChange={set} />
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
