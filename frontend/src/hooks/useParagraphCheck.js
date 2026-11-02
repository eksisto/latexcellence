import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import { isMultiUser } from "@/api/connection";
import useStore from "@/store/useStore";
import { openMultiUserProject } from "@/utils/multiUserProject";
import useCanWrite from "@/hooks/useCanWrite";
import useIsOpened from "@/hooks/useIsOpened";
import { translateError } from "@/utils/errorMessages";
import i18n from "@/i18n";

/**
 * 默认文案：模块级常量保证回调身份稳定（不随渲染重建），
 * getter 保持翻译懒求值，语言切换后仍能取到最新文案。
 */
const DEFAULT_MESSAGES = {
  get checkLoading() {
    return i18n.t("hooks:paragraphCheck.defaultCheckLoading");
  },
  checkSuccess: (count) =>
    i18n.t("hooks:paragraphCheck.defaultCheckSuccess", { count }),
  applySuccess: (idx) =>
    i18n.t("hooks:paragraphCheck.defaultApplySuccess", { idx }),
  applyAllSuccess: (count) =>
    i18n.t("hooks:paragraphCheck.defaultApplyAllSuccess", { count }),
  get applyAllEmpty() {
    return i18n.t("hooks:paragraphCheck.defaultApplyAllEmpty");
  },
};

export function flattenTexFiles(tree, prefix = "") {
  const result = [];
  for (const node of tree) {
    if (node.is_dir && node.children) {
      result.push(...flattenTexFiles(node.children, prefix + node.name + "/"));
    } else if (node.name.endsWith(".tex")) {
      result.push({ name: prefix + node.name, path: node.path });
    }
  }
  return result;
}

/**
 * 从段落/结果列表中提取去重的文件路径列表（保持出现顺序） */
export function extractFileList(items) {
  const seen = new Set();
  const files = [];
  for (const item of items) {
    const fp = item.file_path;
    if (fp && !seen.has(fp)) {
      seen.add(fp);
      files.push(fp);
    }
  }
  return files;
}

/**
 * 按 file_path 对结果进行分组
 * 返回 { [file_path]: result[] }
 */
export function groupByFile(items) {
  const groups = {};
  for (const item of items) {
    const fp = item.file_path || "";
    if (!groups[fp]) groups[fp] = [];
    groups[fp].push(item);
  }
  return groups;
}

