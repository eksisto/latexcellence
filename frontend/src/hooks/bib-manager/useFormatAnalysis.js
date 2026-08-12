import { useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api } from "@/api/index";

export default function useFormatAnalysis() {
  const { t } = useTranslation(["pages", "common"]);

  const [example, setExample] = useState("");
  const [formatResult, setFormatResult] = useState(null);
  const [step1Loading, setStep1Loading] = useState(false);
  const [savedFormats, setSavedFormats] = useState([]);
  const [selectedFormatName, setSelectedFormatName] = useState("");
  const [isEditingFormat, setIsEditingFormat] = useState(false);
  const [editedFormatInfo, setEditedFormatInfo] = useState(null);
  const [savingFormat, setSavingFormat] = useState(false);

  useEffect(() => {
    api.bibManager.formats
      .list()
      .then((data) => setSavedFormats(data.formats || []))
      .catch(() => {});
  }, []);

  const handleAnalyzeFormat = useCallback(async () => {
    if (!example.trim()) return;
    setStep1Loading(true);
    setFormatResult(null);
    try {
      const data = await api.bibManager.analyzeFormat(example.trim());
      setFormatResult(data);
      if (data.confidence === "low") {
        toast.warning(
          t("pages:bibManager.lowConfidence", { name: data.format_name }),
          { description: t("pages:bibManager.lowConfidenceHint") },
        );
      } else {
        toast.success(
          t("pages:bibManager.analyzeFormatSuccess", {
            name: data.format_name,
          }),
        );
      }
    } catch (e) {
      toast.error(t("pages:bibManager.analyzeFormatFailed"), {
        description: e.message,
      });
    } finally {
      setStep1Loading(false);
    }
  }, [example, t]);

  const handleLoadSavedFormat = useCallback(
    async (name) => {
      if (!name) return;
      try {
        const data = await api.bibManager.formats.load(name);
        setFormatResult(data);
        setEditedFormatInfo(data);
        setSelectedFormatName(name);
        toast.success(
          t("pages:bibManager.loadFormatSuccess", { name: data.format_name }),
        );
      } catch (e) {
        toast.error(t("pages:bibManager.loadFormatFailed"), {
          description: e.message,
        });
      }
    },
    [t],
  );

  const handleStartEditFormat = useCallback(() => {
    if (formatResult) {
      setEditedFormatInfo({ ...formatResult });
      setIsEditingFormat(true);
    }
  }, [formatResult]);

  const handleCancelEditFormat = useCallback(() => {
    setIsEditingFormat(false);
    setEditedFormatInfo(null);
  }, []);

  const handleSaveEditFormat = useCallback(() => {
    if (!editedFormatInfo) return;
    const parsed = { ...editedFormatInfo };
    [
      "latex_backend",
      "latex_package",
      "latex_bibliographystyle",
      "latex_preamble",
    ].forEach((key) => {
      if (parsed[key] === "" || parsed[key] == null) parsed[key] = null;
    });
    if (typeof parsed.required_fields === "string") {
      parsed.required_fields = parsed.required_fields
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    if (typeof parsed.optional_fields === "string") {
      parsed.optional_fields = parsed.optional_fields
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    if (typeof parsed.field_descriptions === "string") {
      try {
        parsed.field_descriptions = JSON.parse(parsed.field_descriptions);
      } catch {}
    }
    setFormatResult(parsed);
    setIsEditingFormat(false);
    setEditedFormatInfo(null);
  }, [editedFormatInfo]);

  const handleSaveFormatAs = useCallback(async () => {
    if (!formatResult) return;
    const rawName = (formatResult.format_name || "unnamed").trim();
    const safeName =
      rawName
        .replace(/[<>:"/\\|?*\x00-\x1f]/g, "-")
        .replace(/-+/g, "-")
        .replace(/^-|-$/g, "") || "unnamed";
    const payload = { ...formatResult };
    [
      "latex_backend",
      "latex_package",
      "latex_bibliographystyle",
      "latex_preamble",
    ].forEach((key) => {
      if (payload[key] === "" || payload[key] == null) payload[key] = null;
    });
    setSavingFormat(true);
    try {
      const result = await api.bibManager.formats.save(safeName, payload);
      toast.success(
        t("pages:bibManager.saveFormatSuccess", { name: result.name }),
      );
      const listData = await api.bibManager.formats.list();
      setSavedFormats(listData.formats || []);
    } catch (e) {
      toast.error(t("pages:bibManager.saveFormatFailed"), {
        description: e.message,
      });
    } finally {
      setSavingFormat(false);
    }
  }, [formatResult, t]);

  const handleDeleteFormat = useCallback(
    async (name) => {
      try {
        await api.bibManager.formats.delete(name);
        toast.success(t("pages:bibManager.deleteFormatSuccess", { name }));
        if (selectedFormatName === name) setSelectedFormatName("");
        const listData = await api.bibManager.formats.list();
        setSavedFormats(listData.formats || []);
      } catch (e) {
        toast.error(t("pages:bibManager.deleteFormatFailed"), {
          description: e.message,
        });
      }
    },
    [selectedFormatName, t],
  );

  return {
    example,
    setExample,
    formatResult,
    setFormatResult,
    step1Loading,
    savedFormats,
    selectedFormatName,
    setSelectedFormatName,
    isEditingFormat,
    editedFormatInfo,
    setEditedFormatInfo,
    savingFormat,
    handleAnalyzeFormat,
    handleLoadSavedFormat,
    handleStartEditFormat,
    handleCancelEditFormat,
    handleSaveEditFormat,
    handleSaveFormatAs,
    handleDeleteFormat,
  };
}
