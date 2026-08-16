import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import ScanText from "lucide-react/dist/esm/icons/scan-text.mjs";
import { api } from "@/api/index";
import ImageOCRPage from "@/components/ImageOCRPage";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { wrapLatexForPaste } from "@/utils/formulaWrap";

export default function FormulaOCRPage() {
  const { t } = useTranslation(["pages", "common"]);

  const handleToggleInline = useCallback((setResult, inline) => {
    setResult((prev) => {
      if (!prev || prev.inline === inline) return prev;
      return { ...prev, inline, latex: wrapLatexForPaste(prev.latex, inline) };
    });
  }, []);

  const renderFormulaMeta = useCallback(
    (result, setResult) => {
      const confidence = (
        <span className="text-xs text-muted-foreground">
          · {(result.confidence * 100).toFixed(0)}
          {t("pages:formulaOcr.confidence")}
        </span>
      );
      if (result.inline_source === "default") {
        return (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className="text-xs text-muted-foreground">
              {t("pages:formulaOcr.undecided")}
            </span>
            <Tabs
              value={result.inline ? "inline" : "display"}
              onValueChange={(value) =>
                handleToggleInline(setResult, value === "inline")
              }
            >
              <TabsList className="h-7">
                <TabsTrigger value="inline" className="px-2 py-0.5 text-xs">
                  {t("pages:formulaOcr.inline")}
                </TabsTrigger>
                <TabsTrigger value="display" className="px-2 py-0.5 text-xs">
                  {t("pages:formulaOcr.display")}
                </TabsTrigger>
              </TabsList>
            </Tabs>
            {confidence}
          </div>
        );
      }
      return (
        <span className="text-xs text-muted-foreground">
          {result.inline
            ? t("pages:formulaOcr.inline")
            : t("pages:formulaOcr.display")}{" "}
          {confidence}
        </span>
      );
    },
    [t, handleToggleInline],
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