export default function useParagraphCheck({
  apiModule,
  checkMethodName = "check",
  correctedTextField = "corrected",
  /** 自定义"是否有问题"判断函数，覆盖默认的 changes/suggestions 检查 */
  hasResultIssue: customHasResultIssue,
  storeSlice = {
    paragraphsKey: "typoParagraphs",
    setParagraphsKey: "setTypoParagraphs",
    resultsKey: "typoResults",
    setResultsKey: "setTypoResults",
    loadingKey: "typoCheck",
  },
  messages = DEFAULT_MESSAGES,
}) {
  const fileTree = useStore((s) => s.fileTree);
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);
  const activeFile = useStore((s) => s.activeFile);
  const workspaceId = useStore((s) => s.workspaceId);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const setRootPath = useStore((s) => s.setRootPath);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setMainFile = useStore((s) => s.setMainFile);
  const setLoading = useStore((s) => s.setLoading);
  const setCheckProgress = useStore((s) => s.setCheckProgress);
  const checkProgress = useStore((s) => s.checkProgress);
  const isLoading = useStore((s) => s.loadingStates[storeSlice.loadingKey]);

  const paragraphs = useStore((s) => s[storeSlice.paragraphsKey]);
  const setParagraphs = useStore((s) => s[storeSlice.setParagraphsKey]);
  const results = useStore((s) => s[storeSlice.resultsKey]);
  const setResults = useStore((s) => s[storeSlice.setResultsKey]);

  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [restoring, setRestoring] = useState(false);
  const [applyingAll, setApplyingAll] = useState(false);
  const [resolveInput, setResolveInput] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  /** 保鲜横幅（editor-design §8）：检查后文件 MD5 已变化的文件列表 */
  const [staleFiles, setStaleFiles] = useState([]);
  // hasResultIssue 结果缓存：结果对象不可变，同一条跨帧只算一次
  //（SSE 流式期间每帧 flush 都要为排序取 has 值，重复遍历 changes/
  // suggestions 纯属浪费；WeakMap 按对象缓存，随对象消亡自动释放）
  const [issueCache] = useState(() => new WeakMap());
  // 多用户模式写权限：viewer 只读，写操作（应用更改/全部应用）直接拒绝
  const canWrite = useCanWrite();
  // 模式感知"项目已打开"：单用户看 projectPath，多用户看 projectId
  // （多用户下 projectPath 恒为 null，直接判会导致检查流程无法启动）
  const isOpened = useIsOpened();
  const restoredRef = useRef(false);
  const eventSourceRef = useRef(null);
  const resultsWithChangesRef = useRef(0);
  const pausedRef = useRef(false);
  const bufferRef = useRef([]);
  const operationIdRef = useRef(null);
  const flushScheduledRef = useRef(false);
  const fileMd5sRef = useRef({});

  /**
   * 判断单条检查结果是否包含实质性问题（错字/术语修改、LLM 审查建议等）。
   * 用于：排序时将有问题的结果置顶、统计"发现 x 个段落有问题"、
   * "全部应用"时筛选出需要批量写回的结果。
   *
   * 各功能对"有问题"的定义不同（code_review 额外将工具格式化差异视为问题），
   * 因此允许调用方通过 hasResultIssue 参数传入自定义实现。
   */
  const defaultHasResultIssue = useCallback((r) => {
    return (
      (r.changes && r.changes.length > 0) ||
      (r.suggestions && r.suggestions.length > 0) ||
      r.unlisted_changes === true
    );
  }, []);

  const hasResultIssue = customHasResultIssue || defaultHasResultIssue;

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

  // 从段落中提取 DFS 文件顺序，用于结果排序时保持文件分组
  const fileOrderRef = useRef(new Map());
  useEffect(() => {
    const map = new Map();
    for (const p of paragraphs) {
      if (p.file_path && !map.has(p.file_path)) {
        map.set(p.file_path, map.size);
      }
    }
    fileOrderRef.current = map;
  }, [paragraphs]);

  // 从段落和结果中提取文件列表（用于 Tab 展示）
  const paragraphFiles = useMemo(
    () => extractFileList(paragraphs),
    [paragraphs],
  );
  const resultFiles = useMemo(() => extractFileList(results), [results]);
  const activeFiles = resultFiles.length > 0 ? resultFiles : paragraphFiles;

  // 项目恢复逻辑：多用户模式按 projectId 恢复（projectPath 为空），
  // 单用户模式按 projectPath 恢复；两套逻辑按模式分支（方案 §11.2）。
  useEffect(() => {
    if (restoredRef.current) return;
    const multi = isMultiUser();
    const canRestore = multi ? !!projectId : !!projectPath;
    if (!canRestore || fileTree.length > 0) return;

    const restore = async () => {
      restoredRef.current = true;
      setRestoring(true);
      try {
        if (multi) {
          // 多用户：统一打开入口（openById + 并行角色同步），角色实时刷新，
          // 避免刷新直达模块页时 canWrite 依赖陈旧的持久化 projectRole
          await openMultiUserProject(projectId, workspaceId, {
            preserveActiveFile: true,
          });
        } else {
          const data = await api.project.open(projectPath, workspaceId);
          setRootPath(data.root_path);
          setProjectPath(projectPath);
          setFileTree(data.file_tree);
          setMainFile(data.main_file);
          if (!activeFile) {
            setActiveFile(data.main_file);
          }
        }
      } catch (_) {
        // 忽略错误
      } finally {
        setRestoring(false);
      }
    };

    restore();
  }, [
    projectId,
    projectPath,
    fileTree.length,
    activeFile,
    setRootPath,
    setProjectPath,
    setFileTree,
    setMainFile,
    setActiveFile,
  ]);

  const handlePreview = useCallback(async () => {
    if (!activeFile) {
      toast.error(i18n.t("hooks:paragraphCheck.selectFileFirst"));
      return;
    }
    setLoading(storeSlice.loadingKey, true);
    setError(null);
    setMessage(null);
    setResults([]);
    setParagraphs([]);
    try {
      const data = await apiModule.preview(
        activeFile,
        workspaceId,
        resolveInput,
      );
      setParagraphs(data.paragraphs || []);
      operationIdRef.current = data.operation_id || null;
      setMessage(
        i18n.t("hooks:paragraphCheck.loadedParagraphs", { count: data.total }),
      );
    } catch (e) {
      const msg = translateError(e.message);
      setError(msg);
      toast.error(i18n.t("hooks:paragraphCheck.loadParagraphsFailed"), {
        description: msg,
      });
    } finally {
      setLoading(storeSlice.loadingKey, false);
    }
  }, [
    activeFile,
    resolveInput,
    apiModule,
    storeSlice.loadingKey,
    setLoading,
    setResults,
    setParagraphs,
  ]);

  const finalizeCheck = useCallback(
    (es, progressNull) => {
      es.close();
      eventSourceRef.current = null;
      pausedRef.current = false;
      flushScheduledRef.current = false;
      bufferRef.current = [];
      setIsPaused(false);
      setLoading(storeSlice.loadingKey, false);
      if (progressNull) setCheckProgress(null);
    },
    [storeSlice.loadingKey, setLoading, setCheckProgress],
  );

  const flushResults = useCallback(
    (batch) => {
      if (batch.length === 0) return;
      const fileOrder = fileOrderRef.current;
      // 检查结果保鲜（editor-design §8 / 决策 Q13）：每条结果落检查时该文件
      // 的 MD5 版本戳，供检查页加载后与当前 MD5 比对渲染过期横幅
      const stamped = batch.map((r) => ({
        ...r,
        check_md5:
          r.check_md5 ?? fileMd5sRef.current[r.file_path || activeFile] ?? null,
      }));
      setResults((prev) => {
        const hasOf = (r) => {
          let has = issueCache.get(r);
          if (has === undefined) {
            has = hasResultIssue(r);
            issueCache.set(r, has);
          }
          return has;
        };
        const orderOf = (r) => fileOrder.get(r.file_path) ?? Infinity;
        const cmp = (a, b) => {
          const ha = hasOf(a);
          const hb = hasOf(b);
          if (ha !== hb) return ha ? -1 : 1;
          const oa = orderOf(a);
          const ob = orderOf(b);
          if (oa !== ob) return oa - ob;
          return a.paragraph_index - b.paragraph_index;
        };
        // SSE 流式增量到达：prev 已按 cmp 有序（下方归并维持该不变量，
        // 其余 setResults 写入路径均为保序的 filter/map 或整体重置），批内
        // 排序后线性归并即可——无须每帧对全量结果装饰-排序-去装饰，帧
        // 成本从 O(n log n) 降到 O(n + b log b)。平局取 prev 在前，
        // 与稳定排序的既有输出次序一致
        stamped.sort(cmp);
        const merged = new Array(prev.length + stamped.length);
        let i = 0;
        let j = 0;
        let k = 0;
        while (i < prev.length && j < stamped.length) {
          merged[k++] =
            cmp(prev[i], stamped[j]) <= 0 ? prev[i++] : stamped[j++];
        }
        while (i < prev.length) merged[k++] = prev[i++];
        while (j < stamped.length) merged[k++] = stamped[j++];
        return merged;
      });
    },
    [setResults, hasResultIssue, activeFile, issueCache],
  );

  const scheduleFlush = useCallback(() => {
    if (flushScheduledRef.current) return;
    flushScheduledRef.current = true;
    requestAnimationFrame(() => {
      flushScheduledRef.current = false;
      const buf = bufferRef.current;
      if (buf.length === 0) return;
      bufferRef.current = [];
      flushResults(buf);
    });
  }, [flushResults]);

  const drainBuffer = useCallback(() => {
    const buf = bufferRef.current;
    if (buf.length === 0) return;
    bufferRef.current = [];
    flushResults(buf);
  }, [flushResults]);

  const handlePauseResume = useCallback(() => {
    setIsPaused((prev) => {
      const next = !prev;
      pausedRef.current = next;
      if (!next) {
        drainBuffer();
      }
      return next;
    });
  }, [drainBuffer]);

  const handleStop = useCallback(() => {
    const es = eventSourceRef.current;
    if (!es) return;
    pausedRef.current = false;
    setIsPaused(false);
    drainBuffer();
    finalizeCheck(es, true);
    const count = resultsWithChangesRef.current;
    setMessage(messages.checkSuccess(count));
    toast.success(messages.checkSuccess(count));
  }, [finalizeCheck, drainBuffer, messages]);

  const handleCheck = useCallback(() => {
    if (!activeFile || !isOpened) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    pausedRef.current = false;
    bufferRef.current = [];
    setIsPaused(false);
    setLoading(storeSlice.loadingKey, true);
    setError(null);
    setMessage(messages.checkLoading);
    setResults([]);
    resultsWithChangesRef.current = 0;
    setCheckProgress({ current: 0, total: paragraphs.length });

    const url = apiModule.stream(workspaceId, activeFile, resolveInput);

    // 跨源部署时携带 Cookie（多用户会话认证）；同源下无副作用
    const es = new EventSource(url, { withCredentials: true });
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "progress") {
          setCheckProgress({
            current: data.current,
            total: data.total,
            file_path: data.file_path || null,
          });
          return;
        }

        if (data.type === "result") {
          const r = data.data;
          if (r) {
            if (hasResultIssue(r)) {
              resultsWithChangesRef.current += 1;
            }
            bufferRef.current.push(r);
            if (!pausedRef.current) {
              scheduleFlush();
            }
          }
          return;
        }

        if (data.type === "error") {
          const msg = translateError(data.message);
          setError(msg);
          toast.error(i18n.t("hooks:paragraphCheck.checkError"), {
            description: msg,
          });
          return;
        }

        if (data.type === "done") {
          operationIdRef.current = data.operation_id || null;
          // 术语检查等模块会在流结束时一次性推送全部 result + done，
          // 若 rAF 调度的 flush 尚未执行就 finalize 清空 buffer，
          // 结果会被丢弃。先排空 buffer 再收尾。
          drainBuffer();
          finalizeCheck(es, true);
          const count = resultsWithChangesRef.current;
          setMessage(messages.checkSuccess(count));
          toast.success(messages.checkSuccess(count));
          return;
        }
      } catch (_) {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      finalizeCheck(es, true);
      setError(i18n.t("hooks:paragraphCheck.connectionLost"));
      toast.error(i18n.t("hooks:paragraphCheck.connectionLost"));
    };
  }, [
    activeFile,
    isOpened,
    resolveInput,
    apiModule,
    storeSlice.loadingKey,
    paragraphs.length,
    setLoading,
    setCheckProgress,
    setResults,
    setError,
    messages,
    finalizeCheck,
    scheduleFlush,
    drainBuffer,
  ]);

  const refreshConflictMd5s = useCallback(
    async (errors) => {
      const conflictFiles = new Set(
        errors
          .filter((e) => e.reason === "file_conflict")
          .map((e) => e.file_path || activeFile),
      );
      // 各文件的 MD5 请求相互独立，并行发出（async-parallel，与下方
      // 保鲜比对的并行写法一致）；单个失败忽略该文件
      const settled = await Promise.all(
        [...conflictFiles].map(async (f) => {
          try {
            const md5Data = await api.project.fileMd5(f, workspaceId);
            return [f, md5Data.md5];
          } catch (_) {
            return null;
          }
        }),
      );
      for (const entry of settled) {
        if (entry) {
          fileMd5sRef.current = {
            ...fileMd5sRef.current,
            [entry[0]]: entry[1],
          };
        }
      }
    },
    [activeFile, workspaceId],
  );

  const handleApplyChange = useCallback(
    async (idx, newText, filePath) => {
      if (!canWrite) {
        toast.error(i18n.t("errors:noPermission"));
        return;
      }
      try {
        const targetFile = filePath || activeFile;
        const result = await apiModule.apply(
          {
            file_path: activeFile,
            changes: [
              {
                paragraph_index: idx,
                new_text: newText,
                file_path: targetFile,
                expected_md5: fileMd5sRef.current[targetFile] || null,
              },
            ],
            operation_id: operationIdRef.current,
          },
          workspaceId,
        );
        const hadErrors = result.errors && result.errors.length > 0;
        if (hadErrors) {
          const reasons = result.errors
            .map((e) => translateError(e.reason || e.detail))
            .join("、");
          const msg = i18n.t("hooks:paragraphCheck.applyFailedHint", {
            reasons,
          });
          setError(msg);
          toast.error(i18n.t("hooks:paragraphCheck.applyFailed"), {
            description: msg,
          });
          // 如果是文件冲突，刷新 MD5 供下次重试
          if (result.errors.some((e) => e.reason === "file_conflict")) {
            await refreshConflictMd5s(result.errors);
          }
          return;
        }
        setMessage(messages.applySuccess(idx));
        toast.success(messages.applySuccess(idx));
        // 写回成功即同步该文件最新 MD5（apply 响应携带），同文件连续
        // 应用不必先吃一次冲突再刷新
        if (result.file_md5s) {
          fileMd5sRef.current = { ...fileMd5sRef.current, ...result.file_md5s };
        }
        // 从结果中移除已应用的段落，保留其余结果
        setResults((prev) =>
          prev.filter(
            (r) =>
              !(
                r.paragraph_index === idx &&
                (r.file_path || activeFile) === (filePath || activeFile)
              ),
          ),
        );
      } catch (e) {
        const msg = translateError(e.message);
        setError(msg);
        toast.error(i18n.t("hooks:paragraphCheck.applyChangeFailed"), {
          description: msg,
        });
      }
    },
    [
      activeFile,
      workspaceId,
      apiModule,
      setResults,
      messages,
      refreshConflictMd5s,
    ],
  );

  const handleIgnoreChange = useCallback(
    (idx, filePath) => {
      setResults((prev) =>
        prev.filter(
          (r) =>
            !(
              r.paragraph_index === idx &&
              (r.file_path || activeFile) === (filePath || activeFile)
            ),
        ),
      );
    },
    [activeFile, setResults],
  );

  const handleApplyAll = useCallback(
    async (onConfirm) => {
      if (!canWrite) {
        toast.error(i18n.t("errors:noPermission"));
        return;
      }
      // 空集短路先于确认弹窗（async-cheap-condition-before-await）：
      // results.filter 是同步廉价操作，已无待应用项时不再打扰用户确认
      const pending = results.filter(hasResultIssue);
      if (pending.length === 0) {
        toast.info(messages.applyAllEmpty);
        return;
      }
      if (typeof onConfirm === "function") {
        const approved = await onConfirm();
        if (!approved) return;
      }
      setApplyingAll(true);
      const toastId = toast.loading(
        i18n.t("hooks:paragraphCheck.applyingAll"),
        {
          description: i18n.t("hooks:paragraphCheck.applyingAllDesc", {
            count: pending.length,
          }),
        },
      );
      try {
        const changes = pending.map((r) => ({
          paragraph_index: r.paragraph_index,
          new_text: r[correctedTextField],
          file_path: r.file_path || activeFile,
          expected_md5: fileMd5sRef.current[r.file_path || activeFile] || null,
        }));
        const result = await apiModule.apply(
          {
            file_path: activeFile,
            changes,
            operation_id: operationIdRef.current,
          },
          workspaceId,
        );

        const skippedIndices = new Set();
        if (result.errors && result.errors.length > 0) {
          for (const e of result.errors) {
            skippedIndices.add(
              `${e.file_path || activeFile}:${e.paragraph_index}`,
            );
          }
          // 刷新冲突文件的 MD5，供下次重试
          if (result.errors.some((e) => e.reason === "file_conflict")) {
            await refreshConflictMd5s(result.errors);
          }
        }
        // 写成功的文件同步最新 MD5（apply 响应携带），部分失败重试时
        // 不再携带过期基线
        if (result.file_md5s) {
          fileMd5sRef.current = { ...fileMd5sRef.current, ...result.file_md5s };
        }

        const appliedKeys = new Set(
          pending.map(
            (r) => `${r.file_path || activeFile}:${r.paragraph_index}`,
          ),
        );
        for (const key of skippedIndices) {
          appliedKeys.delete(key);
        }
        setResults((prev) =>
          prev.filter(
            (r) =>
              !appliedKeys.has(
                `${r.file_path || activeFile}:${r.paragraph_index}`,
              ),
          ),
        );

        const appliedCount = appliedKeys.size;
        const skippedCount = skippedIndices.size;

        if (skippedCount > 0) {
          const msg = i18n.t("hooks:paragraphCheck.applyAllPartial", {
            applied: appliedCount,
            skipped: skippedCount,
          });
          setError(msg);
          toast.warning(msg, { id: toastId });
        } else {
          toast.success(messages.applyAllSuccess(appliedCount), {
            id: toastId,
          });
        }
      } catch (e) {
        const msg = translateError(e.message);
        setError(msg);
        toast.error(i18n.t("hooks:paragraphCheck.batchApplyFailed"), {
          id: toastId,
          description: msg,
        });
      } finally {
        setApplyingAll(false);
      }
    },
    [
      results,
      activeFile,
      workspaceId,
      apiModule,
      correctedTextField,
      setResults,
      messages,
      hasResultIssue,
      refreshConflictMd5s,
    ],
  );

  const handleFileChange = useCallback(
    (path) => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      pausedRef.current = false;
      bufferRef.current = [];
      setIsPaused(false);
      setCheckProgress(null);
      setActiveFile(path);
      setParagraphs([]);
      setResults([]);
      setError(null);
      setMessage(null);
    },
    [setActiveFile, setParagraphs, setResults, setCheckProgress],
  );

  // ── 跨文件结果版本戳补齐 ────────────────────────────────────────────
  // fileMd5sRef 仅在预览/冲突刷新时写入，跨文件结果（file_path ≠
  // activeFile）会被戳 null 后静默脱离保鲜检测。此处对流中无戳的文件
  // 补拉当前 MD5 作为基线并回写结果——语义为「自本端首次可见起保鲜」；
  // 单个失败放弃该文件本轮基线，待下次 results 变化重试
  const stampBackfillSeqRef = useRef(0);
  useEffect(() => {
    if (isLoading) return;
    const missing = new Set();
    for (const r of results) {
      const fp = r.file_path || activeFile;
      if (fp && !r.check_md5 && !fileMd5sRef.current[fp]) missing.add(fp);
    }
    if (missing.size === 0) return;
    const seq = ++stampBackfillSeqRef.current;
    (async () => {
      const fetched = await Promise.all(
        [...missing].map(async (fp) => {
          try {
            const d = await api.project.fileMd5(fp, workspaceId);
            return [fp, d.md5];
          } catch (_) {
            return null;
          }
        }),
      );
      if (seq !== stampBackfillSeqRef.current) return;
      const got = fetched.filter(Boolean);
      if (got.length === 0) return;
      const md5ByFp = Object.fromEntries(got);
      fileMd5sRef.current = { ...fileMd5sRef.current, ...md5ByFp };
      setResults((prev) =>
        prev.map((r) => {
          const fp = r.file_path || activeFile;
          return fp && !r.check_md5 && md5ByFp[fp]
            ? { ...r, check_md5: md5ByFp[fp] }
            : r;
        }),
      );
    })();
  }, [results, isLoading, activeFile, workspaceId, setResults]);

  // ── 检查结果保鲜比对（editor-design §8 / 决策 Q13）─────────────────────
  // 结果就绪（检查流结束或从 persist 恢复）后，逐文件拉取当前 MD5 与
  // 检查时版本戳比对；不一致的文件进入横幅提示。只提示不销毁结果。
  const staleSeqRef = useRef(0);
  /** 上次已比对的版本戳集合签名：results 变化但戳集合未变时跳过重复
   * 比对（应用/忽略单条结果不再触发全量重拉，async-parallel） */
  const staleStampSigRef = useRef("");
  useEffect(() => {
    if (isLoading) return;
    const stamped = new Map();
    for (const r of results) {
      const fp = r.file_path || activeFile;
      if (fp && r.check_md5 && !stamped.has(fp)) {
        stamped.set(fp, r.check_md5);
      }
    }
    if (stamped.size === 0) {
      staleStampSigRef.current = "";
      setStaleFiles((prev) => (prev.length ? [] : prev));
      return;
    }
    // 签名含 workspaceId：切换工作区后即使戳集合相同也强制重新比对
    const sig = `${workspaceId}|${[...stamped]
      .map(([fp, md5]) => `${fp}:${md5}`)
      .join("|")}`;
    if (sig === staleStampSigRef.current) return;
    staleStampSigRef.current = sig;
    const seq = ++staleSeqRef.current;
    (async () => {
      // 各文件的 MD5 请求相互独立，并行发出（原逐个串行 await 使横幅
      // 出现时间随文件数线性放大）；单个失败不判过期（不误报）
      const settled = await Promise.all(
        [...stamped].map(async ([fp, checkMd5]) => {
          try {
            const data = await api.project.fileMd5(fp, workspaceId);
            return data.md5 !== checkMd5 ? fp : null;
          } catch (_) {
            return null;
          }
        }),
      );
      if (seq === staleSeqRef.current) setStaleFiles(settled.filter(Boolean));
    })();
  }, [results, isLoading, activeFile, workspaceId]);

  // 组件卸载时关闭 SSE 连接
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      pausedRef.current = false;
      flushScheduledRef.current = false;
      bufferRef.current = [];
    };
  }, []);

  return {
    projectPath,
    activeFile,
    workspaceId,
    texFiles,
    paragraphs,
    results,
    isLoading,
    progress: checkProgress,
    restoring,
    applyingAll,
    isPaused,
    error,
    message,
    resolveInput,
    setResolveInput,
    activeFiles,
    staleFiles,
    operationId: operationIdRef,
    canWrite,
    handlePreview,
    handleCheck,
    handlePauseResume,
    handleStop,
    handleApplyChange,
    handleApplyAll,
    handleIgnoreChange,
    handleFileChange,
  };
}
