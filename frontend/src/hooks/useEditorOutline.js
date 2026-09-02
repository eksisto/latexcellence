import { useEffect, useRef, useState } from "react";

import useEditorStore, {
  activeBuffer,
  getTabBuffer,
} from "@/store/editorStore";
import { scanOutline } from "@/lib/editor/outlineScan";

/** 大纲重扫防抖阈值：编辑激活缓冲区停改该时长后重扫（阶段三实现层决策） */
export const OUTLINE_RESCAN_DEBOUNCE_MS = 300;

/**
 * 大纲面板数据源（editor-productivity-plan §5.3 阶段三 / 决策 Q6）：
 * 仅解析当前激活 tab 的缓冲区（跨文件大纲留二期）。
 *
 * 重扫时机：
 *   - 切换 tab / 关闭 tab / 项目切换（activeTabPath 变化）：立即重扫，
 *     解析目标缓冲区内容；无激活 tab 时清空；
 *   - 编辑激活缓冲区：防抖 300ms 重扫。变更监听走
 *     useEditorStore.subscribe 命令式订阅（A2 渲染解耦：键入只做字符串
 *     引用比对，不触发面板重渲染），防抖到点后现取当下激活缓冲区扫描，
 *     期间切换 tab 时结果以当下激活 tab 为准，不会串档。
 *
 * 防抖器清理：组件卸载时清定时器与订阅；tab 关闭 / 项目切换经
 * activeTabPath 变化的立即重扫路径取消在途定时器，不会向已关闭的
 * 缓冲区投递过期结果。
 *
 * @returns {Array<{level:number,title:string,line:number}>} 大纲条目
 */
export default function useEditorOutline() {
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const [entries, setEntries] = useState([]);
  const timerRef = useRef(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  /* ── 激活 tab 变化（切换/关闭/项目切换）：立即重扫目标缓冲区 ──────── */
  useEffect(() => {
    clearTimer();
    if (!activeTabPath) {
      setEntries([]);
      return;
    }
    setEntries(scanOutline(activeBuffer(useEditorStore.getState())));
  }, [activeTabPath]);

  /* ── 编辑激活缓冲区：防抖重扫（命令式订阅，键入不重渲染面板） ──────── */
  useEffect(() => {
    const unsub = useEditorStore.subscribe((s, prev) => {
      if (s.activeTabPath !== prev.activeTabPath) return; // 换档走上方立即路径
      if (s.tabs === prev.tabs) return;
      const path = s.activeTabPath;
      if (!path) return;
      if (getTabBuffer(s, path) === getTabBuffer(prev, path)) return;
      clearTimer();
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        const cur = useEditorStore.getState();
        setEntries(cur.activeTabPath ? scanOutline(activeBuffer(cur)) : []);
      }, OUTLINE_RESCAN_DEBOUNCE_MS);
    });
    return () => {
      clearTimer();
      unsub();
    };
  }, []);

  return entries;
}
