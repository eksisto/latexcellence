import { useState, useCallback, useMemo, useEffect, useRef } from "react";
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

export default function useParagraphCheck({
  apiModule,
  checkMethodName = "check",
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
  },
}) {
  const store = useStore();
  const {
    fileTree,
    projectPath,
    activeFile,
    setActiveFile,
    setRootPath,
    setProjectPath,
    setFileTree,
    setMainFile,
    loadingStates,
    setLoading,
  } = store;

  const paragraphs = store[storeSlice.paragraphsKey];
  const setParagraphs = store[storeSlice.setParagraphsKey];
  const results = store[storeSlice.resultsKey];
  const setResults = store[storeSlice.setResultsKey];

  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [restoring, setRestoring] = useState(false);
  const restoredRef = useRef(false);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

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
      setError("请先选择一个文件");
      return;
    }
    setLoading(storeSlice.loadingKey, true);
    setError(null);
    setMessage(null);
    setResults([]);
    setParagraphs([]);
    try {
      const data = await apiModule.preview(activeFile);
      setParagraphs(data.paragraphs || []);
      setMessage(`已加载 ${data.total} 个段落`);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(storeSlice.loadingKey, false);
    }
  }, [activeFile, apiModule, storeSlice.loadingKey, setLoading, setResults, setParagraphs]);

  const handleCheck = useCallback(async () => {
    if (!activeFile) return;
    setLoading(storeSlice.loadingKey, true);
    setError(null);
    setMessage(messages.checkLoading);
    try {
      const data = await apiModule[checkMethodName]({
        file_path: activeFile,
      });
      setResults(
        (data.results || []).slice().sort((a, b) => {
          const aHas = a.changes && a.changes.length > 0;
          const bHas = b.changes && b.changes.length > 0;
          if (aHas !== bHas) return aHas ? -1 : 1;
          return a.paragraph_index - b.paragraph_index;
        })
      );
      const withIssues = (data.results || []).filter(
        (r) => r.changes && r.changes.length > 0
      );
      setMessage(messages.checkSuccess(withIssues.length));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(storeSlice.loadingKey, false);
    }
  }, [
    activeFile,
    apiModule,
    checkMethodName,
    storeSlice.loadingKey,
    setLoading,
    setResults,
    messages,
  ]);

  const handleApplyChange = useCallback(
    async (idx, newText) => {
      try {
        await apiModule.apply({
          file_path: activeFile,
          changes: [{ paragraph_index: idx, new_text: newText }],
        });
        setMessage(messages.applySuccess(idx));
        // 从结果中移除已应用的段落，保留其余结果
        setResults((prev) => prev.filter((r) => r.paragraph_index !== idx));
      } catch (e) {
        setError(e.message);
      }
    },
    [activeFile, apiModule, setResults, messages]
  );

  const handleFileChange = useCallback(
    (path) => {
      setActiveFile(path);
      setParagraphs([]);
      setResults([]);
      setError(null);
      setMessage(null);
    },
    [setActiveFile, setParagraphs, setResults]
  );

  return {
    projectPath,
    activeFile,
    texFiles,
    paragraphs,
    results,
    isLoading: loadingStates[storeSlice.loadingKey],
    restoring,
    error,
    message,
    handlePreview,
    handleCheck,
    handleApplyChange,
    handleFileChange,
  };
}
