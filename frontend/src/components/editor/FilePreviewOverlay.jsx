import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import PDFPreview from "@/components/PDFPreview";

import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import Image from "lucide-react/dist/esm/icons/image.mjs";
import X from "lucide-react/dist/esm/icons/x.mjs";

import { isImagePreviewFilePath } from "@/lib/editor/files";

/**
 * 只读文件预览覆盖层：PDF / 位图在编辑器区上方展示，不进入 CM6 缓冲区。
 * 关闭按钮退出，点 .tex 打开时由父级清理路径自动让位。
 */
export default function FilePreviewOverlay({
  filePreviewPath,
  filePreviewUrl,
  onClose,
}) {
  // 组件内自取翻译：此前经 prop 注入但唯一调用方漏传，点击预览即
  // TypeError 白屏；就地 useTranslation 同时消除 t 的 prop drilling
  const { t } = useTranslation();
  if (!filePreviewPath) return null;
  return (
    <div className="absolute inset-0 z-20 flex flex-col overflow-hidden rounded-lg border bg-background">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-1.5">
        <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
          {isImagePreviewFilePath(filePreviewPath) ? (
            <Image className="size-4 shrink-0 text-muted-foreground" />
          ) : (
            <FileText className="size-4 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate" title={filePreviewPath}>
            {filePreviewPath}
          </span>
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          onClick={onClose}
          title={t("common:close")}
        >
          <X className="size-4" />
          <span className="sr-only">{t("common:close")}</span>
        </Button>
      </div>
      <div className="min-h-0 flex-1 p-2">
        {filePreviewUrl ? (
          isImagePreviewFilePath(filePreviewPath) ? (
            <div className="flex h-full items-center justify-center overflow-auto rounded bg-muted/30 p-2">
              <img
                src={filePreviewUrl}
                alt={filePreviewPath}
                className="max-h-full max-w-full object-contain"
              />
            </div>
          ) : (
            <PDFPreview url={filePreviewUrl} />
          )
        ) : (
          <div className="grid h-full place-items-center text-sm text-muted-foreground">
            {t("pages:editor.opening")}
          </div>
        )}
      </div>
    </div>
  );
}
