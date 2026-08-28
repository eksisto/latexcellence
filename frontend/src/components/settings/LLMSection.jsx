import { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

/** 解析 JSON 对象；空串视为 {}；非法或非对象返回 undefined（不传播） */
function parseExtraBody(raw) {
  const text = raw.trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch {
    /* fall through */
  }
  return undefined;
}

/** 深合并：patch 的叶子值覆盖 base，嵌套对象递归合并 */
function deepMerge(base, patch) {
  const out = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      out[key] &&
      typeof out[key] === "object" &&
      !Array.isArray(out[key])
    ) {
      out[key] = deepMerge(out[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

/** 深移除：仅删除与 patch 叶子值完全相等的键；嵌套对象删空后一并移除 */
function deepRemove(base, patch) {
  const out = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (!(key in out)) continue;
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      out[key] &&
      typeof out[key] === "object" &&
      !Array.isArray(out[key])
    ) {
      const nested = deepRemove(out[key], value);
      if (Object.keys(nested).length === 0) delete out[key];
      else out[key] = nested;
    } else if (JSON.stringify(out[key]) === JSON.stringify(value)) {
      delete out[key];
    }
  }
  return out;
}

/** 判断 obj 是否深包含 patch 的全部键值（勾选态派生） */
function deepContains(obj, patch) {
  return Object.entries(patch).every(([key, value]) => {
    if (!(key in obj)) return false;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return (
        obj[key] &&
        typeof obj[key] === "object" &&
        !Array.isArray(obj[key]) &&
        deepContains(obj[key], value)
      );
    }
    return JSON.stringify(obj[key]) === JSON.stringify(value);
  });
}

/** 常见厂商私有参数预设（勾选合并 / 取消勾选移除） */
function buildPresets(t) {
  return [
    {
      key: "reasoning_low",
      label: t("settings.llmPresetReasoningLow"),
      body: { reasoning_effort: "low" },
    },
    {
      key: "qwen_no_thinking",
      label: t("settings.llmPresetQwenNoThinking"),
      body: { enable_thinking: false },
    },
    {
      key: "zhipu_budget_low",
      label: t("settings.llmPresetZhipuBudgetLow"),
      body: { thinking: { reasoning_budget: "low" } },
    },
  ];
}

function LLMSection({ llm, onChange }) {
  const { t } = useTranslation("components");
  // extra_body 草稿：编辑中显示原始文本，仅在合法 JSON 时向父级传播对象；
  // 外部重新加载配置（对象引用变化且非本组件发出）时重置草稿
  const [extraBodyDraft, setExtraBodyDraft] = useState(null);
  const lastEmittedRef = useRef(llm.extra_body);
  const extraBodyValid =
    extraBodyDraft === null || parseExtraBody(extraBodyDraft) !== undefined;

  useEffect(() => {
    if (llm.extra_body !== lastEmittedRef.current) {
      lastEmittedRef.current = llm.extra_body;
      setExtraBodyDraft(null);
    }
  }, [llm.extra_body]);

  function handleExtraBodyChange(raw) {
    setExtraBodyDraft(raw);
    const parsed = parseExtraBody(raw);
    if (parsed !== undefined) {
      lastEmittedRef.current = parsed;
      onChange("llm.extra_body", parsed);
    }
  }

  /** 勾选预设：合并进当前对象；取消勾选：移除该预设键值；同步草稿文本 */
  function handlePresetToggle(preset, checked) {
    const current =
      extraBodyDraft !== null
        ? (parseExtraBody(extraBodyDraft) ?? {})
        : llm.extra_body;
    const next = checked
      ? deepMerge(current, preset.body)
      : deepRemove(current, preset.body);
    const text =
      Object.keys(next).length > 0 ? JSON.stringify(next, null, 2) : "";
    setExtraBodyDraft(text);
    lastEmittedRef.current = next;
    onChange("llm.extra_body", next);
  }

  const extraBodyText =
    extraBodyDraft ??
    (llm.extra_body && Object.keys(llm.extra_body).length > 0
      ? JSON.stringify(llm.extra_body, null, 2)
      : "");

  // 勾选态从当前生效对象派生：手动编辑 JSON 到等价内容时复选框自动同步
  const effectiveExtraBody =
    extraBodyDraft !== null
      ? (parseExtraBody(extraBodyDraft) ?? {})
      : (llm.extra_body ?? {});
  const presets = buildPresets(t);

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
              onChange("llm.max_tokens", parseInt(e.target.value, 10) || 16384)
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
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.llmContextWindow")}</Label>
          <Input
            type="number"
            min="0"
            value={llm.context_window_tokens}
            onChange={(e) =>
              onChange(
                "llm.context_window_tokens",
                parseInt(e.target.value, 10) || 0,
              )
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.llmModelsProbeUrl")}</Label>
          <Input
            value={llm.models_probe_url}
            placeholder="http://host:8000/v1"
            onChange={(e) => onChange("llm.models_probe_url", e.target.value)}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label className="text-xs">{t("settings.llmExtraBody")}</Label>
        <textarea
          className={`h-16 w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs ${
            extraBodyValid
              ? "border-input"
              : "border-destructive focus-visible:ring-destructive"
          }`}
          placeholder='{"reasoning_effort": "low"}'
          spellCheck={false}
          value={extraBodyText}
          onChange={(e) => handleExtraBodyChange(e.target.value)}
        />
        {!extraBodyValid && (
          <p className="text-xs text-destructive">
            {t("settings.llmExtraBodyInvalid")}
          </p>
        )}
        <div className="space-y-1.5 pt-0.5">
          <span className="block text-xs text-muted-foreground">
            {t("settings.llmExtraBodyPresets")}
          </span>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
            {presets.map((preset) => (
              <CheckboxField
                key={preset.key}
                label={preset.label}
                checked={deepContains(effectiveExtraBody, preset.body)}
                disabled={!extraBodyValid}
                onChange={(checked) => handlePresetToggle(preset, checked)}
              />
            ))}
          </div>
        </div>
      </div>
    </ConfigSection>
  );
}

export default memo(LLMSection);
