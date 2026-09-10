import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import FolderUp from "lucide-react/dist/esm/icons/folder-up.mjs";
import FileArchive from "lucide-react/dist/esm/icons/file-archive.mjs";
import UploadCloud from "lucide-react/dist/esm/icons/upload-cloud.mjs";
import Loader from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Pause from "lucide-react/dist/esm/icons/pause.mjs";
import Play from "lucide-react/dist/esm/icons/play.mjs";
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
import { fetchConfigStatus } from "@/hooks/useConfigStatus";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";
import { formatBytes } from "@/utils/format";
import { Md5, md5HexFromBlob } from "@/utils/md5";
import {
  createUploadQueue,
  isPausedError,
  planTasksFromStatus,
  resetForResume,
  runUploadQueue,
} from "@/utils/uploadQueue";

/**
 * 分片上传（v2 P5）：本地持久化的待续传会话信息。
 * 浏览器无法在刷新后保留 File 句柄，「继续上传」需要用户重新选择相同内容；
 * 这里只保存会话 id 与轻量清单（path+size），用于匹配与进度对位。
 */
const RESUME_STORAGE_KEY = "latexcellence-upload-session-v1";

// 载荷结构版本（client-localstorage-schema，对齐 useStore persist 的
// version+migrate 惯例）：结构变更时递增，读取侧校验失败即视为无待续传会话
const RESUME_PAYLOAD_VERSION = 1;

// 全量 MD5 计算的分片读取粒度
const HASH_READ_CHUNK = 1 << 20;

// 进度条可见的运行阶段（idle 无进度可展示）
const ACTIVE_PHASES = new Set([
  "preparing",
  "uploading",
  "completing",
  "paused",
  "pausing",
]);

/**
 * 目录模式：收集 webkitdirectory 文件列表，相对路径用 webkitRelativePath
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

/** 文件模式 kind 判定（与服务端扩展名白名单一致） */
function archiveKindOf(filename) {
  const low = (filename || "").toLowerCase();
  if (low.endsWith(".zip")) return "zip";
  if (low.endsWith(".7z")) return "7z";
  if (low.endsWith(".rar")) return "rar";
  return null;
}

/** 顺序读取 File 计算全量 MD5（增量流式，避免整文件载入内存） */
async function hashFile(file, onProgress) {
  const hasher = new Md5();
  let offset = 0;
  while (offset < file.size) {
    const end = Math.min(offset + HASH_READ_CHUNK, file.size);
    const buf = await file.slice(offset, end).arrayBuffer();
    hasher.update(new Uint8Array(buf));
    offset = end;
    if (onProgress) onProgress(end);
  }
  if (file.size === 0) {
    // 空文件也要产出空串 MD5（与后端 hashlib.md5(b"") 一致）
    hasher.update(new Uint8Array(0));
  }
  return hasher.hex();
}

