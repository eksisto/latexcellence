import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Table2 } from "lucide-react";
import { api } from "@/api/index";
import ImageOCRPage from "@/components/ImageOCRPage";

export default function TableOCRPage() {
  const { t } = useTranslation(["pages", "common"]);

  const renderTableMeta = useCallback((result) => (
    <span className="text-xs text-muted-foreground">
      {(result.confidence * 100).toFixed(0)}{t("pages:tableOcr.confidence")}
    </span>
  ), [t]);

  return (
    <ImageOCRPage
      title={t("pages:tableOcr.title")}
      icon={Table2}
      recognize={api.tableOCR.recognize}
      recognizeButtonText={t("pages:tableOcr.btn")}
      renderResultMeta={renderTableMeta}
    />
  );
}
