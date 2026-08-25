/**
 * ConflictDialog —— 保存 409 冲突弹窗（editor-plan §2 / 决策 Q6）。
 *
 * 乐观锁校验失败（baseMd5 与磁盘不符）时展示，提供两条出路：
 *   1. 一键放弃本地修改并重载服务端最新内容（重置缓冲区与 baseMd5）；
 *   2. 本地内容复制到剪贴板逃生（先保住未保存的编辑成果再人工裁决）。
 *
 * 弹窗可关闭但不自动消除脏标记——是否放弃始终由用户显式决定。
 */

import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import Copy from "lucide-react/dist/esm/icons/copy.mjs";
import RotateCcw from "lucide-react/dist/esm/icons/rotate-ccw.mjs";

/**
 * 写剪贴板（逃生通道）。
 *
 * 异步 Clipboard API 仅在安全上下文可用；本项目常经 http://局域网IP 自部署，
 * 该场景下 navigator.clipboard 为 undefined，必须回退到隐藏 textarea +
 * execCommand 同步复制，否则"复制本地内容"逃生按钮在最需要它的部署形态下
 * 永远失败。
 * @param {string} text
 */
async function writeClipboardText(text) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  // 离屏挂载的功能性节点：避免触发页面滚动与视觉闪现
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand("copy")) {
      throw new Error("execCommand('copy') rejected");
    }
  } finally {
    document.body.removeChild(textarea);
  }
}

/**
 * @param {object} props
 * @param {boolean} props.open
 * @param {(open: boolean) => void} props.onOpenChange
 * @param {{ message?: string }|null} props.conflict
 *   后端 409 结构化错误中用于展示的字段（current_md5 由后端附带，前端未消费）
 * @param {string} props.localContent 冲突发生时的本地缓冲区全文（逃生用）
 * @param {() => Promise<boolean>} props.onDiscardAndReload 放弃本地并重载，
 *   返回是否重载成功；失败时弹窗保持打开以保留逃生出口
 */
export default function ConflictDialog({
  open,
  onOpenChange,
  conflict,
  localContent,
  onDiscardAndReload,
}) {
  const { t } = useTranslation("components");
  const [reloading, setReloading] = useState(false);

  const handleCopyLocal = useCallback(async () => {
    try {
      await writeClipboardText(localContent ?? "");
      toast.success(t("components:conflictDialog.copySuccess"));
    } catch (e) {
      toast.error(t("components:conflictDialog.copyFailed"), {
        description: e?.message,
      });
    }
  }, [localContent, t]);

  const handleDiscardAndReload = useCallback(async () => {
    setReloading(true);
    try {
      const ok = await onDiscardAndReload();
      // 重载失败（网络断开 / 文件被删）不关弹窗：脏缓冲区仍在，
      // 用户可重试或先复制逃生，不能假装冲突已解决
      if (ok !== false) onOpenChange(false);
    } finally {
      setReloading(false);
    }
  }, [onDiscardAndReload, onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("components:conflictDialog.title")}</DialogTitle>
          <DialogDescription>
            {t("components:conflictDialog.description")}
            {conflict?.message ? (
              <span className="mt-2 block font-mono text-xs break-all text-muted-foreground">
                {conflict.message}
              </span>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t("components:conflictDialog.hint")}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={handleCopyLocal}>
            <Copy />
            {t("components:conflictDialog.copyLocal")}
          </Button>
          <Button
            variant="destructive"
            onClick={handleDiscardAndReload}
            disabled={reloading}
          >
            <RotateCcw />
            {t("components:conflictDialog.discardAndReload")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
