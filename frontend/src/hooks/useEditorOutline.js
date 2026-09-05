import { useEffect, useRef, useState } from "react";

import useStore from "@/store/useStore";
import useEditorStore, {
  activeBuffer,
  getTabBuffer,
} from "@/store/editorStore";
import { api } from "@/api/index";
import { scanOutline } from "@/lib/editor/outlineScan";
import { reduceProjectKey } from "@/lib/editor/latexSymbols";

/** 大纲重扫防抖阈值：编辑激活缓冲区停改该时长后重扫（阶段三实现层决策） */
export const OUTLINE_RESCAN_DEBOUNCE_MS = 300;

/** 「本项目」视图保存后重拉防抖阈值：连续保存合并为一次重扫（阶段四） */
export const OUTLINE_PROJECT_REFETCH_DEBOUNCE_MS = 300;

/** 大纲视图：当前文件（激活缓冲区本地解析） */
export const OUTLINE_VIEW_FILE = "file";
/** 大纲视图：本项目（后端跨文件扫描） */
export const OUTLINE_VIEW_PROJECT = "project";

/** 当前文件视图的恒空 files（稳定引用，避免每 render 新数组触发下游 effect 空转） */
const EMPTY_FILES = [];

/**
 * 大纲面板数据源（editor-productivity-design §5.3 阶段三 / 决策 Q6；
 * editor-experience-design §4.4 / 决策 Q10 扩展「本项目」视图，阶段四）。
 *
 * view = OUTLINE_VIEW_FILE（当前文件，行为不变）：
 *   仅解析当前激活 tab 的缓冲区。重扫时机：
 *   - 切换 tab / 关闭 tab / 项目切换（activeTabPath 变化）：立即重扫，
 *     解析目标缓冲区内容；无激活 tab 时清空；
 *   - 编辑激活缓冲区：防抖 300ms 重扫。变更监听走
 *     useEditorStore.subscribe 命令式订阅（A2 渲染解耦：键入只做字符串
 *     引用比对，不触发面板重渲染），防抖到点后现取当下激活缓冲区扫描，
 *     期间切换 tab 时结果以当下激活 tab 为准，不会串档。
 *
 * view = OUTLINE_VIEW_PROJECT（本项目）：
 *   拉取后端 GET /api/project/outline 全项目 .tex 分节扫描（后端带项目级
 *   缓存 + 保存失效）。拉取时机：
 *   - 切入本项目视图 / 项目切换：立即拉取；
 *   - 任一 tab 保存成功落盘（savedBuffer 基线变化）：防抖 300ms 重拉
 *     （后端保存失效已保证重拉拿到新鲜数据，失败静默保持旧分组）。
 *
 * 防抖器清理：组件卸载时清定时器与订阅；拉取请求带 cancelled 守卫，
 * 卸载 / 依赖变化后的过期响应不入 state。
 *
 * @param {string} view 大纲视图（OUTLINE_VIEW_FILE / OUTLINE_VIEW_PROJECT）
 * @returns {{entries: Array, files: Array, loading: boolean}}
 *   当前文件视图返回 entries（files 恒为空数组）；本项目视图返回 files
 *   （[{file_path, entries}]，entries 恒为空数组）与 loading 态。
 */
export default function useEditorOutline(view = OUTLINE_VIEW_FILE) {
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const workspaceId = useStore((s) => s.workspaceId);
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const projectKey = reduceProjectKey({ projectPath, projectId });

  const [entries, setEntries] = useState([]);
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef(null);
  const projectTimerRef = useRef(null);
  const saveTickRef = useRef(0);
  const [saveTick, setSaveTick] = useState(0);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  /* ── 激活 tab 变化（切换/关闭/项目切换）：立即重扫目标缓冲区 ──────── */
  useEffect(() => {
    if (view !== OUTLINE_VIEW_FILE) return;
    clearTimer();
    if (!activeTabPath) {
      setEntries([]);
      return;
    }
    setEntries(scanOutline(activeBuffer(useEditorStore.getState())));
  }, [activeTabPath, view]);

  /* ── 编辑激活缓冲区：防抖重扫（命令式订阅，键入不重渲染面板） ──────── */
  useEffect(() => {
    if (view !== OUTLINE_VIEW_FILE) return;
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
  }, [view]);

  /* ── 本项目视图：切入 / 项目切换立即拉取；保存后防抖重拉 ───────────── */
  useEffect(() => {
    if (view !== OUTLINE_VIEW_PROJECT) return;
    if (!projectKey || !workspaceId) {
      setFiles([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api.project
      .projectOutline(workspaceId)
      .then((data) => {
        if (!cancelled) {
          setFiles(Array.isArray(data?.files) ? data.files : []);
        }
      })
      .catch(() => {
        // 拉取失败静默保持旧分组（沿袭 latex-symbols 缓存的静默策略）：
        // 不清空 files，面板继续展示上一次成功拉取的结果；仅由 finally
        // 收掉 loading 态。空 catch 吞掉 rejection 防未处理异常。
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [view, projectKey, workspaceId, saveTick]);

  useEffect(() => {
    if (view !== OUTLINE_VIEW_PROJECT) return;
    const unsub = useEditorStore.subscribe((s, prev) => {
      if (s.tabs === prev.tabs) return;
      // 任一 tab 的保存基线（savedBuffer）变化 = 一次成功落盘；
      // 连续保存（编译前全量脏保存 / 自动保存）合并为一次重拉
      let saved = false;
      if (s.tabs.length === prev.tabs.length) {
        for (let i = 0; i < s.tabs.length; i += 1) {
          if (s.tabs[i].filePath !== prev.tabs[i].filePath) break;
          if (s.tabs[i].savedBuffer !== prev.tabs[i].savedBuffer) {
            saved = true;
            break;
          }
        }
      } else {
        // tab 增删（关闭弹窗保存并关闭等）：退化为逐 path 比对
        const prevSaved = new Map(
          prev.tabs.map((tab) => [tab.filePath, tab.savedBuffer]),
        );
        for (const tab of s.tabs) {
          if (prevSaved.get(tab.filePath) !== tab.savedBuffer) {
            saved = true;
            break;
          }
        }
      }
      if (!saved) return;
      if (projectTimerRef.current !== null) {
        window.clearTimeout(projectTimerRef.current);
      }
      projectTimerRef.current = window.setTimeout(() => {
        projectTimerRef.current = null;
        saveTickRef.current += 1;
        setSaveTick(saveTickRef.current);
      }, OUTLINE_PROJECT_REFETCH_DEBOUNCE_MS);
    });
    return () => {
      if (projectTimerRef.current !== null) {
        window.clearTimeout(projectTimerRef.current);
        projectTimerRef.current = null;
      }
      unsub();
    };
  }, [view]);

  return view === OUTLINE_VIEW_PROJECT
    ? { entries: [], files, loading }
    : { entries, files: EMPTY_FILES, loading: false };
}
