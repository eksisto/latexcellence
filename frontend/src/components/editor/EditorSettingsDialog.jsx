/**
 * EditorSettingsDialog —— 编辑器偏好设置（editor-plan §9 / §11）。
 *
 * 开关：软换行、字号即时生效；自动保存（默认关，决策 Q6）在缓冲区停止
 * 编辑后延迟落盘（行为联动属阶段四，经 save-file 单一路径）。
 *
 * 偏好导入/导出（§11 / 决策 Q19-Q21）：导出 pretty-print 的
 * kind/version/prefs 结构；导入支持选文件或粘贴 JSON 双通道，按
 * version + 字段白名单严格校验（校验逻辑见 lib/editor/preferences.js
 * 纯模块），非法字段静默丢弃，合法字段整体覆盖当前切片，toast 反馈结果。
 *
 * 注：计划形态为 Popover 弹层，仓库 ui 基座未含 popover 原语，按 shadcn/ui
 * 优先原则改用现有 Dialog 实现，交互语义等价。
 */

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import Download from "lucide-react/dist/esm/icons/download.mjs";
import FileUp from "lucide-react/dist/esm/icons/file-up.mjs";
import ClipboardPaste from "lucide-react/dist/esm/icons/clipboard-paste.mjs";

import useEditorStore from "@/store/editorStore";
import { useShallow } from "zustand/react/shallow";
import {
  parsePreferencesImport,
  serializePreferences,
} from "@/lib/editor/preferences";

const FONT_SIZE_OPTIONS = [12, 13, 14, 16, 18, 20];
const EXPORT_FILENAME = "latexcellence-editor-preferences.json";

export default function EditorSettingsDialog({ open, onOpenChange }) {
  const { t } = useTranslation("components");

  const {
    softWrap,
    autoSave,
    autoCloseEnv,
    formulaHover,
    fontSize,
    setSoftWrap,
    setAutoSave,
    setAutoCloseEnv,
    setFormulaHover,
    setFontSize,
    applyPreferences,
  } = useEditorStore(
    useShallow((s) => ({
      softWrap: s.softWrap,
      autoSave: s.autoSave,
      autoCloseEnv: s.autoCloseEnv,
      formulaHover: s.formulaHover,
      fontSize: s.fontSize,
      setSoftWrap: s.setSoftWrap,
      setAutoSave: s.setAutoSave,
      setAutoCloseEnv: s.setAutoCloseEnv,
      setFormulaHover: s.setFormulaHover,
      setFontSize: s.setFontSize,
      applyPreferences: s.applyPreferences,
    })),
  );

  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const fileInputRef = useRef(null);

  const handleExport = () => {
    const prefs = useEditorStore.getState();
    const blob = new Blob(
      [
        serializePreferences({
          autoSave: prefs.autoSave,
          softWrap: prefs.softWrap,
          autoCloseEnv: prefs.autoCloseEnv,
          formulaHover: prefs.formulaHover,
          fontSize: prefs.fontSize,
        }),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = EXPORT_FILENAME;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  /** 导入统一出口：整体覆盖切片并反馈（合法字段生效，非法静默丢弃） */
  const handleImportText = (text) => {
    const result = parsePreferencesImport(text);
    if (!result.ok) {
      toast.error(
        t(`editorSettings.import_${result.reason}`, {
          defaultValue: t("editorSettings.importFailed"),
        }),
      );
      return;
    }
    applyPreferences(result.prefs);
    toast.success(t("editorSettings.importSuccess"));
    setPasteOpen(false);
    setPasteText("");
  };

  const handleImportFile = async (file) => {
    if (!file) return;
    try {
      const text = await file.text();
      handleImportText(text);
    } catch (_) {
      toast.error(t("editorSettings.import_invalid_json"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("components:editorSettings.title")}</DialogTitle>
          <DialogDescription>
            {t("components:editorSettings.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-softwrap" className="text-sm">
              {t("components:editorSettings.softWrap")}
            </Label>
            <Switch
              id="editor-softwrap"
              checked={softWrap}
              onCheckedChange={(checked) => setSoftWrap(checked === true)}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-autosave" className="text-sm">
              {t("components:editorSettings.autoSave")}
            </Label>
            <Switch
              id="editor-autosave"
              checked={autoSave}
              onCheckedChange={(checked) => setAutoSave(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.autoSaveHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-autoclose-env" className="text-sm">
              {t("components:editorSettings.autoCloseEnv")}
            </Label>
            <Switch
              id="editor-autoclose-env"
              checked={autoCloseEnv}
              onCheckedChange={(checked) => setAutoCloseEnv(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.autoCloseEnvHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-formula-hover" className="text-sm">
              {t("components:editorSettings.formulaHover")}
            </Label>
            <Switch
              id="editor-formula-hover"
              checked={formulaHover}
              onCheckedChange={(checked) => setFormulaHover(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.formulaHoverHint")}
          </p>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="editor-font-size" className="text-sm">
              {t("components:editorSettings.fontSize")}
            </Label>
            <Select
              value={String(fontSize)}
              onValueChange={(v) => setFontSize(Number(v))}
            >
              <SelectTrigger id="editor-font-size" className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FONT_SIZE_OPTIONS.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size} px
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 偏好导入/导出（§11）：入口在编辑器设置弹层，不放全局设置对话框 */}
          <div className="space-y-2 rounded-md border p-2.5 bg-muted/40">
            <Label className="text-xs text-muted-foreground">
              {t("components:editorSettings.transferTitle")}
            </Label>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" variant="outline" onClick={handleExport}>
                <Download />
                {t("components:editorSettings.export")}
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
              >
                <FileUp />
                {t("components:editorSettings.importFile")}
              </Button>
              <Button
                size="xs"
                variant="ghost"
                onClick={() => setPasteOpen((v) => !v)}
              >
                <ClipboardPaste />
                {t("components:editorSettings.importPaste")}
              </Button>
              {/* 隐藏文件选择器：导入通道一（选文件） */}
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,application/json"
                className="hidden"
                onChange={(e) => {
                  handleImportFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </div>
            {pasteOpen && (
              <div className="space-y-1.5">
                <Textarea
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                  placeholder='{"kind": "...", "version": 1, "prefs": {...}}'
                  className="min-h-20 font-mono text-xs"
                />
                <Button
                  size="xs"
                  onClick={() => handleImportText(pasteText)}
                  disabled={!pasteText.trim()}
                >
                  {t("components:editorSettings.importApply")}
                </Button>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
