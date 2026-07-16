import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import useStore from "@/store/useStore";

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
    checkLoading: "正在检查...",
    checkSuccess: (count) => `发现 ${count} 个段落可能有问题`,
    applySuccess: (idx) => `已应用段落 ${idx} 的修改`,
    applyAllSuccess: (count) => `已批量应用 ${count} 个段落的修改`,
    applyAllEmpty: "没有可应用的修改",
  },
}) {
  const fileTree = useStore((s) => s.fileTree);
  const projectPath = useStore((s) => s.projectPath);
  const activeFile = useStore((s) => s.activeFile);
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

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

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
        const data = await api.project.open(projectPath);
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
      toast.error("请先选择一个文件");
      return;
    }
    setLoading(storeSlice.loadingKey, true);
    setError(null);
    setMessage(null);
    setResults([]);
    setParagraphs([]);
    try {
      const data = await apiModule.preview(activeFile, resolveInput);
      setParagraphs(data.paragraphs || []);
      setMessage(`已加载 ${data.total} 个段落`);
    } catch (e) {
      setError(e.message);
      toast.error("加载段落失败", { description: e.message });
    } finally {
      setLoading(storeSlice.loadingKey, false);
    }
  }, [activeFile, resolveInput, apiModule, storeSlice.loadingKey, setLoading, setResults, setParagraphs]);

  const finalizeCheck = useCallback(
    (es, progressNull) => {
      es.close();
      eventSourceRef.current = null;
      pausedRef.current = false;
      bufferRef.current = [];
      setIsPaused(false);
      setLoading(storeSlice.loadingKey, false);
      if (progressNull) setCheckProgress(null);
    },
    [storeSlice.loadingKey, setLoading, setCheckProgress]
  );

  const drainBuffer = useCallback(() => {
    const buf = bufferRef.current;
    if (buf.length === 0) return;
    bufferRef.current = [];
    for (const r of buf) {
      if (r.changes && r.changes.length > 0) {
        resultsWithChangesRef.current += 1;
      }
      setResults((prev) => {
        const next = [...prev, r];
        next.sort((a, b) => {
          const aHas = a.changes && a.changes.length > 0;
          const bHas = b.changes && b.changes.length > 0;
          if (aHas !== bHas) return aHas ? -1 : 1;
          return a.paragraph_index - b.paragraph_index;
        });
        return next;
      });
    }
  }, [setResults]);

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
      encodeURIComponent(projectPath),
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
            if (pausedRef.current) {
              bufferRef.current.push(r);
            } else {
              if (r.changes && r.changes.length > 0) {
                resultsWithChangesRef.current += 1;
              }
              setResults((prev) => {
                const next = [...prev, r];
                next.sort((a, b) => {
                  const aHas = a.changes && a.changes.length > 0;
                  const bHas = b.changes && b.changes.length > 0;
                  if (aHas !== bHas) return aHas ? -1 : 1;
                  return a.paragraph_index - b.paragraph_index;
                });
                return next;
              });
            }
          }
          return;
        }

        if (data.type === "error") {
          setError(data.message);
          toast.error("检查出错", { description: data.message });
          return;
        }

        if (data.type === "done") {
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
      setError("连接中断，请重试");
      toast.error("连接中断，请重试");
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
  ]);

  const handleApplyChange = useCallback(
    async (idx, newText, filePath) => {
      try {
        await apiModule.apply({
          file_path: activeFile,
          changes: [{ paragraph_index: idx, new_text: newText, file_path: filePath || activeFile }],
        });
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
        toast.error("应用修改失败", { description: e.message });
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
      const toastId = toast.loading("正在批量应用修改...", {
        description: `共 ${pending.length} 个段落`,
      });
      try {
        const changes = pending.map((r) => ({
          paragraph_index: r.paragraph_index,
          new_text: r[correctedTextField],
          file_path: r.file_path || activeFile,
        }));
        await apiModule.apply({ file_path: activeFile, changes });
        const appliedKeys = new Set(
          pending.map((r) => `${r.file_path || activeFile}:${r.paragraph_index}`)
        );
        setResults((prev) =>
          prev.filter(
            (r) =>
              !appliedKeys.has(`${r.file_path || activeFile}:${r.paragraph_index}`)
          )
        );
        toast.success(messages.applyAllSuccess(pending.length), {
          id: toastId,
        });
      } catch (e) {
        setError(e.message);
        toast.error("批量应用失败", {
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
      bufferRef.current = [];
    };
  }, []);

  return {
    projectPath,
    activeFile,
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
