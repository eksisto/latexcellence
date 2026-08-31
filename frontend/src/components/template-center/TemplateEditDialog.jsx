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
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import {
  TEMPLATE_CATEGORIES,
  categoryLabelKey,
  normalizeCategory,
} from "@/utils/templateCenter";

/**
 * 编辑模板元数据对话框（仅「我的模板」）：改名 / 改分类 / 改描述。
 * 提交走 PUT /api/templates/{id}（内置模板后端拒绝 builtin_readonly）。
 *
 * @param {object|null} props.template 待编辑模板（null 表示关闭）
 * @param {(v: boolean) => void} props.onOpenChange
 * @param {(template: object) => void} props.onSaved 保存成功回调（携带新摘要）
 */
export default function TemplateEditDialog({
  template,
  onOpenChange,
  onSaved,
}) {
  const open = !!template;
  const { t } = useTranslation(["pages", "common"]);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("other");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (template) {
      setName(template.name || "");
      setCategory(normalizeCategory(template.category));
      setDescription(template.description || "");
      setError(null);
    }
  }, [template]);

  const handleSubmit = async () => {
    if (!template || submitting) return;
    if (!name.trim()) {
      setError(t("pages:templateCenter.edit.nameRequired"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const data = await api.templateLibrary.update(template.id, {
        name: name.trim(),
        category,
        description,
      });
      onOpenChange(false);
      onSaved?.(data.template);
    } catch (e) {
      setError(translateError({ error: e.code, message: e.message }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{t("pages:templateCenter.edit.title")}</DialogTitle>
          <DialogDescription>
            {t("pages:templateCenter.edit.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="template-edit-name">
              {t("pages:templateCenter.edit.nameLabel")}
            </Label>
            <Input
              id="template-edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("pages:templateCenter.edit.categoryLabel")}</Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TEMPLATE_CATEGORIES.map((c) => (
                  <SelectItem key={c} value={c}>
                    {t(`pages:${categoryLabelKey(c)}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="template-edit-description">
              {t("pages:templateCenter.edit.descriptionLabel")}
            </Label>
            <Textarea
              id="template-edit-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t(
                "pages:templateCenter.edit.descriptionPlaceholder",
              )}
            />
          </div>
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
          <Button onClick={handleSubmit} disabled={submitting || !name.trim()}>
            {submitting ? (
              <>
                <Loader2 className="animate-spin" />
                {t("pages:templateCenter.edit.saving")}
              </>
            ) : (
              t("common:save")
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
