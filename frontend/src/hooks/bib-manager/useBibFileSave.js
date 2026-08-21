import { useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { isMultiUser } from "@/api/connection";
import { isProjectOpened } from "@/utils/projectAccess";

export default function useBibFileSave({
  projectPath,
  workspaceId,
  editedBibtex,
  editedCiteKey,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const projectId = useStore((s) => s.projectId);

  const [bibFiles, setBibFiles] = useState([]);
  const [selectedBibFile, setSelectedBibFile] = useState("");
  const [saving, setSaving] = useState(false);

  // 模式感知"项目已打开"（多用户下 projectPath 恒为 null，按 projectId 判定）
  const isOpened = isProjectOpened({
    multiUser: isMultiUser(),
    projectPath,
    projectId,
  });

  useEffect(() => {
    if (!isOpened) return;
    api.bibManager
      .listBibFiles(workspaceId)
      .then((data) => {
        setBibFiles(data.bib_files || []);
        if (data.bib_files?.length > 0) {
          setSelectedBibFile(data.bib_files[0]);
        }
      })
      .catch(() => {});
  }, [isOpened, workspaceId]);

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
