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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import LibraryBig from "lucide-react/dist/esm/icons/library-big.mjs";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import { TEMPLATE_CATEGORIES, categoryLabelKey } from "@/utils/templateCenter";

/**
 * template_gen 生成结果保存到我的模板库对话框：模板名（契约必填，预填
 * 默认名）+ 可选分类；会话 id 由调用方传入。提交走
 * POST /api/templates/from-template-gen，后端剔除 PDF/trace/编译辅助
 * 文件后复制入库。
 *
 * @param {boolean} open
 * @param {(v: boolean) => void} onOpenChange
 * @param {string|null} props.sessionId template_gen 会话 id（null 表示关闭）
 * @param {(template: object) => void} props.onSaved 保存成功回调（携带模板摘要）
 */
export default function SaveTemplateGenDialog({
  open,
  onOpenChange,
  sessionId,
  onSaved,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // 每次打开重置为默认名（模板名契约必填，默认值保证确认即可入库）
  useEffect(() => {
    if (open) {
      setName(t("pages:templateGen.saveToLibrary.defaultName"));
      setCategory("");
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleSubmit = async () => {
    if (!sessionId || !name.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const data = await api.templateLibrary.fromTemplateGen(
        sessionId,
        name.trim(),
        category || null,
      );
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
          <DialogTitle>
            {t("pages:templateGen.saveToLibrary.title")}
          </DialogTitle>
          <DialogDescription>
            {t("pages:templateGen.saveToLibrary.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="template-gen-save-name">
              {t("pages:templateGen.saveToLibrary.nameLabel")}
            </Label>
            <Input
              id="template-gen-save-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("pages:templateGen.saveToLibrary.namePlaceholder")}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("pages:templateGen.saveToLibrary.categoryLabel")}</Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={t(
                    "pages:templateGen.saveToLibrary.categoryAuto",
                  )}
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
          <Button onClick={handleSubmit} disabled={!name.trim() || submitting}>
            {submitting ? (
              <>
                <Loader2 className="animate-spin" />
                {t("pages:templateGen.saveToLibrary.submitting")}
              </>
            ) : (
              <>
                <LibraryBig />
                {t("pages:templateGen.saveToLibrary.submit")}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
