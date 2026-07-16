import { useCallback } from "react";
import { Table2 } from "lucide-react";
import { api } from "@/api/index";
import ImageOCRPage from "@/components/ImageOCRPage";

export default function TableOCRPage() {
  const renderTableMeta = useCallback((result) => (
    <span className="text-xs text-muted-foreground">
      {(result.confidence * 100).toFixed(0)}% 置信度
    </span>
  ), []);

  return (
    <ImageOCRPage
      title="表格 OCR 识别"
      icon={Table2}
      recognize={api.tableOCR.recognize}
      recognizeButtonText="识别表格"
      renderResultMeta={renderTableMeta}
    />
  );
}
