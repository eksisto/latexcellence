import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";

/**
 * 通用确认对话框。
 *
 * 基于 base-ui Dialog 构建，用于 destructive / 不可逆操作的二次确认。
 * 默认禁止点击遮罩关闭，避免误触。
 *
 * @param {boolean} open               受控开关
 * @param {(open:boolean)=>void} onOpenChange
 * @param {string} title               标题
 * @param {React.ReactNode} description 描述（可为 JSX）
 * @param {string} confirmText         确认按钮文案，默认 "确认"
 * @param {string} cancelText          取消按钮文案，默认 "取消"
 * @param {() => void|Promise<void>} onConfirm  确认回调（可为 async）
 * @param {"default"|"destructive"} variant  确认按钮样式
 * @param {boolean} confirmLoading     确认中状态（禁用按钮 + spinner）
 * @param {boolean} disableDismiss     是否禁止点击遮罩关闭，默认 true
 */
export default function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText: confirmTextProp,
  cancelText: cancelTextProp,
  onConfirm,
  variant = "default",
  confirmLoading = false,
  disableDismiss = true,
}) {
  const { t } = useTranslation("components");
  const confirmText = confirmTextProp ?? t("confirmDialog.confirm");
  const cancelText = cancelTextProp ?? t("confirmDialog.cancel");

  const handleConfirm = async () => {
    if (onConfirm) await onConfirm();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      disablePointerDismissal={disableDismiss}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? (
            <DialogDescription>{description}</DialogDescription>
          ) : null}
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={confirmLoading}
          >
            {cancelText}
          </Button>
          <Button
            variant={variant}
            onClick={handleConfirm}
            disabled={confirmLoading}
          >
            {confirmLoading ? t("confirmDialog.processing") : confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
