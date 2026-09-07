import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import useEditorStore, { activeFilePath, findTab } from "@/store/editorStore";
import useStore from "@/store/useStore";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import { translateError } from "@/utils/errorMessages";
import { openMultiUserProject } from "@/utils/multiUserProject";
import useIsOpened from "@/hooks/useIsOpened";
import {
  isEditableFilePath,
  isFileTooLarge,
  isPreviewableFilePath,
} from "@/lib/editor/files";
import {
  countDirtyTabs,
  diffTabsForAutosave,
  getDirtyTabsInOrder,
  planTabClose,
  planTabSave,
} from "@/lib/editor/tabs";
import tabViewRegistry from "@/lib/editor/tabViews";
import {
  clearSession,
  loadSessionAdoptingLegacy,
  saveSession,
} from "@/lib/editor/sessionPersistence";
import { projectSymbolCache } from "@/lib/editor/latexSymbols";

/** 自动保存停改阈值：停止编辑该时长后落盘（editor-productivity-design Q15） */
const AUTO_SAVE_DELAY_MS = 2500;
/** 会话持久化防抖：键入高频变更合并写入 localStorage（pagehide 兜底 flush） */
const SESSION_PERSIST_DEBOUNCE_MS = 1000;

/**
 * 编辑器多缓冲区管理（editor-productivity-design §5.1，阶段一）：
 * 打开 / 激活 / 关闭（脏三选）/ 保存（MD5 乐观锁，per-tab）/ 冲突快照门闩 /
 * Ctrl+S / beforeunload / per-tab 自动保存 / 陈旧检测 / 全部保存 /
 * URL ?file= 驱动 / localStorage 刷新恢复（阶段八升级，含 sessionStorage
 * 遗留迁移）/ 项目切换守卫。
 *
 * A2 渲染解耦：本 hook 不订阅缓冲区内容（content/dirty 的组件级订阅会让
 * 宿主页随每次按键重渲染），beforeunload / 自动保存 / 冲突快照门闩 /
 * 会话持久化全部走 useEditorStore.subscribe 命令式副作用。
 *
 * @param {{ workspaceId: string, canWrite: boolean, t: Function, setFilePreviewPath: Function, setFilePreviewUrl: Function }} params
 */
