/**
 * EditorCloseTabDialog —— 关闭脏 tab 三选弹窗（editor-productivity-plan
 * §5.1 / 决策 Q5，阶段一）：保存并关闭 / 放弃改动 / 取消。autoSave 偏好
 * 开启时默认选中「保存并关闭」。
 *
 * 弹窗不做「点外关闭」静默吞掉裁决：Escape / 点击遮罩一律走「取消」，
 * 脏缓冲区保持原样，交由用户显式决定。
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import RotateCcw from "lucide-react/dist/esm/icons/rotate-ccw.mjs";
import Save from "lucide-react/dist/esm/icons/save.mjs";
import X from "lucide-react/dist/esm/icons/x.mjs";

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {string|null} props.tabPath 关闭目标 tab（弹窗归属）
 * @param {boolean} props.autoSaveDefault autoSave 偏好开启时默认聚焦「保存并关闭」
 * @param {(open: boolean) => void} props.onOpenChange（false 即取消）
 * @param {(path: string) => Promise<boolean>} props.onSaveAndClose
 * @param {(path: string) => void} props.onDiscardAndClose
 */
export default function EditorCloseTabDialog({
  open,
  tabPath,
  autoSaveDefault = false,
  onOpenChange,
  onSaveAndClose,
  onDiscardAndClose,
}) {
  const { t } = useTranslation("components");
  const [busy, setBusy] = useState(false);

  // 弹窗打开时重置焦点态（autoSave 开启 → 默认「保存并关闭」）
  const [saveFocused, setSaveFocused] = useState(false);
  useEffect(() => {
    if (open) setSaveFocused(autoSaveDefault);
  }, [open, autoSaveDefault]);

  const handleSaveAndClose = async () => {
    if (!tabPath || busy) return;
    setBusy(true);
    try {
      const ok = await onSaveAndClose(tabPath);
      // 保存失败（409 → 冲突弹窗接管 / 网络失败）：tab 保留脏态，不关闭
      if (ok) onOpenChange(false);
    } finally {
      setBusy(false);
    }
  };

  const handleDiscard = () => {
    if (!tabPath || busy) return;
    onDiscardAndClose(tabPath);
    onOpenChange(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onOpenChange(false); // Escape / 遮罩点击 = 取消
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("editor.closeTabTitle")}</DialogTitle>
          <DialogDescription>
            {t("editor.closeTabDescription")}
            {tabPath ? (
              <span className="mt-2 block font-mono text-xs break-all text-muted-foreground">
                {tabPath}
              </span>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={handleSaveAndClose}
            disabled={busy}
            className={saveFocused ? "ring-2 ring-ring/50" : undefined}
            autoFocus={saveFocused}
          >
            {busy ? <Loader2 className="animate-spin" /> : <Save />}
            {t("editor.closeTabSaveAndClose")}
          </Button>
          <Button variant="destructive" onClick={handleDiscard} disabled={busy}>
            <RotateCcw />
            {t("editor.closeTabDiscard")}
          </Button>
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            <X />
            {t("editor.closeTabCancel")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
