import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
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

/** 自动保存停顿阈值：停止编辑该时长后落盘（决策 Q6） */
const AUTO_SAVE_DELAY_MS = 2500;

/**
 * 编辑器缓冲区管理：打开 / 脏标 / 保存（MD5 乐观锁） / 冲突快照门闩 /
 * Ctrl+S / beforeunload / 自动保存 / URL ?file= 驱动 / 刷新直达恢复。
 * 从 EditorPage 拆出，减少主文件行数并独立可测。
 *
 * A2 渲染解耦：本 hook 不做任何组件级 store 订阅（content/dirty 的订阅
 * 会让宿主页随每次按键重渲染），beforeunload / 自动保存 / 冲突快照门闩
 * 全部改为 useEditorStore.subscribe 命令式副作用，行为与原实现等价。
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
  /** 订阅回调内读取实时写权限（避免为 readOnly 建立组件级订阅）。
   * 经 useLayoutEffect 同步（commit 后、绘制前），不在 render 函数体
   * 内直接写 ref——并发渲染下被丢弃的渲染会把"未来值"提前写入 */
  const canWriteRef = useRef(canWrite);
  useLayoutEffect(() => {
    canWriteRef.current = canWrite;
  }, [canWrite]);

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
  useLayoutEffect(() => {
    saveRef.current = doSave;
  }, [doSave]);
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

  /* ── 关闭/刷新拦截 · 自动保存 · 冲突快照门闩（命令式订阅） ──────────
   * 三者原为组件级 content/dirty/conflict 订阅 effect，宿主页随每次按键
   * 重渲染；改为 store.subscribe 副作用驱动后零重渲染，语义逐条对齐：
   *   - dirty 置真挂 beforeunload、清假卸载；
   *   - autoSave 开启且 dirty、无冲突、非 viewer 时，内容停改 2.5s 落盘
   *     （真防抖：每次内容变化都重置计时器）；
   *   - 冲突弹窗被关闭后内容未再变化期间不武装定时器（防 409 回路），
   *     脏标清零解除快照门闩。 */
  useEffect(() => {
    let timer = null;
    let unloadBound = false;
    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const unloadHandler = (e) => {
      e.preventDefault();
      // Chrome/Edge 需要 returnValue 才会弹出确认
      e.returnValue = "";
    };
    const setUnloadBound = (bound) => {
      if (bound === unloadBound) return;
      if (bound) window.addEventListener("beforeunload", unloadHandler);
      else window.removeEventListener("beforeunload", unloadHandler);
      unloadBound = bound;
    };
    const armAutosave = () => {
      clearTimer();
      const s = useEditorStore.getState();
      if (!s.autoSave || !canWriteRef.current || !s.dirty || s.conflict) {
        return;
      }
      // 防回路门闩：冲突弹窗被关闭后，缓冲区自冲突以来未再变化时不自动
      // 重试保存（必然再次 409）；用户继续编辑（content 变化）即恢复节奏
      if (
        conflictSnapshotRef.current !== null &&
        s.content === conflictSnapshotRef.current
      ) {
        return;
      }
      timer = window.setTimeout(() => saveRef.current(), AUTO_SAVE_DELAY_MS);
    };

    const unsub = useEditorStore.subscribe((s, prev) => {
      // 脏标清零（成功保存 / 放弃重载 / 切换文件）解除冲突快照门闩
      if (!s.dirty && conflictSnapshotRef.current !== null) {
        conflictSnapshotRef.current = null;
      }
      if (s.dirty !== prev.dirty) setUnloadBound(s.dirty);
      if (
        s.content !== prev.content ||
        s.dirty !== prev.dirty ||
        s.conflict !== prev.conflict ||
        s.autoSave !== prev.autoSave
      ) {
        armAutosave();
      }
    });

    // 挂载时对齐既有状态（如路由恢复时缓冲区已脏）
    const initial = useEditorStore.getState();
    setUnloadBound(initial.dirty);
    armAutosave();

    return () => {
      clearTimer();
      unsub();
      setUnloadBound(false);
    };
  }, [canWrite]);
  // canWrite 入依赖：写权限变化（viewer 升级等）时重建订阅并立即重查
  // 自动保存武装，对齐旧组件级实现对 readOnly 变化的即时响应。

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
  };
}
