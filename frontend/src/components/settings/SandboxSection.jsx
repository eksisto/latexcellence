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
import ConfigSection, {
  CheckboxField,
} from "@/components/settings/ConfigSection";

/** mode → 当前选中项下方的说明文案 key */
const MODE_HINT_KEY = {
  auto: "settings.sandboxModeAutoHint",
  docker: "settings.sandboxModeDockerHint",
  local: "settings.sandboxModeLocalHint",
};

/**
 * 数字输入：默认接受 ≥ min（1）的正整数；allowDecimal 时接受 ≥ min 的小数（CPU 核数）。
 * 与 MultiUserSection 一致采用本地草稿态：清空/非法中间态不立即回弹，失焦时归一化。
 */
function NumberField({
  label,
  value,
  onChange,
  hint,
  min = 1,
  allowDecimal = false,
}) {
  const [draft, setDraft] = useState(() => String(value ?? min));

  // 外部值变化（如重置/保存后回填）时同步草稿
  useEffect(() => {
    setDraft(String(value ?? min));
  }, [value, min]);

  // 解析失败 / 低于下限 / 整数字段混入小数时返回 null（视为未完成输入）
  const parse = (raw) => {
    if (raw === "") return null;
    const n = Number(raw);
    if (Number.isNaN(n) || n < min) return null;
    if (!allowDecimal && !Number.isInteger(n)) return null;
    return n;
  };

  const commit = () => {
    const safe = parse(draft) ?? min;
    if (String(safe) !== draft) setDraft(String(safe));
    onChange(safe);
  };

  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        min={min}
        step={allowDecimal ? 0.1 : 1}
        value={draft}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          // 仅合法完整值即时上抛，""、"1." 等中间态不阻塞输入
          const n = parse(raw);
          if (n !== null) onChange(n);
        }}
        onBlur={commit}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function TextField({ label, value, onChange }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function SandboxSection({ sandbox, onChange }) {
  const { t } = useTranslation("components");
  return (
    <ConfigSection title={t("settings.sandbox")}>
      <p className="text-xs text-muted-foreground">
        {t("settings.sandboxRestartHint")}
      </p>

      {/* 执行模式 */}
      <div className="space-y-1">
        <Label className="text-xs">{t("settings.sandboxMode")}</Label>
        <Select
          value={sandbox.mode}
          onValueChange={(v) => onChange("sandbox.mode", v)}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">
              {t("settings.sandboxModeAuto")}
            </SelectItem>
            <SelectItem value="docker">
              {t("settings.sandboxModeDocker")}
            </SelectItem>
            <SelectItem value="local">
              {t("settings.sandboxModeLocal")}
            </SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          {t(MODE_HINT_KEY[sandbox.mode] ?? MODE_HINT_KEY.auto)}
        </p>
      </div>

      {/* 容器镜像 */}
      <div className="grid grid-cols-1 gap-2">
        <TextField
          label={t("settings.sandboxImageTex")}
          value={sandbox.image_tex}
          onChange={(v) => onChange("sandbox.image_tex", v)}
        />
        <div className="grid grid-cols-2 gap-2">
          <TextField
            label={t("settings.sandboxImageConvert")}
            value={sandbox.image_convert}
            onChange={(v) => onChange("sandbox.image_convert", v)}
          />
          <TextField
            label={t("settings.sandboxImagePlot")}
            value={sandbox.image_plot}
            onChange={(v) => onChange("sandbox.image_plot", v)}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          {t("settings.sandboxImagesHint")}
        </p>
      </div>

      {/* 资源限额 */}
      <div className="grid grid-cols-2 gap-2">
        <NumberField
          label={t("settings.sandboxCpus")}
          value={sandbox.cpus}
          min={0.1}
          allowDecimal
          hint={t("settings.sandboxCpusHint")}
          onChange={(v) => onChange("sandbox.cpus", v)}
        />
        <NumberField
          label={t("settings.sandboxMemoryMb")}
          value={sandbox.memory_mb}
          min={64}
          hint={t("settings.sandboxMemoryMbHint")}
          onChange={(v) => onChange("sandbox.memory_mb", v)}
        />
      </div>

      {/* 进程与临时空间 */}
      <div className="grid grid-cols-3 gap-2">
        <NumberField
          label={t("settings.sandboxPidsLimit")}
          value={sandbox.pids_limit}
          min={16}
          onChange={(v) => onChange("sandbox.pids_limit", v)}
        />
        <NumberField
          label={t("settings.sandboxTmpfsMb")}
          value={sandbox.tmpfs_mb}
          min={32}
          onChange={(v) => onChange("sandbox.tmpfs_mb", v)}
        />
        <NumberField
          label={t("settings.sandboxStartupGraceSeconds")}
          value={sandbox.startup_grace_seconds}
          onChange={(v) => onChange("sandbox.startup_grace_seconds", v)}
        />
      </div>

      {/* 并发控制 */}
      <div className="grid grid-cols-2 gap-2">
        <NumberField
          label={t("settings.sandboxMaxConcurrentGlobal")}
          value={sandbox.max_concurrent_global}
          onChange={(v) => onChange("sandbox.max_concurrent_global", v)}
        />
        <NumberField
          label={t("settings.sandboxMaxConcurrentPerUser")}
          value={sandbox.max_concurrent_per_user}
          onChange={(v) => onChange("sandbox.max_concurrent_per_user", v)}
        />
      </div>

      {/* 容器预创建池（warm pool）：仅 docker 后端生效 */}
      <div className="space-y-1">
        <CheckboxField
          label={t("settings.sandboxWarmPool")}
          checked={sandbox.warm_pool_enabled}
          onChange={(v) => onChange("sandbox.warm_pool_enabled", v)}
        />
        <p className="text-xs text-muted-foreground">
          {t("settings.sandboxWarmPoolHint")}
        </p>
      </div>
    </ConfigSection>
  );
}

export default memo(SandboxSection);
