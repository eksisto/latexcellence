import { ImageIcon } from "lucide-react";
import { api } from "@/api/index";
import ImageOCRPage from "@/components/ImageOCRPage";

export default function FormulaOCRPage() {
  return (
    <ImageOCRPage
      title="公式 OCR 识别"
      icon={ImageIcon}
      recognize={api.formulaOCR.recognize}
      recognizeButtonText="识别公式"
      renderResultMeta={(result) => (
        <span className="text-xs text-muted-foreground">
          {result.inline ? "行内公式" : "行间公式"} ·{" "}
          {(result.confidence * 100).toFixed(0)}% 置信度
        </span>
      )}
    />
  );
}
