import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import useEditorStore from "@/store/editorStore";
import useStore from "@/store/useStore";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import { translateError } from "@/utils/errorMessages";
import { openMultiUserProject } from "@/utils/multiUserProject";
import {
  isEditableFilePath,
  isFileTooLarge,
  isPreviewableFilePath,
} from "@/lib/editor/files";

/**
 * 编辑器缓冲区管理：打开 / 脏标 / 保存（MD5 乐观锁） / 冲突快照门闩 /
 * Ctrl+S / beforeunload / 自动保存 / URL ?file= 驱动 / 刷新直达恢复。
 * 从 EditorPage 拆出，减少主文件行数并独立可测。
 *
 * @param {{ workspaceId: string, canWrite: boolean, setSearchParams: Function, t: Function, setFilePreviewPath: Function, setFilePreviewUrl: Function }} params
 */
export default function useEditorBuffer({
  workspaceId,
  canWrite,
  setSearchParams,
  t,
  setFilePreviewPath,
  setFilePreviewUrl,
}) {
  const readOnly = !canWrite;

  const session = useEditorStore((s) => ({
    filePath: s.filePath,
    content: s.content,
    dirty: s.dirty,
    conflict: s.conflict,
    autoSave: s.autoSave,
  }));
  const openBuffer = useEditorStore((s) => s.openBuffer);
  const markSaved = useEditorStore((s) => s.markSaved);
  const setConflict = useEditorStore((s) => s.setConflict);

  const [saving, setSaving] = useState(false);
  const [opening, setOpening] = useState(false);
  const [switchConfirmPath, setSwitchConfirmPath] = useState(null);

  const restoringRef = useRef(false);
  const openSeqRef = useRef(0);
  const savingRef = useRef(false);
  const conflictSnapshotRef = useRef(null);

  const describeError = useCallback((e, filePathForNotFound) => {
    if (e?.code === "file_not_found") {
      return translateError({ error: e.code, path: filePathForNotFound ?? "" });
    }
    return translateError({ error: e?.code, message: e?.message });
  }, []);

  /* ── 打开文件（返回是否成功；过期请求返回 false） ──────────────────── */
  const doOpenFile = useCallback(
    async (filePath) => {
      const seq = ++openSeqRef.current;
      setOpening(true);
      try {
        const data = await api.project.fileContent(filePath, workspaceId);
        if (seq !== openSeqRef.current) return false;
        if (isFileTooLarge(data.content)) {
          toast.error(t("pages:editor.fileTooLarge", { path: filePath }));
          return false;
        }
        openBuffer({ filePath, content: data.content, baseMd5: data.md5 });
        // 文本文件成功打开：收起只读预览覆盖层，让位给编辑器缓冲区
        setFilePreviewPath(null);
        setFilePreviewUrl(null);
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (next.get("file") !== filePath) next.set("file", filePath);
            return next;
          },
          { replace: true },
        );
        return true;
      } catch (e) {
        if (seq !== openSeqRef.current) return false;
        const cur = useEditorStore.getState();
        if (cur.filePath === filePath && !cur.dirty) {
          cur.closeBuffer();
        }
        toast.error(t("pages:editor.openFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      } finally {
        if (seq === openSeqRef.current) setOpening(false);
      }
    },
    [
      workspaceId,
      openBuffer,
      setSearchParams,
      t,
      describeError,
      setFilePreviewPath,
      setFilePreviewUrl,
    ],
  );

  /** 文件树点击入口：只读预览分流（PDF/位图） + 文本白名单 + 脏改动确认 */
  const requestOpenFile = useCallback(
    (filePath) => {
      if (!filePath || opening || saving) return;
      if (isPreviewableFilePath(filePath)) {
        setFilePreviewPath(filePath);
        return;
      }
      if (!isEditableFilePath(filePath)) {
        toast.error(t("pages:editor.unsupportedType", { path: filePath }));
        return;
      }
      const cur = useEditorStore.getState();
      if (cur.filePath === filePath) return;
      if (cur.dirty) {
        setSwitchConfirmPath(filePath);
        return;
      }
      doOpenFile(filePath);
    },
    [doOpenFile, opening, saving, t, setFilePreviewPath],
  );

  /* ── 保存（MD5 乐观锁；返回是否成功落账） ─────────────────────────── */
  const doSave = useCallback(async () => {
    if (savingRef.current) return false;
    const cur = useEditorStore.getState();
    if (!cur.filePath || !cur.dirty || cur.conflict || !canWrite) {
      return false;
    }
    savingRef.current = true;
    setSaving(true);
    const savedPath = cur.filePath;
    const savedContent = cur.content;
    try {
      const res = await api.project.saveFile(
        savedPath,
        savedContent,
        cur.baseMd5,
        workspaceId,
      );
      if (useEditorStore.getState().filePath !== savedPath) return false;
      markSaved(res.md5, savedContent);
      toast.success(t("pages:editor.saved"));
      return true;
    } catch (e) {
      if (useEditorStore.getState().filePath !== savedPath) return false;
      if (e?.code === "file_conflict") {
        setConflict({ message: describeError(e, savedPath) });
        conflictSnapshotRef.current = savedContent;
        toast.error(t("pages:editor.conflictToast"));
      } else {
        toast.error(t("pages:editor.saveFailed"), {
          description: describeError(e, savedPath),
        });
      }
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [canWrite, describeError, markSaved, setConflict, t, workspaceId]);

  /* ── 冲突弹窗「覆盖保存」出路 ────────────────────────────────────── */
  const handleOverwriteSave = useCallback(async () => {
    const cur = useEditorStore.getState();
    if (!cur.filePath || !canWrite || savingRef.current) return false;
    savingRef.current = true;
    setSaving(true);
    const savedPath = cur.filePath;
    const savedContent = cur.content;
    try {
      const res = await api.project.saveFile(
        savedPath,
        savedContent,
        null,
        workspaceId,
      );
      if (useEditorStore.getState().filePath !== savedPath) return false;
      markSaved(res.md5, savedContent);
      conflictSnapshotRef.current = null;
      setConflict(null);
      toast.success(t("pages:editor.saved"));
      return true;
    } catch (e) {
      if (useEditorStore.getState().filePath === savedPath) {
        toast.error(t("pages:editor.saveFailed"), {
          description: describeError(e, savedPath),
        });
      }
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [canWrite, describeError, markSaved, setConflict, t, workspaceId]);

  /* ── Ctrl/Cmd+S：window 级统一拦截 ─────────────────────────────────── */
  const saveRef = useRef(doSave);
  saveRef.current = doSave;
  useEffect(() => {
    const handler = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key?.toLowerCase() !== "s") return;
      e.preventDefault();
      saveRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  /* ── 关闭/刷新拦截 ──────────────────────────────────────────────── */
  useEffect(() => {
    if (!session.dirty) return undefined;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [session.dirty]);

  /* ── 自动保存偏好 ──────────────────────────────────────────────── */
  useEffect(() => {
    if (!session.autoSave || readOnly || !session.dirty || session.conflict) {
      return undefined;
    }
    if (
      conflictSnapshotRef.current !== null &&
      session.content === conflictSnapshotRef.current
    ) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      saveRef.current();
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [
    session.autoSave,
    readOnly,
    session.dirty,
    session.conflict,
    session.content,
  ]);

  // 脏标清零后解除冲突快照门闩
  useEffect(() => {
    if (!session.dirty) conflictSnapshotRef.current = null;
  }, [session.dirty]);

  /* ── 后端撤销/重做落盘后 / 冲突放弃本地：重拉文件内容刷新缓冲区 ──── */
  const handleReloadBuffer = useCallback(async () => {
    const cur = useEditorStore.getState();
    if (!cur.filePath) return false;
    return doOpenFile(cur.filePath);
  }, [doOpenFile]);

  const handleDiscardAndReload = useCallback(async () => {
    const ok = await handleReloadBuffer();
    if (ok) toast.success(t("pages:editor.reloadedFromDisk"));
    return ok;
  }, [handleReloadBuffer, t]);

  const handleSelectTreeNode = useCallback(
    (node) => {
      if (node.is_dir) return;
      requestOpenFile(node.path);
    },
    [requestOpenFile],
  );

  /* ── URL ?file= 参数驱动打开 ────────────────────────────────────── */
  // 注意：需要从外部传入 requestedFile，因为 URL 监听在 EditorPage 中
  // 这里只导出 doOpenFile 和 requestOpenFile，URL effect 留在 EditorPage

  /* ── 刷新直达恢复 ──────────────────────────────────────────────── */
  const isOpened = useStore((s) => s.isOpened);
  const fileTree = useStore((s) => s.fileTree);
  const hydrated = useStore((s) => s.hydrated);

  useEffect(() => {
    if (!hydrated || restoringRef.current || !isOpened) return;
    if (fileTree.length > 0) return;
    restoringRef.current = true;
    (async () => {
      try {
        const store = useStore.getState();
        if (isMultiUser()) {
          if (store.projectId) {
            await openMultiUserProject(store.projectId, workspaceId, {
              preserveActiveFile: true,
            });
          }
        } else if (store.projectPath) {
          const data = await api.project.open(store.projectPath, workspaceId);
          store.setRootPath(data.root_path);
          store.setFileTree(data.file_tree);
          store.setMainFile(data.main_file);
        }
      } catch (_) {
        // 恢复失败静默
      } finally {
        restoringRef.current = false;
      }
    })();
  }, [hydrated, isOpened, fileTree.length, workspaceId]);

  return {
    opening,
    saving,
    switchConfirmPath,
    setSwitchConfirmPath,
    doOpenFile,
    requestOpenFile,
    doSave,
    saveRef,
    handleOverwriteSave,
    handleReloadBuffer,
    handleDiscardAndReload,
    handleSelectTreeNode,
    conflictSnapshotRef,
    readOnly,
  };
}
