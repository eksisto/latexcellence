import { useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api } from "@/api/index";

export default function useBibFileSave({
  projectPath,
  workspaceId,
  editedBibtex,
  editedCiteKey,
}) {
  const { t } = useTranslation(["pages", "common"]);

  const [bibFiles, setBibFiles] = useState([]);
  const [selectedBibFile, setSelectedBibFile] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!projectPath) return;
    api.bibManager
      .listBibFiles(workspaceId)
      .then((data) => {
        setBibFiles(data.bib_files || []);
        if (data.bib_files?.length > 0) {
          setSelectedBibFile(data.bib_files[0]);
        }
      })
      .catch(() => {});
  }, [projectPath, workspaceId]);

  const handleSaveBib = useCallback(async () => {
    if (!editedBibtex.trim() || !selectedBibFile) return;
    setSaving(true);
    try {
      const data = await api.bibManager.save(
        {
          file_path: selectedBibFile,
          bibtex_code: editedBibtex.trim(),
          cite_key: editedCiteKey,
        },
        workspaceId,
      );
      const actionKey =
        data.action === "updated" ? "step4Updated" : "step4Appended";
      toast.success(
        t("pages:bibManager.step4SaveSuccess", {
          action: t(`pages:bibManager.${actionKey}`),
          file: selectedBibFile,
        }),
      );
    } catch (e) {
      toast.error(t("pages:bibManager.step4SaveFailed"), {
        description: e.message,
      });
    } finally {
      setSaving(false);
    }
  }, [editedBibtex, editedCiteKey, selectedBibFile, workspaceId, t]);

  return {
    bibFiles,
    selectedBibFile,
    setSelectedBibFile,
    saving,
    handleSaveBib,
  };
}
