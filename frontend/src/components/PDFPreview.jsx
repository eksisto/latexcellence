import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import PdfJsPreview from "@/components/editor/PdfJsPreview";
import FileWarning from "lucide-react/dist/esm/icons/file-exclamation-point.mjs";

/**
 * PDF 预览（editor v3 阶段一 / 决策 Q2）：iframe 原生预览整体替换为
 * pdf.js 渲染（PdfJsPreview，动态分块懒加载），正向定位高亮与反向取点
 * 都需要渲染层可控（iframe 无法程序化滚动/取点击位置）。
 *
 * 对外 props 兼容原有 {url}；forwardTarget / onBackwardSync 为编辑器
 * SyncTeX 正反向同步的可选通道，不传时为纯预览（其余页面零影响），
 * FullscreenPDFPreview 等既有使用点自动受益于新渲染层与工具条。
 */
export default function PDFPreview({
  url,
  forwardTarget = null,
  onBackwardSync = null,
}) {
  const { t } = useTranslation("components");
  if (!url) {
    return (
      <Card className="h-full">
        <CardContent className="h-full flex flex-col items-center justify-center gap-2">
          <FileWarning className="size-8 text-muted-foreground/40" />
          <p className="text-muted-foreground/60 text-sm">
            {t("pdfPreview.noPreview")}
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card className="h-full overflow-hidden">
      <PdfJsPreview
        url={url}
        forwardTarget={forwardTarget}
        onBackwardSync={onBackwardSync}
      />
    </Card>
  );
}
