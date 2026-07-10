import { useState, useEffect } from "react";
import { Settings } from "lucide-react";
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
        className="size-4 rounded border-input accent-primary"
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

  useEffect(() => {
    if (open) {
      setLoading(true);
      api.config
        .get()
        .then(setConfig)
        .catch((err) => console.error("Failed to load config:", err))
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
        template_gen: config.features.template_gen.enabled,
        typo_check: config.features.typo_check.enabled,
        expression_opt: config.features.expression_opt.enabled,
        formatting: config.features.formatting.enabled,
      });
      setOpen(false);
    } catch (err) {
      console.error("Failed to save config:", err);
      alert("保存配置失败：" + err.message);
    } finally {
      setSaving(false);
    }
  }

  if (!config) {
    return (
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <Button variant="ghost" size="icon">
            <Settings className="size-5" />
            <span className="sr-only">设置</span>
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>设置</DialogTitle>
          </DialogHeader>
          <div className="flex items-center justify-center py-12">
            <span className="text-sm text-muted-foreground">
              {loading ? "加载中..." : "点击以加载配置"}
            </span>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon">
          <Settings className="size-5" />
          <span className="sr-only">设置</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>设置</DialogTitle>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto space-y-4 pr-1 -mr-1">
          {/* Server */}
          <ConfigSection title="服务器">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">主机</Label>
                <Input
                  value={config.server.host}
                  onChange={(e) => set("server.host", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">端口</Label>
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
              <Label className="text-xs">Vite 开发服务器地址</Label>
              <Input
                value={config.server.vite_dev_url}
                onChange={(e) => set("server.vite_dev_url", e.target.value)}
              />
            </div>
            <CheckboxField
              label="开发模式"
              checked={config.server.dev_mode}
              onChange={(v) => set("server.dev_mode", v)}
            />
          </ConfigSection>

          {/* LLM */}
          <ConfigSection title="LLM">
            <div className="space-y-1.5">
              <Label className="text-xs">API 密钥</Label>
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
              <Label className="text-xs">接口地址</Label>
              <Input
                value={config.llm.base_url}
                onChange={(e) => set("llm.base_url", e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">模型</Label>
                <Input
                  value={config.llm.model}
                  onChange={(e) => set("llm.model", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">模型温度</Label>
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
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">最大 Token 数</Label>
                <Input
                  type="number"
                  value={config.llm.max_tokens}
                  onChange={(e) =>
                    set("llm.max_tokens", parseInt(e.target.value, 10) || 4096)
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">请求间隔 (s)</Label>
                <Input
                  type="number"
                  step="0.5"
                  min="0.5"
                  value={config.llm.request_interval}
                  onChange={(e) =>
                    set(
                      "llm.request_interval",
                      parseFloat(e.target.value) || 1.5
                    )
                  }
                />
              </div>
            </div>
          </ConfigSection>

          {/* Project */}
          <ConfigSection title="项目">
            <div className="space-y-1.5">
              <Label className="text-xs">自动保存间隔（秒）</Label>
              <Input
                type="number"
                value={config.project.auto_save_interval}
                onChange={(e) =>
                  set(
                    "project.auto_save_interval",
                    parseInt(e.target.value, 10) || 120
                  )
                }
              />
            </div>
            <CheckboxField
              label="修改前自动备份"
              checked={config.project.backup_on_modify}
              onChange={(v) => set("project.backup_on_modify", v)}
            />
          </ConfigSection>

          {/* Features */}
          <ConfigSection title="功能">
            <CheckboxField
              label="文本校对"
              checked={config.features.typo_check.enabled}
              onChange={(v) => set("features.typo_check.enabled", v)}
            />
            <CheckboxField
              label="表达优化"
              checked={config.features.expression_opt.enabled}
              onChange={(v) => set("features.expression_opt.enabled", v)}
            />
            <CheckboxField
              label="公式识别"
              checked={config.features.formula_ocr.enabled}
              onChange={(v) => set("features.formula_ocr.enabled", v)}
            />
            <CheckboxField
              label="模板生成"
              checked={config.features.template_gen.enabled}
              onChange={(v) => set("features.template_gen.enabled", v)}
            />
            <CheckboxField
              label="源码格式化"
              checked={config.features.formatting.enabled}
              onChange={(v) => set("features.formatting.enabled", v)}
            />
            <div className="space-y-1.5">
              <Label className="text-xs">
                 表达优化默认提示词
              </Label>
              <Textarea
                rows={3}
                value={config.features.expression_opt.default_prompt}
                onChange={(e) =>
                  set("features.expression_opt.default_prompt", e.target.value)
                }
              />
            </div>
          </ConfigSection>

          {/* Logging */}
          <ConfigSection title="日志">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">级别</Label>
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
                <Label className="text-xs">日志目录</Label>
                <Input
                  value={config.logging.dir}
                  onChange={(e) => set("logging.dir", e.target.value)}
                />
              </div>
            </div>
          </ConfigSection>
        </div>
        <DialogFooter className="mt-4">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存配置"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
