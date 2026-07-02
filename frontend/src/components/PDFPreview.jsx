import { Card, CardContent } from "@/components/ui/card";
import { FileWarning } from "lucide-react";

export default function PDFPreview({ url }) {
  if (!url) {
    return (
      <Card className="h-full">
        <CardContent className="h-full flex flex-col items-center justify-center gap-2">
          <FileWarning className="size-8 text-muted-foreground/40" />
          <p className="text-muted-foreground/60 text-sm">
            No PDF to preview
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
        title="PDF Preview"
      />
    </Card>
  );
}
