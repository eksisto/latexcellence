import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import i18n from "@/i18n";

/**
 * 撤销/重做状态管理 hook。
 *
 * 监听 activeFile 变化，轮询后端 undo-status 端点获取可用状态，
 * 暴露 handleUndo / handleRedo 操作，并在操作后回调通知调用方刷新段落。
 *
 * @param {string|null} activeFile  当前激活的文件路径（相对项目根）
 * @param {string} workspaceId       工作区唯一标识符
 * @param {() => Promise<void>|void} onAfterUndoRedo  撤销/重做成功后回调（如重新加载段落）
 */
export default function useUndoRedo(activeFile, workspaceId, onAfterUndoRedo) {
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [busy, setBusy] = useState(false);

  const busyRef = useRef(false);
  const abortRef = useRef(null);

  const setBusyState = useCallback((value) => {
    busyRef.current = value;
    setBusy(value);
  }, []);

  /**
   * 真正发请求的刷新逻辑：
   * - 每次刷新先取消上一次未完成的请求，避免请求重叠；
   * - 旧文件/旧工作区的慢响应会随取消被丢弃，不会覆盖新状态。
   */
  const doRefresh = useCallback(async () => {
    abortRef.current?.abort();
    if (!activeFile) {
      setCanUndo(false);
      setCanRedo(false);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await api.project.undoStatus(activeFile, workspaceId, {
        signal: controller.signal,
      });
      setCanUndo(Boolean(data.can_undo));
      setCanRedo(Boolean(data.can_redo));
    } catch (e) {
      // 被新请求或组件卸载主动取消，静默忽略
      if (e?.name === "AbortError") return;
      // 后端不可用时静默降级，不阻塞 UI
      setCanUndo(false);
      setCanRedo(false);
    }
  }, [activeFile, workspaceId]);

  /** 对外暴露的刷新：执行撤销/重做期间跳过轮询，避免与写操作抢请求。 */
  const refresh = useCallback(() => {
    if (busyRef.current) return Promise.resolve();
    return doRefresh();
  }, [doRefresh]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // 组件卸载时取消未完成的请求，避免卸载后 setState
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const handleUndo = useCallback(async () => {
    if (!activeFile || busyRef.current || !canUndo) return;
    setBusyState(true);
    try {
      const res = await api.project.undo(activeFile, workspaceId);
      if (res.undone) {
        toast.success(i18n.t("hooks:undoRedo.undone"));
        await doRefresh();
        if (onAfterUndoRedo) await onAfterUndoRedo();
      } else {
        toast.info(i18n.t("hooks:undoRedo.nothingToUndo"));
      }
    } catch (e) {
      toast.error(i18n.t("hooks:undoRedo.undoFailed"), {
        description: e.message,
      });
    } finally {
      setBusyState(false);
    }
  }, [activeFile, canUndo, doRefresh, onAfterUndoRedo, setBusyState]);

  const handleRedo = useCallback(async () => {
    if (!activeFile || busyRef.current || !canRedo) return;
    setBusyState(true);
    try {
      const res = await api.project.redo(activeFile, workspaceId);
      if (res.redone) {
        toast.success(i18n.t("hooks:undoRedo.redone"));
        await doRefresh();
        if (onAfterUndoRedo) await onAfterUndoRedo();
      } else {
        toast.info(i18n.t("hooks:undoRedo.nothingToRedo"));
      }
    } catch (e) {
      toast.error(i18n.t("hooks:undoRedo.redoFailed"), {
        description: e.message,
      });
    } finally {
      setBusyState(false);
    }
  }, [activeFile, canRedo, doRefresh, onAfterUndoRedo, setBusyState]);

  return { canUndo, canRedo, busy, handleUndo, handleRedo, refresh };
}
