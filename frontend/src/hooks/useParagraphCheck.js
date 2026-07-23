import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";
import i18n from "@/i18n";

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
  storeSlice = {
    paragraphsKey: "typoParagraphs",
    setParagraphsKey: "setTypoParagraphs",
    resultsKey: "typoResults",
    setResultsKey: "setTypoResults",
    loadingKey: "typoCheck",
  },
  messages = {
    get checkLoading() { return i18n.t("hooks:paragraphCheck.defaultCheckLoading") },
    checkSuccess: (count) => i18n.t("hooks:paragraphCheck.defaultCheckSuccess", { count }),
    applySuccess: (idx) => i18n.t("hooks:paragraphCheck.defaultApplySuccess", { idx }),
    applyAllSuccess: (count) => i18n.t("hooks:paragraphCheck.defaultApplyAllSuccess", { count }),
    get applyAllEmpty() { return i18n.t("hooks:paragraphCheck.defaultApplyAllEmpty") },
  },
}) {
  const fileTree = useStore((s) => s.fileTree);
  const projectPath = useStore((s) => s.projectPath);
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
  const restoredRef = useRef(false);
  const eventSourceRef = useRef(null);
  const resultsWithChangesRef = useRef(0);
  const pausedRef = useRef(false);
  const bufferRef = useRef([]);
  const operationIdRef = useRef(null);
  const flushScheduledRef = useRef(false);

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
  const paragraphFiles = useMemo(() => extractFileList(paragraphs), [paragraphs]);
  const resultFiles = useMemo(() => extractFileList(results), [results]);
  const activeFiles = resultFiles.length > 0 ? resultFiles : paragraphFiles;

  // 项目恢复逻辑
  useEffect(() => {
    if (restoredRef.current) return;
    if (!projectPath || fileTree.length > 0) return;

    const restore = async () => {
      restoredRef.current = true;
      setRestoring(true);
      try {
        const data = await api.project.open(projectPath, workspaceId);
        setRootPath(data.root_path);
        setProjectPath(projectPath);
        setFileTree(data.file_tree);
        setMainFile(data.main_file);
        if (!activeFile) {
          setActiveFile(data.main_file);
        }
      } catch (_) {
        // 忽略错误
      } finally {
        setRestoring(false);
      }
    };

    restore();
  }, [
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
      const data = await apiModule.preview(activeFile, workspaceId, resolveInput);
      setParagraphs(data.paragraphs || []);
      operationIdRef.current = data.operation_id || null;
      setMessage(i18n.t("hooks:paragraphCheck.loadedParagraphs", { count: data.total }));
    } catch (e) {
      setError(e.message);
      toast.error(i18n.t("hooks:paragraphCheck.loadParagraphsFailed"), { description: e.message });
    } finally {
      setLoading(storeSlice.loadingKey, false);
    }
  }, [activeFile, resolveInput, apiModule, storeSlice.loadingKey, setLoading, setResults, setParagraphs]);

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
    [storeSlice.loadingKey, setLoading, setCheckProgress]
  );

  const flushResults = useCallback(
    (batch) => {
      if (batch.length === 0) return;
      const fileOrder = fileOrderRef.current;
      setResults((prev) => {
        const next = [...prev, ...batch];
        next.sort((a, b) => {
          const aHas = a.changes && a.changes.length > 0;
          const bHas = b.changes && b.changes.length > 0;
          if (aHas !== bHas) return aHas ? -1 : 1;
          const aOrder = fileOrder.get(a.file_path) ?? Infinity;
          const bOrder = fileOrder.get(b.file_path) ?? Infinity;
          if (aOrder !== bOrder) return aOrder - bOrder;
          return a.paragraph_index - b.paragraph_index;
        });
        return next;
      });
    },
    [setResults]
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
    if (!activeFile || !projectPath) return;

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

    const url = apiModule.stream(
      workspaceId,
      activeFile,
      resolveInput
    );

    const es = new EventSource(url);
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
            if ((r.changes && r.changes.length > 0) || (r.suggestions && r.suggestions.length > 0)) {
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
          toast.error(i18n.t("hooks:paragraphCheck.checkError"), { description: msg });
          return;
        }

        if (data.type === "done") {
          operationIdRef.current = data.operation_id || null;
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
    projectPath,
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
  ]);

  const handleApplyChange = useCallback(
    async (idx, newText, filePath) => {
      try {
        const result = await apiModule.apply({
          file_path: activeFile,
          changes: [{ paragraph_index: idx, new_text: newText, file_path: filePath || activeFile }],
          operation_id: operationIdRef.current,
        }, workspaceId);
        const hadErrors = result.errors && result.errors.length > 0;
        if (hadErrors) {
          const reasons = result.errors.map((e) => translateError(e.reason)).join("、");
          const msg = i18n.t("hooks:paragraphCheck.applyFailedHint", { reasons });
          setError(msg);
          toast.error(i18n.t("hooks:paragraphCheck.applyFailed"), { description: msg });
          return;
        }
        setMessage(messages.applySuccess(idx));
        toast.success(messages.applySuccess(idx));
        // 从结果中移除已应用的段落，保留其余结果
        setResults((prev) =>
          prev.filter(
            (r) => !(r.paragraph_index === idx && (r.file_path || activeFile) === (filePath || activeFile))
          )
        );
      } catch (e) {
        setError(e.message);
        toast.error(i18n.t("hooks:paragraphCheck.applyChangeFailed"), { description: e.message });
      }
    },
    [activeFile, apiModule, setResults, messages]
  );

  const handleIgnoreChange = useCallback(
    (idx, filePath) => {
      setResults((prev) =>
        prev.filter(
          (r) => !(r.paragraph_index === idx && (r.file_path || activeFile) === (filePath || activeFile))
        )
      );
    },
    [activeFile, setResults]
  );

  const handleApplyAll = useCallback(
    async (onConfirm) => {
      if (typeof onConfirm === "function") {
        const approved = await onConfirm();
        if (!approved) return;
      }
      const pending = results.filter(
        (r) => r.changes && r.changes.length > 0
      );
      if (pending.length === 0) {
        toast.info(messages.applyAllEmpty);
        return;
      }
      setApplyingAll(true);
      const toastId = toast.loading(i18n.t("hooks:paragraphCheck.applyingAll"), {
        description: i18n.t("hooks:paragraphCheck.applyingAllDesc", { count: pending.length }),
      });
      try {
        const changes = pending.map((r) => ({
          paragraph_index: r.paragraph_index,
          new_text: r[correctedTextField],
          file_path: r.file_path || activeFile,
        }));
        const result = await apiModule.apply({ file_path: activeFile, changes, operation_id: operationIdRef.current }, workspaceId);

        const skippedIndices = new Set();
        if (result.errors && result.errors.length > 0) {
          for (const e of result.errors) {
            skippedIndices.add(`${e.file_path || activeFile}:${e.paragraph_index}`);
          }
        }

        const appliedKeys = new Set(
          pending.map((r) => `${r.file_path || activeFile}:${r.paragraph_index}`)
        );
        for (const key of skippedIndices) {
          appliedKeys.delete(key);
        }
        setResults((prev) =>
          prev.filter(
            (r) =>
              !appliedKeys.has(`${r.file_path || activeFile}:${r.paragraph_index}`)
          )
        );

        const appliedCount = appliedKeys.size;
        const skippedCount = skippedIndices.size;

        if (skippedCount > 0) {
          const msg = i18n.t("hooks:paragraphCheck.applyAllPartial", { applied: appliedCount, skipped: skippedCount });
          setError(msg);
          toast.warning(msg, { id: toastId });
        } else {
          toast.success(messages.applyAllSuccess(appliedCount), {
            id: toastId,
          });
        }
      } catch (e) {
        setError(e.message);
        toast.error(i18n.t("hooks:paragraphCheck.batchApplyFailed"), {
          id: toastId,
          description: e.message,
        });
      } finally {
        setApplyingAll(false);
      }
    },
    [
      results,
      activeFile,
      apiModule,
      correctedTextField,
      setResults,
      messages,
    ]
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
    [setActiveFile, setParagraphs, setResults, setCheckProgress]
  );

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
    operationId: operationIdRef,
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
