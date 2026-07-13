import { Table2 } from "lucide-react";
import { api } from "@/api/index";
import ImageOCRPage from "@/components/ImageOCRPage";

export default function TableOCRPage() {
  return (
    <ImageOCRPage
      title="表格 OCR 识别"
      icon={Table2}
      recognize={api.tableOCR.recognize}
      recognizeButtonText="识别表格"
      renderResultMeta={(result) => (
        <span className="text-xs text-muted-foreground">
          {(result.confidence * 100).toFixed(0)}% 置信度
        </span>
      )}
    />
  );
}