export default function useEditorBuffer({
  workspaceId,
  canWrite,
  t,
  setFilePreviewPath,
  setFilePreviewUrl,
}) {
  const openTab = useEditorStore((s) => s.openTab);
  const activateTab = useEditorStore((s) => s.activateTab);

  const [saving, setSaving] = useState(false);
  const [opening, setOpening] = useState(false);
  /** 关闭脏 tab 三选弹窗的归属路径（null = 弹窗关闭） */
  const [closingTabPath, setClosingTabPath] = useState(null);
  /** 陈旧确认弹窗的归属路径（保存前确认磁盘已变更） */
  const [staleConfirmPath, setStaleConfirmPath] = useState(null);

  /** 在途保存的 tab 集合（同名互斥；全局 saving = 集合非空） */
  const savingPathsRef = useRef(new Set());
  /** 冲突快照门闩（per-tab）：冲突弹窗被关闭后内容未再变化期间不自动重试 */
  const conflictSnapshotsRef = useRef(new Map());
  const openingCountRef = useRef(0);
  /** 自动保存 per-tab 防抖器 */
  const autosaveTimersRef = useRef(new Map());
  /** 会话恢复一次性门闩 */
  const sessionRestoredRef = useRef(false);
  /** 在途保存回调（自动保存定时器 / 编译前置经 ref 调用，不闭包过期） */
  const saveTabRef = useRef(() => Promise.resolve(false));

  /** 订阅回调内读取实时写权限（避免为 readOnly 建立组件级订阅）。
   * 经 useLayoutEffect 同步（commit 后、绘制前），不在 render 函数体
   * 内直接写 ref——并发渲染下被丢弃的渲染会把"未来值"提前写入 */
  const canWriteRef = useRef(canWrite);
  useLayoutEffect(() => {
    canWriteRef.current = canWrite;
  }, [canWrite]);

  /** 项目隔离键：单用户看 projectPath，多用户看 projectId */
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const projectKey = projectPath || projectId || null;
  const projectKeyRef = useRef(projectKey);
  useLayoutEffect(() => {
    projectKeyRef.current = projectKey;
  }, [projectKey]);

  const isOpened = useIsOpened();
  const fileTree = useStore((s) => s.fileTree);
  const hydrated = useStore((s) => s.hydrated);

  const describeError = useCallback((e, filePathForNotFound) => {
    if (e?.code === "file_not_found") {
      return translateError({ error: e.code, path: filePathForNotFound ?? "" });
    }
    return translateError({ error: e?.code, message: e?.message });
  }, []);

  const setOpeningCount = useCallback((delta) => {
    openingCountRef.current = Math.max(0, openingCountRef.current + delta);
    setOpening(openingCountRef.current > 0);
  }, []);

  const dismissFilePreview = useCallback(() => {
    setFilePreviewPath(null);
    setFilePreviewUrl(null);
  }, [setFilePreviewPath, setFilePreviewUrl]);

  /* ── 打开 / 激活（tab 已存在仅激活；不存在拉取后新开） ──────────────── */
  const doOpenFile = useCallback(
    async (filePath) => {
      const snapshot = useEditorStore.getState();
      if (findTab(snapshot, filePath)) {
        snapshot.activateTab(filePath);
        // 文本 tab 激活：收起只读预览覆盖层，让位给编辑器缓冲区
        dismissFilePreview();
        return true;
      }
      setOpeningCount(1);
      try {
        const data = await api.project.fileContent(filePath, workspaceId);
        if (isFileTooLarge(data.content)) {
          toast.error(t("pages:editor.fileTooLarge", { path: filePath }));
          return false;
        }
        // 迟到响应守卫：tab 已被并发打开（双击同文件等）时 openTab 幂等激活
        useEditorStore.getState().openTab({
          filePath,
          content: data.content,
          baseMd5: data.md5,
        });
        // 丢弃可能的旧 EditorState 存档（重开即全新缓冲区）
        tabViewRegistry.drop(filePath);
        dismissFilePreview();
        return true;
      } catch (e) {
        toast.error(t("pages:editor.openFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      } finally {
        setOpeningCount(-1);
      }
    },
    [workspaceId, t, describeError, dismissFilePreview, setOpeningCount],
  );

  /**
   * 显式重拉：以磁盘内容覆盖既有 tab 缓冲区（冲突放弃重载 / 后端撤销
   * 刷新共用），缓冲区与乐观锁基线一并复位，撤销历史随 EditorState
   * 重建而复位（对齐 v1 重挂载语义）。
   */
  const doReloadTab = useCallback(
    async (filePath) => {
      setOpeningCount(1);
      try {
        const data = await api.project.fileContent(filePath, workspaceId);
        if (isFileTooLarge(data.content)) {
          toast.error(t("pages:editor.fileTooLarge", { path: filePath }));
          return false;
        }
        if (!findTab(useEditorStore.getState(), filePath)) return false;
        useEditorStore.getState().openTab({
          filePath,
          content: data.content,
          baseMd5: data.md5,
        });
        tabViewRegistry.drop(filePath);
        return true;
      } catch (e) {
        toast.error(t("pages:editor.openFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      } finally {
        setOpeningCount(-1);
      }
    },
    [workspaceId, t, describeError, setOpeningCount],
  );

  /** 文件树点击入口：只读预览分流（PDF/位图） + 文本白名单 + 打开/激活 */
  const requestOpenFile = useCallback(
    (filePath) => {
      if (!filePath || opening) return;
      if (isPreviewableFilePath(filePath)) {
        setFilePreviewPath(filePath);
        return;
      }
      if (!isEditableFilePath(filePath)) {
        toast.error(t("pages:editor.unsupportedType", { path: filePath }));
        return;
      }
      doOpenFile(filePath);
    },
    [doOpenFile, opening, t, setFilePreviewPath],
  );

  /** 立即关闭 tab（不脏 / 三选弹窗裁决后）：store 移除 + 丢弃视图存档 */
  const closeTabImmediate = useCallback((filePath) => {
    useEditorStore.getState().closeTab(filePath);
    tabViewRegistry.drop(filePath);
  }, []);

  /** 关闭入口：脏 tab 转三选弹窗（决策 Q5），干净 tab 直接关闭 */
  const requestCloseTab = useCallback(
    (filePath) => {
      if (planTabClose(useEditorStore.getState(), filePath) === "confirm") {
        setClosingTabPath(filePath);
        return;
      }
      closeTabImmediate(filePath);
    },
    [closeTabImmediate],
  );

  /* ── 保存（MD5 乐观锁；返回是否成功落账） ─────────────────────────── */
  const doSaveTab = useCallback(
    async (
      filePath,
      { confirmedStale = false, overwrite = false, autosave = false } = {},
    ) => {
      if (savingPathsRef.current.has(filePath)) return false;
      const snapshot = useEditorStore.getState();
      const tab = findTab(snapshot, filePath);
      if (!canWriteRef.current) return false;
      // 保存前置决策矩阵（planTabSave）：冲突待决拦新保存（防 409 回路，
      // 覆盖保存作为弹窗出路放行）；陈旧分流确认弹窗 / 自动保存静默跳过
      const plan = planTabSave(tab, {
        conflictPath: snapshot.conflict?.filePath ?? null,
        overwrite,
        autosave,
        confirmedStale,
      });
      if (plan === "blocked") return false;
      if (plan === "stale_confirm") {
        // 陈旧守卫（决策 Q4）：抢激活位并弹确认窗；409 兜底不变
        if (snapshot.activeTabPath !== filePath) snapshot.activateTab(filePath);
        setStaleConfirmPath(filePath);
        return false;
      }
      savingPathsRef.current.add(filePath);
      setSaving(true);
      const savedBuffer = tab.buffer;
      const baseMd5 = overwrite ? null : tab.baseMd5;
      try {
        const res = await api.project.saveFile(
          filePath,
          savedBuffer,
          baseMd5,
          workspaceId,
        );
        // tab 在在途期间被关闭：内容已按用户决定落盘，无需回写
        if (!findTab(useEditorStore.getState(), filePath)) return false;
        useEditorStore.getState().markTabSaved(filePath, res.md5, savedBuffer);
        conflictSnapshotsRef.current.delete(filePath);
        // 引用符号缓存失效（阶段四 / Q7）：保存可能新增/删除 \label、
        // \cite 或 .bib 条目，下次触发补全时重拉
        projectSymbolCache.invalidate(projectKeyRef.current);
        if (overwrite) {
          // 覆盖保存成功即解决该 tab 冲突（弹窗出路）
          if (useEditorStore.getState().conflict?.filePath === filePath) {
            useEditorStore.getState().setConflict(null);
          }
        }
        toast.success(t("pages:editor.saved"));
        return true;
      } catch (e) {
        if (e?.code === "file_conflict") {
          conflictSnapshotsRef.current.set(filePath, savedBuffer);
          const cur = useEditorStore.getState();
          cur.setConflict({ filePath, message: describeError(e, filePath) });
          // 冲突归属 tab 置前：弹窗动作与视觉锚点对齐被冲突文件（Q16）
          if (cur.activeTabPath !== filePath) cur.activateTab(filePath);
          toast.error(t("pages:editor.conflictToast"));
        } else {
          toast.error(t("pages:editor.saveFailed"), {
            description: describeError(e, filePath),
          });
        }
        return false;
      } finally {
        savingPathsRef.current.delete(filePath);
        setSaving(savingPathsRef.current.size > 0);
      }
    },
    [workspaceId, t, describeError],
  );

  // 自动保存定时器经 ref 调用：陈旧 tab 静默跳过（autosave 通道），
  // 不弹确认窗不抢激活位；避免闭包过期
  useLayoutEffect(() => {
    saveTabRef.current = (path) => doSaveTab(path, { autosave: true });
  }, [doSaveTab]);

  /** 保存激活 tab（Ctrl+S / 工具栏保存按钮） */
  const doSaveActive = useCallback(async () => {
    const path = activeFilePath(useEditorStore.getState());
    if (!path) return false;
    return doSaveTab(path);
  }, [doSaveTab]);

  /**
   * 关闭弹窗「保存并关闭」（Q5）：弹窗待决期间 tab 可能已被自动保存落盘
   * （变干净）或经其他通路关闭——此时直接关闭，不再对干净 tab 发保存
   * 请求（planTabSave 会拒绝，弹窗将永久卡在「保存并关闭」上）；
   * 409 / 陈旧确认接管保存失败时保持弹窗打开，交由对应流程收尾。
   */
  const saveAndCloseTab = useCallback(
    async (filePath) => {
      if (planTabClose(useEditorStore.getState(), filePath) === "close") {
        closeTabImmediate(filePath);
        return true;
      }
      const ok = await doSaveTab(filePath);
      if (ok) closeTabImmediate(filePath);
      return ok;
    },
    [closeTabImmediate, doSaveTab],
  );

  /**
   * 依次保存所有脏 tab（tab 条顺序，Q16）；任一失败即中断并返回归属。
   * 409 / 陈旧确认会在 doSaveTab 内切到归属 tab 并弹对应弹窗。
   */
  const doSaveAllDirty = useCallback(async () => {
    const snapshot = useEditorStore.getState();
    const dirtyPaths = getDirtyTabsInOrder(snapshot.tabs).map(
      (tab) => tab.filePath,
    );
    for (const path of dirtyPaths) {
      const cur = useEditorStore.getState();
      const tab = findTab(cur, path);
      if (!tab || !tab.dirty) continue; // 在途已被保存 / 关闭
      const ok = await doSaveTab(path);
      if (!ok) return { ok: false, failedPath: path };
    }
    return { ok: true, failedPath: null };
  }, [doSaveTab]);

  /** 编译前置 ref：useEditorCompile 经此触发全量脏保存（Q16） */
  const saveAllRef = useRef(doSaveAllDirty);
  useLayoutEffect(() => {
    saveAllRef.current = doSaveAllDirty;
  }, [doSaveAllDirty]);

  /* ── Ctrl/Cmd+S：window 级统一拦截（保存激活 tab） ─────────────────── */
  const saveRef = useRef(doSaveActive);
  useLayoutEffect(() => {
    saveRef.current = doSaveActive;
  }, [doSaveActive]);
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

  /* ── beforeunload · per-tab 自动保存（命令式订阅，A2 解耦） ──────────
   *   - 任一 tab 脏改动挂 beforeunload、全清卸载；
   *   - autoSave 开启且该 tab 脏、无冲突、非 viewer 时，内容停改 2.5s
   *     落盘（per-tab 防抖器，Q15）；
   *   - 冲突弹窗被关闭后该 tab 内容未再变化期间不武装定时器（防 409
   *     回路），脏标清零解除快照门闩。 */
  useEffect(() => {
    let unloadBound = false;
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
    const clearAutosave = (path) => {
      const timer = autosaveTimersRef.current.get(path);
      if (timer !== undefined) {
        window.clearTimeout(timer);
        autosaveTimersRef.current.delete(path);
      }
    };
    const clearAllAutosave = () => {
      for (const timer of autosaveTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      autosaveTimersRef.current.clear();
    };
    const armAutosave = (path) => {
      clearAutosave(path);
      const s = useEditorStore.getState();
      if (!s.autoSave || !canWriteRef.current) return;
      const tab = findTab(s, path);
      if (!tab || !tab.dirty || s.conflict?.filePath === path) return;
      // 防回路门闩（per-tab）：冲突弹窗被关闭后，缓冲区自冲突以来未再
      // 变化时不自动重试保存（必然再次 409）；继续编辑即恢复节奏
      const latch = conflictSnapshotsRef.current.get(path);
      if (latch !== undefined && tab.buffer === latch) return;
      const timer = window.setTimeout(() => {
        autosaveTimersRef.current.delete(path);
        saveTabRef.current(path);
      }, AUTO_SAVE_DELAY_MS);
      autosaveTimersRef.current.set(path, timer);
    };

    const unsub = useEditorStore.subscribe((s, prev) => {
      // 任一 tab 脏 → 挂 beforeunload；全清 → 卸载
      const anyDirty = countDirtyTabs(s.tabs) > 0;
      const prevAnyDirty = countDirtyTabs(prev.tabs) > 0;
      if (anyDirty !== prevAnyDirty) setUnloadBound(anyDirty);
      // 脏标清零（成功保存 / 放弃重载 / 关闭 tab）解除冲突快照门闩
      if (!anyDirty && conflictSnapshotsRef.current.size > 0) {
        conflictSnapshotsRef.current.clear();
      }
      if (s.tabs !== prev.tabs) {
        // 前后对比产出武装/清除清单（含新增 tab——刷新恢复的脏 tab 在
        // 挂载对齐之后才经 restoreSession 出现，必须在此补武装）
        const { toArm, toClear } = diffTabsForAutosave(prev.tabs, s.tabs);
        for (const path of toClear) clearAutosave(path);
        for (const path of toArm) armAutosave(path);
      }
      if (s.conflict !== prev.conflict && s.conflict) {
        clearAutosave(s.conflict.filePath);
      }
      if (s.autoSave !== prev.autoSave) {
        if (!s.autoSave) clearAllAutosave();
        else {
          for (const tab of s.tabs) {
            if (tab.dirty) armAutosave(tab.filePath);
          }
        }
      }
    });

    // 挂载时对齐既有状态（重挂载场景）；刷新恢复经 restoreSession 触发的
    // tabs 变更晚于本对齐，由订阅回调内的 diffTabsForAutosave 补武装
    const initial = useEditorStore.getState();
    setUnloadBound(countDirtyTabs(initial.tabs) > 0);
    if (initial.autoSave) {
      for (const tab of initial.tabs) {
        if (tab.dirty) armAutosave(tab.filePath);
      }
    }

    return () => {
      clearAllAutosave();
      unsub();
      setUnloadBound(false);
    };
  }, []);
  // canWrite 经 canWriteRef 在订阅回调内现取：写权限变化（viewer 升级等）
  // 由下一次 store 变更自然重查，避免为 readOnly 重建订阅。

  /* ── 陈旧检测（Q4）：激活 tab 时惰性比对 file-md5，只提示不销毁 ───── */
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  useEffect(() => {
    if (!activeTabPath || !workspaceId) return undefined;
    let cancelled = false;
    const path = activeTabPath;
    (async () => {
      try {
        const data = await api.project.fileMd5(path, workspaceId);
        if (cancelled) return;
        const cur = useEditorStore.getState();
        const tab = findTab(cur, path);
        if (!tab) return;
        // 获取失败不误报：仅在有权威结果时回写标记（对齐检查保鲜语义）
        if (typeof data?.md5 === "string") {
          cur.setTabStale(path, data.md5 !== tab.baseMd5);
        }
      } catch (_) {
        // MD5 获取失败不误报
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTabPath, workspaceId]);

  /* ── 冲突弹窗出路（作用于冲突归属 tab） ───────────────────────────── */
  const handleOverwriteSave = useCallback(
    async (filePath) => {
      if (!filePath || savingPathsRef.current.has(filePath)) return false;
      return doSaveTab(filePath, { overwrite: true });
    },
    [doSaveTab],
  );

  /** 后端撤销/重做落盘后 / 冲突放弃本地：重拉文件内容刷新缓冲区 */
  const handleReloadBuffer = useCallback(
    async (filePath) => {
      const path = filePath || activeFilePath(useEditorStore.getState());
      if (!path) return false;
      return doReloadTab(path);
    },
    [doReloadTab],
  );

  const handleDiscardAndReload = useCallback(
    async (filePath) => {
      const path = filePath || activeFilePath(useEditorStore.getState());
      if (!path) return false;
      const ok = await doReloadTab(path);
      if (ok) {
        conflictSnapshotsRef.current.delete(path);
        if (useEditorStore.getState().conflict?.filePath === path) {
          useEditorStore.getState().setConflict(null);
        }
        toast.success(t("pages:editor.reloadedFromDisk"));
      }
      return ok;
    },
    [doReloadTab, t],
  );

  /* ── 会话恢复（Q14，阶段八升级 localStorage）：主存 localStorage，
     升级前的 sessionStorage 遗留载荷自动收养迁移 ──────────────────── */
  useEffect(() => {
    if (!hydrated || !isOpened || fileTree.length === 0) return;
    if (sessionRestoredRef.current) return;
    sessionRestoredRef.current = true;
    const result = loadSessionAdoptingLegacy(
      window.localStorage,
      window.sessionStorage,
      projectKeyRef.current,
    );
    if (!result.ok || result.tabs.length === 0) return;
    // 恢复全部 tab 集合（含脏缓冲区全文 + 保存基线 savedBuffer）；URL
    // ?file= 指定者的激活由 EditorPage 的 URL effect 合并（恢复结果与
    // URL 参数取并集）
    useEditorStore.getState().restoreSession({
      tabs: result.tabs,
      activeTabPath: result.activeTabPath,
    });
    toast.success(
      t("pages:editor.sessionRestored", { count: result.tabs.length }),
    );
  }, [hydrated, isOpened, fileTree.length, t]);

  /* ── 会话持久化（防抖写 localStorage；pagehide / 隐藏兜底 flush） ── */
  useEffect(() => {
    let timer = null;
    const flush = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (!projectKeyRef.current) return;
      const s = useEditorStore.getState();
      const result = saveSession(window.localStorage, {
        projectKey: projectKeyRef.current,
        tabs: s.tabs,
        activeTabPath: s.activeTabPath,
      });
      if (result.overflow) {
        // 全为脏 tab 仍超限（Q14）：提示用户保存，不再淘汰任何缓冲区
        toast.warning(t("pages:editor.sessionOverflowToast"));
      }
    };
    const schedule = () => {
      if (timer === null) {
        timer = window.setTimeout(flush, SESSION_PERSIST_DEBOUNCE_MS);
      }
    };
    const onPageHide = () => flush();
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") flush();
    };
    const unsub = useEditorStore.subscribe((s, prev) => {
      if (s.tabs !== prev.tabs || s.activeTabPath !== prev.activeTabPath) {
        schedule();
      }
    });
    schedule();
    window.addEventListener("pagehide", onPageHide);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      unsub();
      if (timer !== null) window.clearTimeout(timer);
      window.removeEventListener("pagehide", onPageHide);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [t]);

  /* ── 项目切换守卫：tab/冲突/视图存档/持久化载荷按项目隔离 ──────────── */
  const prevProjectKeyRef = useRef(projectKey);
  useEffect(() => {
    if (prevProjectKeyRef.current === projectKey) return;
    prevProjectKeyRef.current = projectKey;
    const cur = useEditorStore.getState();
    if (cur.tabs.length > 0 || cur.activeTabPath || cur.conflict) {
      cur.resetSession();
    }
    tabViewRegistry.clear();
    // 双存储清除：localStorage 主存 + 升级期可能的 sessionStorage 遗留
    clearSession(window.localStorage);
    clearSession(window.sessionStorage);
    // 引用符号缓存按项目隔离：项目切换/关闭时清空（阶段四 / Q7）
    projectSymbolCache.clear();
    // 允许新项目挂载后尝试恢复（载荷通常为空，静默跳过）
    sessionRestoredRef.current = false;
    setClosingTabPath(null);
    setStaleConfirmPath(null);
  }, [projectKey]);

  /* ── 刷新直达项目恢复 ──────────────────────────────────────────── */
  const projectRestoringRef = useRef(false);
  useEffect(() => {
    if (!hydrated || projectRestoringRef.current || !isOpened) return;
    if (fileTree.length > 0) return;
    projectRestoringRef.current = true;
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
        projectRestoringRef.current = false;
      }
    })();
  }, [hydrated, isOpened, fileTree.length, workspaceId]);

  const handleSelectTreeNode = useCallback(
    (node) => {
      if (node.is_dir) return;
      requestOpenFile(node.path);
    },
    [requestOpenFile],
  );

  /* ── 文件树新建 / 删除（响应携带重扫后 file_tree，单次往返刷新） ────── */
  const handleCreateFile = useCallback(
    async (filePath) => {
      try {
        const res = await api.project.createFile(filePath, workspaceId);
        useStore.getState().setFileTree(res.file_tree);
        // 新建 .bib 可能引入 cite key、新建 .tex 可能新增 \label：
        // 失效引用符号缓存，下次补全触发时重拉（对齐保存路径语义）
        projectSymbolCache.invalidate(projectKeyRef.current);
        toast.success(t("pages:editor.created", { path: filePath }));
        // 自动打开新 tab（复用打开通路：拉取内容 + 乐观锁基线）
        await doOpenFile(filePath);
        return true;
      } catch (e) {
        toast.error(t("pages:editor.createFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      }
    },
    [workspaceId, t, describeError, doOpenFile],
  );

  const handleDeleteFile = useCallback(
    async (filePath) => {
      try {
        const res = await api.project.deleteFile(filePath, workspaceId);
        useStore.getState().setFileTree(res.file_tree);
        projectSymbolCache.invalidate(projectKeyRef.current);
        // 该文件的全部 tab 一并关闭（含脏缓冲区；确认弹窗已警示未保存
        // 修改将丢失）——文件已不在磁盘，保留 tab 只会保存 404
        if (findTab(useEditorStore.getState(), filePath)) {
          closeTabImmediate(filePath);
        }
        toast.success(t("pages:editor.deleted", { path: filePath }));
        return true;
      } catch (e) {
        toast.error(t("pages:editor.deleteFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      }
    },
    [workspaceId, t, describeError, closeTabImmediate],
  );

  const handleSetMainFile = useCallback(
    async (filePath) => {
      try {
        await api.project.setMainFile(filePath, workspaceId);
        // 同步全局 mainFile：文件树的当前主文件星标即时迁移；
        // 后端已持久化到 .latexcellence/main_file，重开项目不丢失
        useStore.getState().setMainFile(filePath);
        toast.success(t("pages:editor.mainFileSet", { path: filePath }));
        return true;
      } catch (e) {
        toast.error(t("pages:editor.mainFileSetFailed"), {
          description: describeError(e, filePath),
        });
        return false;
      }
    },
    [workspaceId, t, describeError],
  );

  return {
    opening,
    saving,
    closingTabPath,
    setClosingTabPath,
    staleConfirmPath,
    setStaleConfirmPath,
    doOpenFile,
    requestOpenFile,
    requestCloseTab,
    closeTabImmediate,
    doSaveTab,
    doSaveActive,
    saveAndCloseTab,
    doSaveAllDirty,
    doSaveAllDirtyRef: saveAllRef,
    saveRef,
    handleOverwriteSave,
    handleReloadBuffer,
    handleDiscardAndReload,
    handleSelectTreeNode,
    handleCreateFile,
    handleDeleteFile,
    handleSetMainFile,
    conflictSnapshotsRef,
  };
}
