import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import PDFPreview from "@/components/PDFPreview";

import Minimize from "lucide-react/dist/esm/icons/minimize.mjs";

/**
 * PDF 全屏预览覆盖层：fixed 铺满视口，顶部标题栏 + Escape / 按钮退出。
 * 挂在页面级（非面板内），避免 transform 祖先成为 fixed 包含块。
 * 与 GitDiffPage 全屏预览同源。
 * forwardTarget / onBackwardSync（editor v3 阶段一）可选透传给
 * PdfJsPreview 渲染层；不传时行为与既往一致。
 */
export default function FullscreenPDFPreview({
  url,
  onClose,
  forwardTarget = null,
  onBackwardSync = null,
}) {
  const { t } = useTranslation(["components"]);
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <h2 className="text-sm font-semibold">
          {t("components:editor.fullscreenTitle")}
        </h2>
        <Button variant="ghost" size="sm" onClick={onClose}>
          <Minimize />
          {t("components:editor.quitFullscreen")}
        </Button>
      </div>
      <div className="flex-1 overflow-hidden p-4">
        <div className="h-full">
          <PDFPreview
            url={url}
            forwardTarget={forwardTarget}
            onBackwardSync={onBackwardSync}
          />
        </div>
      </div>
    </div>
  );
}
