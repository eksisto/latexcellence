import { useState, useCallback, useEffect } from "react";
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
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import FolderPlus from "lucide-react/dist/esm/icons/folder-plus.mjs";
import {
  EDITABLE_SUFFIXES,
  joinProjectPath,
  validateNewDirName,
  validateNewFileName,
} from "@/lib/editor/files";

/**
 * 新建文件 / 文件夹对话框（文件树新建入口，editor-file-move-design §5.3）。
 *
 * 单段名称输入 + 实时校验（mode = "file" 走白名单后缀校验
 * validateNewFileName，与后端 create-file 同源；mode = "dir" 走
 * validateNewDirName，与后端 create-dir 名字消毒同口径）；目标目录由
 * 调用方经 dirPath 指定（树节点决定，根目录传 null），提交时拼完整
 * 相对路径回调 onCreate。
 *
 * @param {boolean} open 受控开关
 * @param {(v: boolean) => void} onOpenChange 开关回调
 * @param {string|null} dirPath 目标目录相对路径（null = 项目根）
 * @param {"file"|"dir"} [mode] 新建类型（默认文件）
 * @param {(relativePath: string) => Promise<boolean>} onCreate
 *   创建回调（返回 true 表示成功，由本组件关闭弹窗）
 */
export default function CreateFileDialog({
  open,
  onOpenChange,
  dirPath = null,
  mode = "file",
  onCreate,
}) {
  const { t } = useTranslation("components");
  const isDirMode = mode === "dir";
  const validateName = isDirMode ? validateNewDirName : validateNewFileName;
  const [name, setName] = useState("");
  const [errorCode, setErrorCode] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // 打开时重置输入（弹窗复用同一实例）
  useEffect(() => {
    if (open) {
      setName("");
      setErrorCode(null);
      setSubmitting(false);
    }
  }, [open]);

  const handleChange = useCallback(
    (value) => {
      setName(value);
      // 有错误提示时实时重校验，输入合法即刻消错
      setErrorCode((prev) => (prev ? validateName(value) : prev));
    },
    [validateName],
  );

  const handleSubmit = useCallback(async () => {
    const error = validateName(name);
    if (error) {
      setErrorCode(error);
      return;
    }
    if (!onCreate) return;
    setSubmitting(true);
    try {
      const ok = await onCreate(joinProjectPath(dirPath, name.trim()));
      if (ok) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  }, [name, dirPath, onCreate, onOpenChange, validateName]);

  const suffixHint = EDITABLE_SUFFIXES.join("  ");
  const targetPreview = joinProjectPath(dirPath, name.trim() || "…");
  const Icon = isDirMode ? FolderPlus : FileText;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isDirMode
              ? t("projectExplorer.createFolderTitle")
              : t("projectExplorer.createTitle")}
          </DialogTitle>
          <DialogDescription>
            {dirPath
              ? isDirMode
                ? t("projectExplorer.createFolderInDir", { dir: dirPath })
                : t("projectExplorer.createInDir", { dir: dirPath })
              : isDirMode
                ? t("projectExplorer.createFolderInRoot")
                : t("projectExplorer.createInRoot")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Input
            autoFocus
            value={name}
            onChange={(e) => handleChange(e.target.value)}
            placeholder={
              isDirMode
                ? t("projectExplorer.dirNamePlaceholder")
                : t("projectExplorer.namePlaceholder")
            }
            aria-invalid={Boolean(errorCode)}
            disabled={submitting}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
          {errorCode ? (
            <p className="text-sm text-destructive">
              {isDirMode
                ? t(`projectExplorer.dirNameError.${errorCode}`)
                : t(`projectExplorer.fileNameError.${errorCode}`)}
            </p>
          ) : isDirMode ? (
            <p className="text-xs text-muted-foreground">
              {t("projectExplorer.dirNameHint")}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("projectExplorer.nameHint", { suffixes: suffixHint })}
            </p>
          )}
          <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
            <Icon className="size-4 shrink-0" />
            <span className="truncate">{targetPreview}</span>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            {t("projectExplorer.cancelAction")}
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting && <Loader2 className="animate-spin" />}
            {t("projectExplorer.createAction")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
