import { useEffect, useRef, useState } from "react";
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
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";
import { formatBytes } from "@/utils/format";

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
async function zipFiles(files, onProgress) {
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();
  for (const f of files) {
    zip.file(f.name, f.file);
  }
  // onUpdate 高频触发：按整数百分比去重后再上报，避免无谓重渲染（审查 L13，
  // 此前大目录打包只有一个无限 spinner）
  let lastPct = -1;
  return zip.generateAsync({ type: "blob", compression: "DEFLATE" }, (meta) => {
    const pct = Math.round(meta.percent);
    if (onProgress && pct !== lastPct) {
      lastPct = pct;
      onProgress(pct);
    }
  });
}

/**
 * 上传项目对话框（P5）：选择 zip/7z/rar 压缩包，或选择本地目录（webkitdirectory → JSZip 打包）。
 * 走 /api/projects 上传管线，展示打包/上传进度；成功后回调 onUploaded(project)。
 */
export default function UploadProjectDialog({
  open,
  onOpenChange,
  onUploaded,
}) {
  const { t } = useTranslation(["components", "pages"]);

  // 剩余项目空间（配额与用量来自 /auth/me，工作台每次 loadAll 都会刷新）
  const projectQuota = useStore((s) => s.quota?.project_quota_bytes);
  const projectUsage = useStore((s) => s.usage?.project_usage_bytes);

  const [mode, setMode] = useState("file");
  const [file, setFile] = useState(null);
  const [dirFiles, setDirFiles] = useState([]);
  const [dirLabel, setDirLabel] = useState("");

  const [phase, setPhase] = useState("idle"); // idle | reading | packing | uploading
  const [progress, setProgress] = useState(0);
  // 后端上传上限（config/status 只读透出）：目录模式浏览器侧前置校验用
  const [uploadLimits, setUploadLimits] = useState(null);

  const fileInputRef = useRef(null);
  const dirInputRef = useRef(null);

  const reset = () => {
    setFile(null);
    setDirFiles([]);
    setDirLabel("");
    setPhase("idle");
    setProgress(0);
  };

  // 目录模式前置校验需要后端上传上限：打开时拉取一次 config/status；
  // 失败保持 null → 跳过数量/单文件校验，提交时由后端 413 兜底（错误码可翻译）
  useEffect(() => {
    if (!open || uploadLimits) return;
    let mounted = true;
    api
      .configStatus()
      .then((data) => {
        if (mounted && data?.max_upload_files) {
          setUploadLimits({
            maxFiles: data.max_upload_files,
            maxFileBytes: (data.max_upload_file_size_mb || 0) * 1024 * 1024,
          });
        }
      })
      .catch(() => {
        // 探测失败保持 null：前置校验降级，后端仍兜底
      });
    return () => {
      mounted = false;
    };
  }, [open, uploadLimits]);

  const handleClose = (openNow) => {
    // busy（读取/打包/上传）期间禁止关闭（审查 L7）：底层 XHR/打包仍在进行，
    // 关闭会 reset() 界面但完成回调照常触发，UI 与实际状态脱节。
    // ESC / 遮罩 / 右上角 X 都会经 onOpenChange(false) 走到这里。
    if (!openNow && busy) return;
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

      // 浏览器侧前置校验（审查 L13）：文件数 / 单文件大小 / 总字节超剩余
      // 项目空间时直接拦截，避免打包上传整个目录后才被后端 413/403 拒绝。
      // 校验必须先于 setDirFiles：被拒的目录不得进入 state（否则上传按钮
      // 仍可用，前置拦截被绕过）。上限未知（探测失败）时跳过对应检查，由
      // 后端兜底。
      if (uploadLimits && files.length > uploadLimits.maxFiles) {
        toast.error(
          t("components:uploadProject.dirTooManyFiles", {
            count: files.length,
            max: uploadLimits.maxFiles,
          }),
        );
        return;
      }
      if (uploadLimits?.maxFileBytes) {
        const overSizeFile = files.find(
          (f) => f.file.size > uploadLimits.maxFileBytes,
        );
        if (overSizeFile) {
          toast.error(
            t("components:uploadProject.dirFileTooLarge", {
              name: overSizeFile.name,
              max: Math.floor(uploadLimits.maxFileBytes / 1024 / 1024),
            }),
          );
          return;
        }
      }
      const totalBytes = files.reduce((sum, f) => sum + f.file.size, 0);
      const remainingBytes =
        projectQuota != null && projectUsage != null
          ? projectQuota - projectUsage
          : null;
      if (remainingBytes != null && totalBytes > remainingBytes) {
        toast.error(
          t("components:uploadProject.dirQuotaExceeded", {
            size: formatBytes(totalBytes),
            quota: formatBytes(Math.max(remainingBytes, 0)),
          }),
        );
        return;
      }

      setDirFiles(files);
      const label =
        files[0]?.name?.split("/")[0] ||
        list[0]?.webkitRelativePath?.split("/")[0] ||
        "folder";
      setDirLabel(label);
    } catch {
      // 客户端自产错误不走 translateError（其映射表只收录后端错误），
      // 直接用 i18n key 展示当前语言文案（审查 M7）
      toast.error(t("components:uploadProject.dirReadFailed"), {
        description: t("components:uploadProject.dirReadFailedHint"),
      });
    } finally {
      setPhase("idle");
      if (dirInputRef.current) dirInputRef.current.value = "";
    }
  };

  const handleUpload = async () => {
    let archive;
    let archiveName;
    if (mode === "directory") {
      if (dirFiles.length === 0) return;
      setPhase("packing");
      setProgress(0);
      try {
        // 打包进度经 onUpdate 上报，复用进度条展示（审查 L13）
        archive = await zipFiles(dirFiles, setProgress);
        archiveName = `${dirLabel || "project"}.zip`;
      } catch {
        // 同上：客户端打包错误用 i18n key，不走 translateError
        toast.error(t("components:uploadProject.dirPackFailed"));
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
              accept=".zip,.7z,.rar"
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
                ? `${file.name} · ${formatBytes(file.size)}`
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

        {/* 打包与上传共用进度条：打包进度来自 JSZip onUpdate（审查 L13） */}
        {(phase === "packing" || phase === "uploading") && (
          <div className="space-y-1">
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {phase === "packing"
                ? t("components:uploadProject.packing")
                : t("components:uploadProject.uploading")}{" "}
              {progress}%
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
