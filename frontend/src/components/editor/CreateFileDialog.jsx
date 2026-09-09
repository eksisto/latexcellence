import { useState, useCallback, useEffect, useRef } from "react";
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
import Folder from "lucide-react/dist/esm/icons/folder.mjs";
import FolderPlus from "lucide-react/dist/esm/icons/folder-plus.mjs";
import {
  EDITABLE_SUFFIXES,
  joinProjectPath,
  validateNewDirName,
  validateNewFileName,
} from "@/lib/editor/files";

/**
 * 新建文件 / 文件夹 / 重命名对话框（文件树新建与右键菜单入口，
 * editor-file-move-design §5.3 / editor-file-context-menu-design §4.3）。
 *
 * 单段名称输入 + 实时校验（mode = "file" 走白名单后缀校验
 * validateNewFileName，与后端 create-file 同源；mode = "dir" 走
 * validateNewDirName，与后端 create-dir 名字消毒同口径；mode = "rename"
 * 亦走 validateNewDirName 级消毒但**不限** EDITABLE_SUFFIXES 白名单——
 * 图片、.bib、.sty 亦须可改名，与 move-file 后端不限后缀一致，决策 Q10d）；
 * 目标目录由调用方经 dirPath 指定（树节点决定，根目录传 null），提交时拼
 * 完整相对路径回调 onCreate。rename 模式的 dirPath 语义为「目标父目录」，
 * initialName 预填原名并全选。
 *
 * @param {boolean} open 受控开关
 * @param {(v: boolean) => void} onOpenChange 开关回调
 * @param {string|null} dirPath 目标目录相对路径（null = 项目根；rename 模式 = 目标父目录）
 * @param {"file"|"dir"|"rename"} [mode] 对话框类型（默认新建文件）
 * @param {string} [initialName] rename 模式的预填原名
 * @param {boolean} [isDir] rename 模式的目标类型（决定标题 / 图标文案）
 * @param {(relativePath: string) => Promise<boolean>} onCreate
 *   创建 / 重命名回调（返回 true 表示成功，由本组件关闭弹窗；重命名
 *   同名 no-op 时 onCreate 返回 true 静默关闭，不发请求）
 */
export default function CreateFileDialog({
  open,
  onOpenChange,
  dirPath = null,
  mode = "file",
  initialName = "",
  isDir = false,
  onCreate,
}) {
  const { t } = useTranslation("components");
  const isRenameMode = mode === "rename";
  const isDirMode = mode === "dir" || (isRenameMode && isDir);
  // file 新建走白名单后缀校验；dir 新建与 rename 同走消毒校验（不限后缀）
  const validateName =
    mode === "file" ? validateNewFileName : validateNewDirName;
  const [name, setName] = useState("");
  const [errorCode, setErrorCode] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef(null);

  // 打开时重置输入（弹窗复用同一实例）；rename 模式预填原名并全选
  //（等 autoFocus 落定后在下一帧全选，决策 Q7）
  useEffect(() => {
    if (!open) return undefined;
    setName(isRenameMode ? initialName : "");
    setErrorCode(null);
    setSubmitting(false);
    if (!isRenameMode) return undefined;
    const raf = requestAnimationFrame(() => inputRef.current?.select());
    return () => cancelAnimationFrame(raf);
  }, [open, isRenameMode, initialName]);

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
  const oldPreview = isRenameMode
    ? joinProjectPath(dirPath, initialName || "…")
    : null;
  const Icon = isDirMode ? (isRenameMode ? Folder : FolderPlus) : FileText;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isRenameMode
              ? isDir
                ? t("projectExplorer.renameDirTitle")
                : t("projectExplorer.renameFileTitle")
              : isDirMode
                ? t("projectExplorer.createFolderTitle")
                : t("projectExplorer.createTitle")}
          </DialogTitle>
          <DialogDescription>
            {isRenameMode
              ? t("projectExplorer.renameDescription", {
                  path: oldPreview ?? "",
                })
              : dirPath
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
            ref={inputRef}
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
              {isRenameMode
                ? t(`projectExplorer.renameNameError.${errorCode}`)
                : isDirMode
                  ? t(`projectExplorer.dirNameError.${errorCode}`)
                  : t(`projectExplorer.fileNameError.${errorCode}`)}
            </p>
          ) : isRenameMode ? (
            <p className="text-xs text-muted-foreground">
              {t("projectExplorer.renameNameHint")}
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
            {isRenameMode ? (
              // 目标预览行显示「旧路径 → 新路径」（§4.3）
              <span className="flex min-w-0 items-center gap-2">
                <span className="truncate line-through opacity-70">
                  {oldPreview}
                </span>
                <span className="shrink-0">→</span>
                <span className="truncate">{targetPreview}</span>
              </span>
            ) : (
              <span className="truncate">{targetPreview}</span>
            )}
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
            {isRenameMode
              ? t("projectExplorer.renameAction")
              : t("projectExplorer.createAction")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
