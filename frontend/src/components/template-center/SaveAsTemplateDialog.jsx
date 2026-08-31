import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
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
import BookmarkPlus from "lucide-react/dist/esm/icons/bookmark-plus.mjs";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";
import { TEMPLATE_CATEGORIES, categoryLabelKey } from "@/utils/templateCenter";

/**
 * 「另存为模板」对话框：把当前打开的项目复制入库为我的模板
 * （POST /api/templates/from-project，workspace_id 由 body 传给后端
 * require_workspace_read_json 定位打开的项目，多用户按绑定解析、
 * 单用户取项目实例根目录）。可填写名称（必填）/分类/描述（可选）。
 *
 * @param {boolean} open
 * @param {(v: boolean) => void} onOpenChange
 * @param {(template: object) => void} [onSaved] 保存成功回调（携带模板摘要）
 */
export default function SaveAsTemplateDialog({ open, onOpenChange, onSaved }) {
  const { t } = useTranslation(["components", "common"]);
  const workspaceId = useStore((s) => s.workspaceId);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setName("");
      setCategory("");
      setDescription("");
      setError(null);
    }
  }, [open]);

  const handleSubmit = async () => {
    if (submitting || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const data = await api.templateLibrary.saveCurrentAsTemplate(
        workspaceId,
        name.trim(),
        category || null,
        description.trim(),
      );
      onOpenChange(false);
      toast.success(t("components:saveAsTemplate.success"));
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
          <DialogTitle>{t("components:saveAsTemplate.title")}</DialogTitle>
          <DialogDescription>
            {t("components:saveAsTemplate.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="save-template-name">
              {t("components:saveAsTemplate.nameLabel")}
            </Label>
            <Input
              id="save-template-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("components:saveAsTemplate.namePlaceholder")}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("components:saveAsTemplate.categoryLabel")}</Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={t("components:saveAsTemplate.categoryAuto")}
                />
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
            <Label htmlFor="save-template-description">
              {t("components:saveAsTemplate.descriptionLabel")}
            </Label>
            <Textarea
              id="save-template-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t(
                "components:saveAsTemplate.descriptionPlaceholder",
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
                {t("components:saveAsTemplate.saving")}
              </>
            ) : (
              <>
                <BookmarkPlus />
                {t("components:saveAsTemplate.submit")}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
