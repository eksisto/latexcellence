import { useState, useCallback, useRef } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api } from "@/api/index";

export default function useBibtexGeneration({ editedMetadata, formatResult }) {
  const { t } = useTranslation(["pages", "common"]);

  const [bibtexResult, setBibtexResult] = useState(null);
  const [editedBibtex, setEditedBibtex] = useState("");
  const [editedCiteKey, setEditedCiteKey] = useState("");
  const [step3Loading, setStep3Loading] = useState(false);
  const prevCiteKeyRef = useRef("");

  /**
   * cite key 受控更新：在事件处理中同步修正 BibTeX 中的 @entry{旧key}，
   * 避免用 effect 响应状态变化造成额外渲染与状态漂移。
   */
  const handleCiteKeyChange = useCallback(
    (value) => {
      const next = String(value ?? "");
      const nextKey = next.trim();
      const prevKey = prevCiteKeyRef.current;
      setEditedCiteKey(next);
      if (editedBibtex && prevKey && nextKey && nextKey !== prevKey) {
        setEditedBibtex((prev) =>
          prev.replace(
            new RegExp(
              `@(\\w+)\\{${prevKey.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")},`,
            ),
            `@$1{${nextKey},`,
          ),
        );
        prevCiteKeyRef.current = nextKey;
      }
    },
    [editedBibtex],
  );

  const handleGenerateBibtex = useCallback(async () => {
    if (!editedMetadata) return;
    setStep3Loading(true);
    setBibtexResult(null);
    setEditedBibtex("");
    setEditedCiteKey("");
    prevCiteKeyRef.current = "";

    try {
      const data = await api.bibManager.generate({
        metadata: { ...editedMetadata },
        format_info: formatResult,
        existing_keys: [],
      });
      setBibtexResult(data);
      setEditedBibtex(data.raw_bibtex);
      setEditedCiteKey(data.cite_key);
      prevCiteKeyRef.current = data.cite_key;
      toast.success(t("pages:bibManager.step3Success"));
    } catch (e) {
      toast.error(t("pages:bibManager.step3Failed"), {
        description: e.message,
      });
    } finally {
      setStep3Loading(false);
    }
  }, [editedMetadata, formatResult, t]);

  const handleCopyBibtex = useCallback(() => {
    if (!editedBibtex) return;
    navigator.clipboard.writeText(editedBibtex).then(
      () => toast.success(t("pages:bibManager.copySuccess")),
      () => toast.error(t("pages:bibManager.copyFailed")),
    );
  }, [editedBibtex, t]);

  return {
    bibtexResult,
    editedBibtex,
    setEditedBibtex,
    editedCiteKey,
    setEditedCiteKey: handleCiteKeyChange,
    step3Loading,
    handleGenerateBibtex,
    handleCopyBibtex,
  };
}
