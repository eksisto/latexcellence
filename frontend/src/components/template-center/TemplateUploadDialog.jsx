import { useRef, useState } from "react";
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
import FileArchive from "lucide-react/dist/esm/icons/file-archive.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import { TEMPLATE_CATEGORIES, categoryLabelKey } from "@/utils/templateCenter";

/**
 * 上传模板对话框：选择 zip 压缩包 + 可选名称/分类（缺省由后端自动生成：
 * zip 文件名为名、other 分类、自动探测主文件）。
 *
 * @param {boolean} open
 * @param {(v: boolean) => void} onOpenChange
 * @param {(template: object) => void} onUploaded 上传成功回调（携带模板摘要）
 */
export default function TemplateUploadDialog({
  open,
  onOpenChange,
  onUploaded,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const [file, setFile] = useState(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const fileInputRef = useRef(null);

  const reset = () => {
    setFile(null);
    setName("");
    setCategory("");
    setError(null);
  };

  const handleClose = (next) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const handleSubmit = async () => {
    if (!file || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const data = await api.templateLibrary.upload(
        file,
        name.trim() || null,
        category || null,
      );
      reset();
      onOpenChange(false);
      onUploaded?.(data.template);
    } catch (e) {
      setError(translateError({ error: e.code, message: e.message }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{t("pages:templateCenter.upload.title")}</DialogTitle>
          <DialogDescription>
            {t("pages:templateCenter.upload.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="template-upload-file">
              {t("pages:templateCenter.upload.fileLabel")}
            </Label>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-left text-sm hover:bg-accent/50"
            >
              <FileArchive className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate">
                {file
                  ? file.name
                  : t("pages:templateCenter.upload.filePlaceholder")}
              </span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="template-upload-name">
              {t("pages:templateCenter.upload.nameLabel")}
            </Label>
            <Input
              id="template-upload-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("pages:templateCenter.upload.namePlaceholder")}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{t("pages:templateCenter.upload.categoryLabel")}</Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={t("pages:templateCenter.upload.categoryAuto")}
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
            <p className="text-xs text-muted-foreground">
              {t("pages:templateCenter.upload.optionalHint")}
            </p>
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleClose(false)}
            disabled={submitting}
          >
            {t("common:cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={!file || submitting}>
            {submitting ? (
              <>
                <Loader2 className="animate-spin" />
                {t("pages:templateCenter.upload.submitting")}
              </>
            ) : (
              t("pages:templateCenter.upload.submit")
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
