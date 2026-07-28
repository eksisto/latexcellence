import { useState, useCallback } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api } from "@/api/index";

export default function useLatexGeneration({
  formatResult,
  editedMetadata,
  editedCiteKey,
}) {
  const { t } = useTranslation(["pages", "common"]);

  const [latexResult, setLatexResult] = useState(null);
  const [step5Loading, setStep5Loading] = useState(false);
  const [bibFilePath, setBibFilePath] = useState("references");

  const handleGenerateLatex = useCallback(async () => {
    if (!formatResult) {
      toast.error(t("pages:bibManager.step5NeedFormat"));
      return;
    }
    setStep5Loading(true);
    setLatexResult(null);

    try {
      const data = await api.bibManager.generateLatex({
        metadata: { ...editedMetadata, cite_key: editedCiteKey },
        format_info: formatResult,
        bib_file_path: bibFilePath || "references",
      });
      setLatexResult(data);
      toast.success(t("pages:bibManager.step5Success"));
    } catch (e) {
      toast.error(t("pages:bibManager.step5Failed"), {
        description: e.message,
      });
    } finally {
      setStep5Loading(false);
    }
  }, [formatResult, editedMetadata, editedCiteKey, bibFilePath, t]);

  const handleCopyLatex = useCallback(() => {
    if (!latexResult?.full_code) return;
    navigator.clipboard.writeText(latexResult.full_code).then(
      () => toast.success(t("pages:bibManager.copySuccess")),
      () => toast.error(t("pages:bibManager.copyFailed")),
    );
  }, [latexResult, t]);

  return {
    latexResult,
    step5Loading,
    bibFilePath,
    setBibFilePath,
    handleGenerateLatex,
    handleCopyLatex,
  };
}
