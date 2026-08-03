import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { ScanText } from "lucide-react";
import { api } from "@/api/index";
import ImageOCRPage from "@/components/ImageOCRPage";

export default function FormulaOCRPage() {
  const { t } = useTranslation(["pages", "common"]);

  const renderFormulaMeta = useCallback(
    (result) => (
      <span className="text-xs text-muted-foreground">
        {result.inline
          ? t("pages:formulaOcr.inline")
          : t("pages:formulaOcr.display")}{" "}
        · {(result.confidence * 100).toFixed(0)}
        {t("pages:formulaOcr.confidence")}
      </span>
    ),
    [t],
  );

  return (
    <ImageOCRPage
      title={t("pages:formulaOcr.title")}
      icon={ScanText}
      recognize={api.formulaOCR.recognize}
      recognizeButtonText={t("pages:formulaOcr.btn")}
      renderResultMeta={renderFormulaMeta}
    />
  );
}
