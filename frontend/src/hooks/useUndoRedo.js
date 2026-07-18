import { useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";

/**
 * 撤销/重做状态管理 hook。
 *
 * 监听 activeFile 变化，轮询后端 can-undo / can-redo 端点获取可用状态，
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

  const refresh = useCallback(async () => {
    if (!activeFile) {
      setCanUndo(false);
      setCanRedo(false);
      return;
    }
    try {
      const [u, r] = await Promise.all([
        api.project.canUndo(activeFile, workspaceId),
        api.project.canRedo(activeFile, workspaceId),
      ]);
      setCanUndo(Boolean(u.can_undo));
      setCanRedo(Boolean(r.can_redo));
    } catch (_) {
      // 后端不可用时静默降级，不阻塞 UI
      setCanUndo(false);
      setCanRedo(false);
    }
  }, [activeFile, workspaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleUndo = useCallback(async () => {
    if (!activeFile || busy || !canUndo) return;
    setBusy(true);
    try {
      const res = await api.project.undo(activeFile, workspaceId);
      if (res.undone) {
        toast.success("已撤销");
        await refresh();
        if (onAfterUndoRedo) await onAfterUndoRedo();
      } else {
        toast.info("没有可撤销的操作");
      }
    } catch (e) {
      toast.error("撤销失败", { description: e.message });
    } finally {
      setBusy(false);
    }
  }, [activeFile, busy, canUndo, refresh, onAfterUndoRedo]);

  const handleRedo = useCallback(async () => {
    if (!activeFile || busy || !canRedo) return;
    setBusy(true);
    try {
      const res = await api.project.redo(activeFile, workspaceId);
      if (res.redone) {
        toast.success("已重做");
        await refresh();
        if (onAfterUndoRedo) await onAfterUndoRedo();
      } else {
        toast.info("没有可重做的操作");
      }
    } catch (e) {
      toast.error("重做失败", { description: e.message });
    } finally {
      setBusy(false);
    }
  }, [activeFile, busy, canRedo, refresh, onAfterUndoRedo]);

  return { canUndo, canRedo, busy, handleUndo, handleRedo, refresh };
}
