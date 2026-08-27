import { useTranslation } from "react-i18next";

import PDFPreview from "@/components/PDFPreview";

import { isImagePreviewFilePath } from "@/lib/editor/files";

/**
 * 二进制只读预览的唯一渲染分支（编辑器覆盖层与工作台文件树共用）：
 *   - 位图（isImagePreviewFilePath）：<img> 居中 contain；
 *   - 其余（PDF）：PDFPreview（iframe）。
 *
 * 契约：填满父容器高度，父级须有确定高度（如 h-full / h-[70vh]）。
 * url 未就绪时按 loadingKey 居中展示加载文案；blob URL 的拉取与释放
 * 由调用方负责（同 EditorPage 模式），本组件只做纯渲染。
 */
export default function BinaryFilePreview({
  filePath,
  url,
  loadingKey = "pages:editor.opening",
}) {
  const { t } = useTranslation();
  if (!url) {
    return (
      <div className="grid h-full place-items-center text-sm text-muted-foreground">
        {t(loadingKey)}
      </div>
    );
  }
  if (isImagePreviewFilePath(filePath)) {
    return (
      <div className="flex h-full items-center justify-center overflow-auto rounded bg-muted/30 p-2">
        <img
          src={url}
          alt={filePath}
          className="max-h-full max-w-full object-contain"
        />
      </div>
    );
  }
  return <PDFPreview url={url} />;
}
