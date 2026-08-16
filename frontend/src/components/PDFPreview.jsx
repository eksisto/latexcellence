import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import FileWarning from "lucide-react/dist/esm/icons/file-exclamation-point.mjs";

export default function PDFPreview({ url }) {
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
      <iframe
        src={url}
        className="w-full h-full border-0"
        title={t("pdfPreview.title")}
      />
    </Card>
  );
}
