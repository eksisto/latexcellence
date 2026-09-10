import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Undo2 from "lucide-react/dist/esm/icons/undo-2.mjs";
import Redo2 from "lucide-react/dist/esm/icons/redo-2.mjs";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import ConfirmDialog from "@/components/ConfirmDialog";
import useUndoRedo from "@/hooks/useUndoRedo";

/**
 * 模块级共享键盘监听。
 *
 * 整个应用只挂一个 document keydown 监听器（第一个实例挂载时注册，
 * 最后一个实例卸载时移除）。若同一时刻存在多个 UndoRedoButtons
 * （例如未来出现标签页布局），只让最近挂载的实例响应快捷键，
 * 避免一次按键触发多次撤销/重做。
 */
const shortcutHandlers = [];
let sharedKeydownInstalled = false;

function onSharedKeydown(e) {
  const target = e.target;
  const tag = target?.tagName;
  const isEditable =
    tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable;
  if (isEditable) return;
  if (!(e.ctrlKey || e.metaKey)) return;
  const key = e.key.toLowerCase();
  const handler = shortcutHandlers[shortcutHandlers.length - 1];
  if (!handler) return;
  if (key === "z" && !e.shiftKey) {
    e.preventDefault();
    handler.onUndo();
  } else if ((key === "z" && e.shiftKey) || key === "y") {
    e.preventDefault();
    handler.onRedo();
  }
}

function installSharedKeydown() {
  if (sharedKeydownInstalled) return;
  document.addEventListener("keydown", onSharedKeydown);
  sharedKeydownInstalled = true;
}

function uninstallSharedKeydown() {
  if (!sharedKeydownInstalled || shortcutHandlers.length > 0) return;
  document.removeEventListener("keydown", onSharedKeydown);
  sharedKeydownInstalled = false;
}

/**
 * 模块级共享轮询：undo-status 的 2.5s 定时刷新全应用只挂一个 interval
 * （第一个实例挂载时启动，最后一个实例卸载时清除）。若同一时刻存在
 * 多个 UndoRedoButtons，一次 tick 依次刷新全部活跃实例，避免 N 个
 * 实例 N 倍轮询——与上方 keydown 共享注册表同款先例。
 */
const pollRefreshers = new Set();
let sharedPollTimer = null;

function installSharedPolling() {
  if (sharedPollTimer !== null) return;
  sharedPollTimer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    for (const refresh of pollRefreshers) refresh();
  }, 2500);
}

function uninstallSharedPolling() {
  if (sharedPollTimer === null || pollRefreshers.size > 0) return;
  clearInterval(sharedPollTimer);
  sharedPollTimer = null;
}

/**
 * 可复用的撤销/重做按钮组。
 *
 * 用于逐段检查、文献库等页面：对当前激活文件执行撤销/重做，
 * 并轮询后端 undo-status，保证页面内其他写操作
 * （应用修改、保存条目等）完成后按钮状态能及时更新；页面不可见时暂停轮询。
 *
 * @param {string|null} activeFile      当前文件路径（相对项目根）
 * @param {string} workspaceId          工作区唯一标识符
 * @param {() => void|Promise<void>} onAfterUndoRedo  撤销/重做成功后刷新页面数据
 */
export default function UndoRedoButtons({
  activeFile,
  workspaceId,
  onAfterUndoRedo,
  className = "",
}) {
  const { t } = useTranslation(["common"]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { canUndo, canRedo, busy, handleUndo, handleRedo, refresh } =
    useUndoRedo(activeFile, workspaceId, onAfterUndoRedo);

  // 把最新 handler 放进 ref：快捷键监听只需注册一次，
  // canUndo/canRedo/busy 变化时不会反复卸载再挂载监听器
  const undoRef = useRef(handleUndo);
  const redoRef = useRef(handleRedo);
  useEffect(() => {
    undoRef.current = handleUndo;
    redoRef.current = handleRedo;
  });

  // 页面内其他写操作不会触发 useUndoRedo 的刷新，定时轮询保持按钮状态准确；
  // 执行撤销/重做期间 hook 内部会跳过刷新，避免与写操作抢请求。
  // 轮询 interval 为模块级共享（见 pollRefreshers 注释），实例只注册
  // 自己的 refresh；页面不可见时跳过刷新
  useEffect(() => {
    if (!activeFile) return;
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    pollRefreshers.add(refresh);
    installSharedPolling();
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      pollRefreshers.delete(refresh);
      uninstallSharedPolling();
    };
  }, [refresh, activeFile]);

  // 注册到共享快捷键监听（只依赖 activeFile，不随状态变化反复挂载）
  useEffect(() => {
    if (!activeFile) return;
    const handler = {
      onUndo: () => undoRef.current(),
      onRedo: () => redoRef.current(),
    };
    shortcutHandlers.push(handler);
    installSharedKeydown();
    return () => {
      const idx = shortcutHandlers.indexOf(handler);
      if (idx !== -1) shortcutHandlers.splice(idx, 1);
      uninstallSharedKeydown();
    };
  }, [activeFile]);

  return (
    <>
      <div className={`flex items-center gap-1 ${className}`}>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                onClick={() => setConfirmOpen(true)}
                disabled={!canUndo || busy}
              />
            }
          >
            <Undo2 />
          </TooltipTrigger>
          <TooltipContent>{t("undo")}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="icon"
                onClick={handleRedo}
                disabled={!canRedo || busy}
              />
            }
          >
            <Redo2 />
          </TooltipTrigger>
          <TooltipContent>{t("redo")}</TooltipContent>
        </Tooltip>
      </div>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("undoConfirmTitle")}
        description={t("undoConfirmDesc")}
        confirmText={t("undoConfirmBtn")}
        onConfirm={async () => {
          await handleUndo();
          setConfirmOpen(false);
        }}
      />
    </>
  );
}