/**
 * 上传项目对话框（v2 P5 分片版）：zip/7z/rar 压缩包直传或目录直传，
 * 全部走五端点分片协议（并发 3、指数退避、暂停/续传、断点续传）。
 * 成功后回调 onUploaded(project)。
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
  const [selection, setSelection] = useState(null); // {kind,name,files:[{name,file}],totalBytes}
  const [dirLabel, setDirLabel] = useState("");

  // idle | preparing | uploading | paused | completing
  const [phase, setPhase] = useState("idle");
  const [progress, setProgress] = useState(0);
  // 待续传会话提示（localStorage 恢复；重新选择相同内容即可继续）
  const [pendingResume, setPendingResume] = useState(null);

  const fileInputRef = useRef(null);
  const dirInputRef = useRef(null);

  // 分片队列运行时状态（ref 承载，避免闭包陈旧值；不参与渲染）
  const queueRef = useRef(null);

  // 后端上传上限（config/status 只读透出）：前置校验 + 分片大小对齐
  const [uploadLimits, setUploadLimits] = useState(null);

  const loadPendingResume = useCallback(() => {
    try {
      const raw = window.localStorage.getItem(RESUME_STORAGE_KEY);
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (
        saved?.v === RESUME_PAYLOAD_VERSION &&
        saved?.sessionId &&
        Array.isArray(saved.files)
      ) {
        return saved;
      }
    } catch {
      // 存储损坏视为无待续传会话
    }
    return null;
  }, []);

  useEffect(() => {
    if (!open) return;
    setPendingResume(loadPendingResume());
  }, [open, loadPendingResume]);

  // 目录模式前置校验需要后端上传上限与分片大小：打开时拉取一次 config/status
  // （走共享探测层，命中宿主页已拉取的缓存则不再重复请求）；
  // 失败保持 null → 跳过数量/单文件校验，提交时由后端 413 兜底（错误码可翻译）
  useEffect(() => {
    if (!open || uploadLimits) return;
    let mounted = true;
    fetchConfigStatus()
      .then((data) => {
        if (mounted && data?.max_upload_files) {
          setUploadLimits({
            maxFiles: data.max_upload_files,
            maxFileBytes: (data.max_upload_file_size_mb || 0) * 1024 * 1024,
            chunkPartBytes: Math.max(
              1,
              (data.chunk_part_max_mb || 8) * 1024 * 1024,
            ),
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

  const chunkPartBytes = uploadLimits?.chunkPartBytes ?? 8 * 1024 * 1024;

  const clearResumeStorage = () => {
    window.localStorage.removeItem(RESUME_STORAGE_KEY);
    setPendingResume(null);
  };

  /** 彻底复位界面与运行时队列（不清 localStorage，除非显式调用） */
  const reset = () => {
    queueRef.current = null;
    setSelection(null);
    setDirLabel("");
    setPhase("idle");
    setProgress(0);
  };

  /** 中止服务端会话 + 清理本地存储 + 复位（用户主动放弃时调用） */
  const abortAndReset = async () => {
    const sid = queueRef.current?.sessionId;
    queueRef.current = null;
    if (sid) {
      try {
        await api.projects.uploadSessions.abort(sid);
      } catch {
        // 会话可能已过期/被维护清理：忽略，本地照常清理
      }
    }
    clearResumeStorage();
    reset();
  };

  const handleClose = (openNow) => {
    // 上传/准备/落库进行中禁止关闭（底层请求仍在进行）；暂停态允许关闭——
    // 会话已持久化，下次打开可经「待续传」横幅继续。
    if (
      !openNow &&
      (phase === "preparing" || phase === "uploading" || phase === "completing")
    ) {
      return;
    }
    if (!openNow) reset();
    onOpenChange(openNow);
  };

  // ── 选择与前置校验 ────────────────────────────────────────────
  const validateSelection = (entries, kind) => {
    // 数量 / 单文件大小 / 总字节超剩余项目空间的前置拦截（后端仍兜底）
    if (uploadLimits && entries.length > uploadLimits.maxFiles) {
      toast.error(
        t("components:uploadProject.dirTooManyFiles", {
          count: entries.length,
          max: uploadLimits.maxFiles,
        }),
      );
      return false;
    }
    if (uploadLimits?.maxFileBytes) {
      const over = entries.find((f) => f.file.size > uploadLimits.maxFileBytes);
      if (over) {
        toast.error(
          t("components:uploadProject.dirFileTooLarge", {
            name: over.name,
            max: Math.floor(uploadLimits.maxFileBytes / 1024 / 1024),
          }),
        );
        return false;
      }
    }
    const totalBytes = entries.reduce((sum, f) => sum + f.file.size, 0);
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
      return false;
    }
    if (kind !== "directory" && !archiveKindOf(entries[0]?.file?.name)) {
      toast.error(translateError("invalid_archive"));
      return false;
    }
    return true;
  };

  const handleFileChange = (e) => {
    const f = e.target.files?.[0] || null;
    if (f) {
      if (!validateSelection([{ name: f.name, file: f }], "file")) {
        e.target.value = "";
        return;
      }
      setSelection({
        kind: archiveKindOf(f.name),
        name: f.name,
        files: [{ name: f.name, file: f }],
        totalBytes: f.size,
      });
    } else {
      setSelection(null);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleDirChange = (e) => {
    const list = e.target.files;
    if (!list || list.length === 0) return;
    try {
      const files = collectDirectoryFiles(list);
      if (!validateSelection(files, "directory")) {
        e.target.value = "";
        return;
      }
      const totalBytes = files.reduce((sum, f) => sum + f.file.size, 0);
      const label =
        files[0]?.name?.split("/")[0] ||
        list[0]?.webkitRelativePath?.split("/")[0] ||
        "folder";
      setSelection({ kind: "directory", name: label, files, totalBytes });
      setDirLabel(label);
    } catch {
      // 客户端自产错误不走 translateError（其映射表只收录后端错误），
      // 直接用 i18n key 展示当前语言文案
      toast.error(t("components:uploadProject.dirReadFailed"), {
        description: t("components:uploadProject.dirReadFailedHint"),
      });
    } finally {
      if (dirInputRef.current) dirInputRef.current.value = "";
    }
  };

  // ── 分片队列 ────────────────────────────────────────────────
  const updateProgress = (uploaded, total) => {
    const pct =
      total > 0 ? Math.min(100, Math.round((uploaded / total) * 100)) : 100;
    setProgress(pct);
  };

  /** 服务端权威进度对位：按位图重建已收字节基线与缺失任务列表 */
  const alignWithServer = async (q) => {
    const st = await api.projects.uploadSessions.status(q.sessionId);
    const { receivedBytes, tasks } = planTasksFromStatus(st, q.chunkPartBytes);
    q.uploadedBytes = receivedBytes;
    return tasks;
  };

  /** 单个分片的真实网络动作：切片 → 计算 MD5 → PUT（注入 uploadPartWithRetry） */
  const putPart = async (q, task, signal) => {
    const entry = q.files[task.fi];
    const blob = entry.file.slice(task.offset, task.offset + task.bytes);
    const md5hex = await md5HexFromBlob(blob);
    await api.projects.uploadSessions.putPart(
      q.sessionId,
      task.fi,
      task.ci,
      blob,
      md5hex,
      signal,
    );
  };

  const handleFatal = (err) => {
    const code = err?.code;
    if (
      code === "upload_session_expired" ||
      code === "upload_session_not_found"
    ) {
      clearResumeStorage();
      queueRef.current = null;
      setPhase("idle");
      setProgress(0);
      toast.error(t("components:uploadProject.sessionExpired"));
      return;
    }
    toast.error(t("components:uploadProject.uploadFailed"), {
      description:
        err instanceof Error && err.message !== "paused"
          ? translateError(err.message)
          : undefined,
    });
    // 会话已建立：进入暂停态，可经服务端位图续传；会话尚未建立（init/
    // status 阶段失败）则无会话可续，回 idle 允许直接重试——否则界面
    // 停在暂停态，「继续上传」是静默 no-op（togglePause 里队列对象为
    // null 直接返回），用户只能靠「中止上传」逃生。
    setPhase(queueRef.current?.sessionId ? "paused" : "idle");
  };

  const finishComplete = async (q) => {
    setPhase("completing");
    updateProgress(1, 1);
    try {
      const data = await api.projects.uploadSessions.complete(q.sessionId);
      clearResumeStorage();
      queueRef.current = null;
      toast.success(t("components:uploadProject.uploaded"), {
        description: data?.project?.name || q.name,
      });
      reset();
      onUploaded?.(data?.project || null);
      onOpenChange(false);
    } catch (err) {
      if (err?.code === "part_missing") {
        // 校验发现缺块（网络尾差等）：自动回到队列补传。注意当前 runQueue
        // 尚未退出（q.running 仍为 true），直接重入会被运行中守卫吞掉——
        // 延迟一拍等外层 finally 复位 running 后再按服务端位图补缺
        toast.info(t("components:uploadProject.resumingMissing"));
        setPhase("uploading");
        setTimeout(() => runQueue(), 0);
        return;
      }
      handleFatal(err);
    }
  };

  const runQueue = async () => {
    const q = queueRef.current;
    if (!q || q.running) return;
    // 恢复入口：复位 running/paused 的同时清空上次运行遗留的 fatalError。
    // 只复位 paused 不清 fatal 的话，worker 的 while (!paused && !fatalError)
    // 会跳过全部任务并原样重抛陈旧错误，「继续上传」退化为报错循环；
    // 服务端会话与已收分片以 status 位图为唯一权威，清标记后按位图重试安全
    resetForResume(q);
    setPhase("uploading");
    try {
      const tasks = await alignWithServer(q);
      updateProgress(q.uploadedBytes, q.totalBytes);
      await runUploadQueue(q, tasks, {
        uploadPart: putPart,
        onProgress: () => updateProgress(q.uploadedBytes, q.totalBytes),
        onComplete: () => finishComplete(q),
      });
    } catch (err) {
      if (isPausedError(err)) {
        // 暂停哨兵：不改写 fatalError，仅切换界面态等待「继续上传」
        setPhase("paused");
        return;
      }
      q.fatalError = err;
      handleFatal(err);
    } finally {
      q.running = false;
    }
  };

  /** 准备阶段：计算全量 MD5 → init 会话 → 持久化 → 启动队列 */
  const startFreshUpload = async () => {
    if (!selection) return;
    setPhase("preparing");
    const totalBytes = selection.files.reduce((sum, f) => sum + f.file.size, 0);
    try {
      const manifest = [];
      let hashed = 0;
      const fileMap = [];
      for (const f of selection.files) {
        const md5hex = await hashFile(f.file, (done) => {
          updateProgress(hashed + done, Math.max(totalBytes, 1));
        });
        hashed += f.file.size;
        const chunks =
          f.file.size === 0 ? 0 : Math.ceil(f.file.size / chunkPartBytes);
        manifest.push({
          path: f.name,
          size: f.file.size,
          md5: md5hex,
          chunks,
        });
        fileMap.push(f);
      }
      const created = await api.projects.uploadSessions.init({
        name: selection.name,
        kind: selection.kind,
        totalBytes,
        files: manifest,
      });
      queueRef.current = createUploadQueue({
        sessionId: created.session_id,
        kind: selection.kind,
        name: selection.name,
        totalBytes,
        chunkPartBytes: created.chunk_size || chunkPartBytes,
        files: fileMap.map((f, i) => ({
          file: f.file,
          size: manifest[i].size,
        })),
      });
      window.localStorage.setItem(
        RESUME_STORAGE_KEY,
        JSON.stringify({
          v: RESUME_PAYLOAD_VERSION,
          sessionId: created.session_id,
          kind: selection.kind,
          name: selection.name,
          totalBytes,
          files: manifest.map((m) => ({ path: m.path, size: m.size })),
          savedAt: Date.now(),
        }),
      );
      await runQueue();
    } catch (err) {
      handleFatal(err);
    }
  };

  /** 续传入口：先 GET status 对位图再补缺（无需重新计算 MD5/init）。
   * currentSelection 由调用方传入当次渲染的最新 selection（onClick 闭包
   * 本就持有最新值，无需 ref 镜像）。 */
  const resumeSavedSession = async (saved, currentSelection) => {
    if (!selectionMatchesSaved(saved, currentSelection)) {
      toast.error(t("components:uploadProject.resumeNeedReselect"));
      return;
    }
    setPhase("preparing");
    try {
      // 先 GET status：以服务端清单为权威，把重选的 File 按相对路径对位
      // （目录选择的文件顺序可能与首次上传不同，不能按数组下标对齐）
      const st = await api.projects.uploadSessions.status(saved.sessionId);
      const byPath = new Map(
        currentSelection.files.map((f) => [f.name, f.file]),
      );
      const files = [];
      for (const sf of st.files) {
        const file = byPath.get(sf.path);
        if (!file) {
          throw new Error(t("components:uploadProject.resumeNeedReselect"));
        }
        files.push({ file, size: sf.size });
      }
      queueRef.current = createUploadQueue({
        sessionId: saved.sessionId,
        kind: saved.kind,
        name: saved.name,
        totalBytes: saved.totalBytes,
        chunkPartBytes: st.chunk_size || chunkPartBytes,
        files,
      });
      await runQueue();
    } catch (err) {
      handleFatal(err);
    }
  };

  /** 校验重新选择的文件与存储的会话清单是否一致（path+size 匹配） */
  const selectionMatchesSaved = (saved, sel) => {
    if (!sel) return false;
    const savedMap = new Map(saved.files.map((f) => [f.path, f.size]));
    const selMap = new Map(sel.files.map((f) => [f.name, f.file.size]));
    if (savedMap.size !== selMap.size) return false;
    for (const [p, s] of selMap) {
      if (savedMap.get(p) !== s) return false;
    }
    return true;
  };

  const handleUploadClick = async () => {
    const saved = loadPendingResume();
    if (saved && selectionMatchesSaved(saved, selection)) {
      await resumeSavedSession(saved, selection);
      return;
    }
    if (saved) {
      // 所选内容与待续传会话不一致：放弃旧会话后全新上传
      try {
        await api.projects.uploadSessions.abort(saved.sessionId);
      } catch {
        // 旧会话可能已过期：忽略
      }
      toast.info(t("components:uploadProject.mismatchRestart"));
      clearResumeStorage();
    }
    await startFreshUpload();
  };

  const togglePause = async () => {
    const q = queueRef.current;
    if (!q) return;
    if (phase === "uploading") {
      q.paused = true;
      q.inflight.forEach((c) => c.abort());
      setPhase("pausing");
    } else if (phase === "paused") {
      // 仅在完全暂停后允许恢复：pausing 期间旧队列尚未 settle，
      // 此时重入会被 running 守卫吞掉，setPhase 又会被旧队列的
      // catch 打回 paused，产生自相矛盾的界面抖动
      setPhase("uploading");
      runQueue();
    }
  };

  const busy =
    phase === "preparing" ||
    phase === "uploading" ||
    phase === "completing" ||
    phase === "pausing";
  const canUpload = !!selection;
  const showPause =
    phase === "uploading" || phase === "paused" || phase === "pausing";

  // 进度条可见的运行阶段；各阶段的 i18n key 与阶段名一致，按名取文案即可
  const showProgress = ACTIVE_PHASES.has(phase);
  const progressText = showProgress
    ? t(`components:uploadProject.${phase}`)
    : "";

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("components:uploadProject.title")}</DialogTitle>
          <DialogDescription>
            {t("components:uploadProject.desc")}
          </DialogDescription>
        </DialogHeader>

        {pendingResume && !busy && (
          <div className="rounded-md border border-border bg-muted/40 p-2.5 text-xs">
            <p className="font-medium">
              {t("components:uploadProject.resumableTitle", {
                name: pendingResume.name,
              })}
            </p>
            <p className="mt-1 text-muted-foreground">
              {t("components:uploadProject.resumableHint")}
            </p>
            <div className="mt-2 flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  try {
                    await api.projects.uploadSessions.abort(
                      pendingResume.sessionId,
                    );
                  } catch {
                    // 已过期/已清理：仅清本地记录
                  }
                  clearResumeStorage();
                }}
              >
                {t("components:uploadProject.discardResume")}
              </Button>
            </div>
          </div>
        )}

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
              {selection
                ? `${selection.name} · ${formatBytes(selection.totalBytes)}`
                : t("components:uploadProject.chooseArchive")}
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              {t("components:uploadProject.chunkHint")}
            </p>
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
              <FolderUp />
              {dirLabel && selection
                ? `${dirLabel} · ${selection.files.length} ${t("components:uploadProject.files")}`
                : t("components:uploadProject.chooseDir")}
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              {t("components:uploadProject.dirHint")}
            </p>
          </TabsContent>
        </Tabs>

        {showProgress && (
          <div className="space-y-1">
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {progressText} {phase === "completing" ? "" : `${progress}%`}
            </p>
          </div>
        )}

        <DialogFooter>
          {!busy && (
            <Button variant="outline" onClick={() => handleClose(false)}>
              {t("components:uploadProject.cancel")}
            </Button>
          )}
          {showPause && (
            <>
              <Button
                variant="outline"
                onClick={togglePause}
                disabled={phase === "pausing"}
              >
                {phase === "uploading" ? <Pause /> : <Play />}
                {phase === "uploading"
                  ? t("components:uploadProject.pauseBtn")
                  : t("components:uploadProject.resumeBtn")}
              </Button>
              <Button
                variant="destructive"
                onClick={abortAndReset}
                disabled={phase === "completing"}
              >
                {t("components:uploadProject.abortUpload")}
              </Button>
            </>
          )}
          {!showPause && (
            <Button onClick={handleUploadClick} disabled={busy || !canUpload}>
              {busy ? <Loader className="animate-spin" /> : <UploadCloud />}
              {busy
                ? t("components:uploadProject.busy")
                : t("components:uploadProject.upload")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
