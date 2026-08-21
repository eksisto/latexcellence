import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import FolderUp from "lucide-react/dist/esm/icons/folder-up.mjs";
import FileArchive from "lucide-react/dist/esm/icons/file-archive.mjs";
import UploadCloud from "lucide-react/dist/esm/icons/upload-cloud.mjs";
import Loader from "lucide-react/dist/esm/icons/loader-circle.mjs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";

/**
 * 目录模式：收集 webkitdirectory 文件列表，zip 内路径用 webkitRelativePath
 * 保留子目录结构（避免拍平导致 \input 引用断链、同名文件互相覆盖）。
 */
function collectDirectoryFiles(fileList) {
  const files = [];
  for (const item of fileList) {
    const rel = item.webkitRelativePath || item.name;
    files.push({ name: rel, file: item });
  }
  return files;
}

/**
 * 目录模式：客户端打包为 zip Blob（前端 JSZip，走同一上传管线）。
 * JSZip 体积较大且仅目录直传用到，延迟到此处动态导入：
 * 配合 vite manualChunks 的 vendor-jszip 规则拆成独立 chunk，仅在用户
 * 选择目录打包时加载，避免进入首屏 vendor。
 */
async function zipFiles(files) {
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();
  for (const f of files) {
    zip.file(f.name, f.file);
  }
  return zip.generateAsync({ type: "blob", compression: "DEFLATE" });
}

function humanSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * 上传项目对话框（P5）：选择 zip/7z 压缩包，或选择本地目录（webkitdirectory → JSZip 打包）。
 * 走 /api/projects 上传管线，展示打包/上传进度；成功后回调 onUploaded(project)。
 */
export default function UploadProjectDialog({
  open,
  onOpenChange,
  onUploaded,
}) {
  const { t } = useTranslation(["components", "pages"]);

  const [mode, setMode] = useState("file");
  const [file, setFile] = useState(null);
  const [dirFiles, setDirFiles] = useState([]);
  const [dirLabel, setDirLabel] = useState("");

  const [phase, setPhase] = useState("idle"); // idle | reading | packing | uploading
  const [progress, setProgress] = useState(0);

  const fileInputRef = useRef(null);
  const dirInputRef = useRef(null);

  const reset = () => {
    setFile(null);
    setDirFiles([]);
    setDirLabel("");
    setPhase("idle");
    setProgress(0);
  };

  const handleClose = (openNow) => {
    if (!openNow) reset();
    onOpenChange(openNow);
  };

  const handleFileChange = (e) => {
    const f = e.target.files?.[0] || null;
    setFile(f);
    if (f) fileInputRef.current?.value && (fileInputRef.current.value = "");
  };

  const handleDirChange = async (e) => {
    const list = e.target.files;
    if (!list || list.length === 0) return;
    try {
      setPhase("reading");
      const files = collectDirectoryFiles(list);
      setDirFiles(files);
      const label =
        files[0]?.name?.split("/")[0] ||
        list[0]?.webkitRelativePath?.split("/")[0] ||
        "folder";
      setDirLabel(label);
    } catch (err) {
      toast.error(translateError(err.message || "Failed to read directory"), {
        description: translateError(
          "Failed to read the selected directory; try archiving it as a zip instead",
        ),
      });
    } finally {
      setPhase("idle");
      if (dirInputRef.current) dirInputRef.current.value = "";
    }
  };

  const nameFromFile = (f) => {
    const base = f.name.replace(/\.(zip|7z)$/i, "");
    return base.replace(/[\\/]+/g, "_").trim() || undefined;
  };

  const handleUpload = async () => {
    let archive;
    let archiveName;
    if (mode === "directory") {
      if (dirFiles.length === 0) return;
      setPhase("packing");
      try {
        archive = await zipFiles(dirFiles);
        archiveName = `${dirLabel || "project"}.zip`;
      } catch (err) {
        toast.error(translateError(err.message || "Failed to pack directory"));
        setPhase("idle");
        return;
      }
    } else {
      if (!file) return;
      archive = file;
      archiveName = file.name;
    }

    setPhase("uploading");
    setProgress(0);
    try {
      const data = await api.projects.upload(
        archive,
        // 目录打包产物是匿名 Blob，显式传名（后端 sanitize 后作为项目名）
        mode === "directory" ? dirLabel || undefined : undefined,
        (p) => setProgress(p),
      );
      toast.success(t("components:uploadProject.uploaded"), {
        description: data?.project?.name || archiveName,
      });
      reset();
      onUploaded?.(data?.project || null);
      onOpenChange(false);
    } catch (err) {
      toast.error(t("components:uploadProject.uploadFailed"), {
        description: translateError(err.message),
      });
    } finally {
      setPhase("idle");
    }
  };

  const busy =
    phase === "reading" || phase === "packing" || phase === "uploading";
  const canUpload = mode === "file" ? !!file : dirFiles.length > 0;

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("components:uploadProject.title")}</DialogTitle>
          <DialogDescription>
            {t("components:uploadProject.desc")}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={mode}
          onValueChange={(v) => {
            if (busy) return;
            setMode(v);
          }}
        >
          <TabsList className="w-full">
            <TabsTrigger value="file">
              <FileArchive /> {t("components:uploadProject.fileTab")}
            </TabsTrigger>
            <TabsTrigger value="directory">
              <FolderUp /> {t("components:uploadProject.dirTab")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="file" className="py-3">
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,.7z"
              className="hidden"
              onChange={handleFileChange}
            />
            <Button
              variant="outline"
              className="w-full"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
            >
              <FileArchive />
              {file
                ? `${file.name} · ${humanSize(file.size)}`
                : t("components:uploadProject.chooseArchive")}
            </Button>
          </TabsContent>

          <TabsContent value="directory" className="py-3">
            <input
              ref={dirInputRef}
              type="file"
              // webkitdirectory 只对目录选择生效，accept 不限制扩展名
              multiple
              className="hidden"
              {...{ webkitdirectory: "", directory: "" }}
              onChange={handleDirChange}
            />
            <Button
              variant="outline"
              className="w-full"
              onClick={() => dirInputRef.current?.click()}
              disabled={busy}
            >
              {phase === "reading" ? (
                <>
                  <Loader className="animate-spin" />
                  {t("components:uploadProject.reading")}
                </>
              ) : (
                <>
                  <FolderUp />
                  {dirLabel
                    ? `${dirLabel} · ${dirFiles.length} ${t("components:uploadProject.files")}`
                    : t("components:uploadProject.chooseDir")}
                </>
              )}
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              {t("components:uploadProject.dirHint")}
            </p>
          </TabsContent>
        </Tabs>

        {phase === "packing" && (
          <p className="text-sm text-muted-foreground">
            <Loader className="mr-1 inline size-4 animate-spin" />
            {t("components:uploadProject.packing")}
          </p>
        )}
        {phase === "uploading" && (
          <div className="space-y-1">
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {t("components:uploadProject.uploading")} {progress}%
            </p>
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleClose(false)}
            disabled={busy}
          >
            {t("components:uploadProject.cancel")}
          </Button>
          <Button onClick={handleUpload} disabled={busy || !canUpload}>
            <UploadCloud />
            {busy
              ? t("components:uploadProject.busy")
              : t("components:uploadProject.upload")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
