/**
 * EditorSettingsDialog —— 编辑器偏好设置（editor-plan §9 / §11 最小入口）。
 *
 * 本阶段提供基础开关：软换行、字号即时生效；自动保存开关仅维护数据结构
 * （默认关，决策 Q6），其行为联动（编译前自动保存）属阶段四，故置灰并注明。
 * 偏好导入/导出属阶段四，此处不出现。
 *
 * 注：计划形态为 Popover 弹层，仓库 ui 基座未含 popover 原语，按 shadcn/ui
 * 优先原则改用现有 Dialog 实现，交互语义等价。
 */

import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import useEditorStore from "@/store/editorStore";
import { useShallow } from "zustand/react/shallow";

const FONT_SIZE_OPTIONS = [12, 13, 14, 16, 18, 20];

export default function EditorSettingsDialog({ open, onOpenChange }) {
  const { t } = useTranslation("components");

  const {
    softWrap,
    autoSave,
    fontSize,
    setSoftWrap,
    setAutoSave,
    setFontSize,
  } = useEditorStore(
    useShallow((s) => ({
      softWrap: s.softWrap,
      autoSave: s.autoSave,
      fontSize: s.fontSize,
      setSoftWrap: s.setSoftWrap,
      setAutoSave: s.setAutoSave,
      setFontSize: s.setFontSize,
    })),
  );

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
              disabled
              onCheckedChange={(checked) => setAutoSave(checked === true)}
            />
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            {t("components:editorSettings.autoSaveHint")}
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
        </div>
      </DialogContent>
    </Dialog>
  );
}
