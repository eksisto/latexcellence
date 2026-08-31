import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import { translateError } from "@/utils/errorMessages";
import FolderPickerDialog from "@/components/FolderPickerDialog";

/**
 * 从模板创建项目对话框：输入项目名；单用户模式追加 FolderPicker 选择
 * 目标目录（后端创建 <目标目录>/<项目名>/）。
 * 成功后回调 onCreated(project)——多用户 {project_id, name}、
 * 单用户 {name, path}；打开项目与跳转由页面层处理。
 *
 * @param {object|null} props.template 目标模板（null 表示关闭）
 * @param {(v: boolean) => void} props.onOpenChange
 * @param {(project: object) => void} props.onCreated
 */
export default function CreateProjectDialog({
  template,
  onOpenChange,
  onCreated,
}) {
  const open = !!template;
  const { t } = useTranslation(["pages", "common"]);
  const multiUser = isMultiUser();
  const [name, setName] = useState("");
  const [targetDir, setTargetDir] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (template) {
      setName("");
      setTargetDir("");
      setError(null);
    }
  }, [template]);

  const canSubmit =
    !!template &&
    !!name.trim() &&
    (multiUser || !!targetDir.trim()) &&
    !submitting;

  const handleSubmit = async () => {
    if (!template || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const data = await api.templateLibrary.createProject(
        template.id,
        name.trim(),
        multiUser ? null : targetDir.trim(),
      );
      onOpenChange(false);
      onCreated?.(data.project);
    } catch (e) {
      setError(translateError({ error: e.code, message: e.message }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>
              {t("pages:templateCenter.create.title", {
                name: template?.name || "",
              })}
            </DialogTitle>
            <DialogDescription>
              {t("pages:templateCenter.create.description")}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="template-create-name">
                {t("pages:templateCenter.create.nameLabel")}
              </Label>
              <Input
                id="template-create-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("pages:templateCenter.create.namePlaceholder")}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              />
            </div>
            {!multiUser && (
              <div className="space-y-1.5">
                <Label>{t("pages:templateCenter.create.targetDirLabel")}</Label>
                <div className="flex gap-2">
                  <Input
                    className="flex-1"
                    value={targetDir}
                    onChange={(e) => setTargetDir(e.target.value)}
                    placeholder={t(
                      "pages:templateCenter.create.targetDirPlaceholder",
                    )}
                  />
                  <Button
                    variant="outline"
                    onClick={() => setPickerOpen(true)}
                    type="button"
                  >
                    {t("pages:templateCenter.create.browse")}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("pages:templateCenter.create.targetDirHint")}
                </p>
              </div>
            )}
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              {t("common:cancel")}
            </Button>
            <Button onClick={handleSubmit} disabled={!canSubmit}>
              {submitting ? (
                <>
                  <Loader2 className="animate-spin" />
                  {t("pages:templateCenter.create.creating")}
                </>
              ) : (
                <>
                  <FolderOpen />
                  {t("pages:templateCenter.create.submit")}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {!multiUser && (
        <FolderPickerDialog
          open={pickerOpen}
          onOpenChange={setPickerOpen}
          onSelect={setTargetDir}
          initialPath={targetDir}
        />
      )}
    </>
  );
}
