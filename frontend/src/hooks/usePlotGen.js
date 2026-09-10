import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import { getUiLanguage } from "@/i18n";

export default function usePlotGen() {
  const [sessionId, setSessionId] = useState(null);
  const [dataProfile, setDataProfile] = useState(null);
  const [chartPlan, setChartPlan] = useState(null);
  const [steps, setSteps] = useState([]);
  const [files, setFiles] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [summary, setSummary] = useState("");
  // 思维链流式文本（只读展示，不参与任何后续流程）
  const [reasoningText, setReasoningText] = useState("");
  const [selectedFile, setSelectedFile] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [loadingFile, setLoadingFile] = useState(false);
  const [trace, setTrace] = useState(null);
  const [loadingTrace, setLoadingTrace] = useState(false);
  const [previewSrc, setPreviewSrc] = useState("");

  const abortRef = useRef(null);
  const previewUrlRef = useRef("");
  const previewSeqRef = useRef(0);
  const stepKeyRef = useRef(0);
  // 思维链事件可能逐 token 到达：先累积进 ref，requestAnimationFrame 批量
  // 提交（每帧至多一次 setState），避免逐事件整页重渲染
  // （useParagraphCheck 的 scheduleFlush 为同款先例）
  const reasoningBufRef = useRef("");
  const reasoningFlushRef = useRef(0);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      cancelAnimationFrame(reasoningFlushRef.current);
    },
    [],
  );
  useEffect(
    () => () => {
      previewSeqRef.current += 1;
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    [],
  );

  const flushReasoning = useCallback(() => {
    if (reasoningFlushRef.current) {
      cancelAnimationFrame(reasoningFlushRef.current);
      reasoningFlushRef.current = 0;
    }
    if (!reasoningBufRef.current) return;
    const chunk = reasoningBufRef.current;
    reasoningBufRef.current = "";
    setReasoningText((prev) => prev + chunk);
  }, []);

  const clearReasoning = useCallback(() => {
    if (reasoningFlushRef.current) {
      cancelAnimationFrame(reasoningFlushRef.current);
      reasoningFlushRef.current = 0;
    }
    reasoningBufRef.current = "";
    setReasoningText("");
  }, []);

  const handleEvent = useCallback(
    (ev) => {
      if (ev.type === "error") {
        flushReasoning();
        setError(translateError(ev.message || ""));
        return;
      }
      if (ev.type === "reasoning") {
        // 增量思维链：追加到思考面板，不进入步骤流
        reasoningBufRef.current += ev.text || "";
        if (!reasoningFlushRef.current) {
          reasoningFlushRef.current = requestAnimationFrame(() => {
            reasoningFlushRef.current = 0;
            flushReasoning();
          });
        }
        return;
      }
      // 非 reasoning 事件先落盘缓冲的思维链文本，保持展示顺序
      flushReasoning();
      if (ev.type === "warning") {
        setWarning(translateError(ev.message || ""));
        return;
      }
      if (ev.type === "data_profile") {
        setDataProfile(ev.data_profile);
        return;
      }
      if (ev.type === "chart_plan") {
        setChartPlan(ev.chart_plan);
        return;
      }
      if (ev.type === "done") {
        setSessionId(ev.session_id);
        setFiles(ev.files || []);
        setArtifacts(ev.artifacts || []);
        setSummary(ev.summary || "");
        return;
      }
      const step =
        ev.ok === false && ev.summary
          ? { ...ev, summary: translateError(ev.summary) }
          : ev;
      setSteps((prev) => [...prev, { ...step, _key: stepKeyRef.current++ }]);
    },
    [flushReasoning],
  );

  const handleStreamError = useCallback((e) => {
    setError(translateError(e?.message || String(e)));
  }, []);

  const upload = useCallback(async (file, description, style) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("description", description);
    formData.append("style", style);
    formData.append("language", getUiLanguage());
    const data = await api.plotGen.upload(formData);
    setSessionId(data.session_id);
    setDataProfile(data.data_profile);
    setError("");
    setWarning("");
    setSteps([]);
    setFiles([]);
    setArtifacts([]);
    return data;
  }, []);

  const generate = useCallback(
    async (description, style, sessionIdOverride) => {
      const sid = sessionIdOverride || sessionId;
      if (!sid) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setRunning(true);
      setError("");
      setWarning("");
      setSteps([]);
      clearReasoning();
      setSummary("");
      setFiles([]);
      setArtifacts([]);
      try {
        await api.plotGen.generate({
          sessionId: sid,
          description,
          style,
          signal: controller.signal,
          onEvent: handleEvent,
          onError: handleStreamError,
        });
      } catch (e) {
        if (e?.name !== "AbortError") setError(translateError(e.message));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setRunning(false);
      }
    },
    [sessionId, handleEvent, handleStreamError, clearReasoning],
  );

  const refine = useCallback(
    async (message) => {
      if (!sessionId || !message.trim()) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setRunning(true);
      setError("");
      setWarning("");
      setSteps([]);
      clearReasoning();
      try {
        await api.plotGen.refine({
          sessionId,
          message: message.trim(),
          signal: controller.signal,
          onEvent: handleEvent,
          onError: handleStreamError,
        });
      } catch (e) {
        if (e?.name !== "AbortError") setError(translateError(e.message));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setRunning(false);
      }
    },
    [sessionId, handleEvent, handleStreamError, clearReasoning],
  );

  const loadFile = useCallback(
    async (path) => {
      if (!sessionId) return;
      setSelectedFile(path);
      setLoadingFile(true);
      try {
        const data = await api.plotGen.file(sessionId, path);
        setFileContent(data.content || "");
      } catch (e) {
        setFileContent(translateError(e.message));
      } finally {
        setLoadingFile(false);
      }
    },
    [sessionId],
  );

  const loadPreview = useCallback(
    async (path) => {
      const seq = ++previewSeqRef.current;
      if (!sessionId) {
        setPreviewSrc("");
        return;
      }
      try {
        const url = await api.plotGen.previewBlobUrl(sessionId, path);
        if (seq !== previewSeqRef.current) {
          // 已有更新的加载请求，丢弃本次结果并释放 blob，避免竞态覆盖新预览
          URL.revokeObjectURL(url);
          return;
        }
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
        previewUrlRef.current = url;
        setPreviewSrc(url);
      } catch (_) {
        if (seq === previewSeqRef.current) setPreviewSrc("");
      }
    },
    [sessionId],
  );

  const clearPreview = useCallback(() => {
    previewSeqRef.current += 1;
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = "";
    setPreviewSrc("");
  }, []);

  const download = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.plotGen.download(sessionId);
    } catch (e) {
      setError(translateError(e.message));
    }
  }, [sessionId]);

  const loadTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoadingTrace(true);
    try {
      const data = await api.plotGen.trace(sessionId);
      setTrace(data);
    } catch (e) {
      setError(translateError(e.message));
    } finally {
      setLoadingTrace(false);
    }
  }, [sessionId]);

  const downloadTrace = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.plotGen.downloadTrace(sessionId);
    } catch (e) {
      setError(translateError(e.message));
    }
  }, [sessionId]);

  const reset = useCallback(() => {
    previewSeqRef.current += 1;
    setSessionId(null);
    setDataProfile(null);
    setChartPlan(null);
    setSteps([]);
    clearReasoning();
    setFiles([]);
    setArtifacts([]);
    setRunning(false);
    setError("");
    setWarning("");
    setSummary("");
    setSelectedFile("");
    setFileContent("");
    setTrace(null);
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = "";
    setPreviewSrc("");
  }, [clearReasoning]);

  const clearFile = useCallback(() => {
    setSelectedFile("");
    setFileContent("");
  }, []);

  return {
    sessionId,
    dataProfile,
    chartPlan,
    steps,
    files,
    artifacts,
    running,
    error,
    warning,
    summary,
    reasoningText,
    selectedFile,
    fileContent,
    loadingFile,
    trace,
    loadingTrace,
    upload,
    generate,
    refine,
    loadFile,
    previewSrc,
    loadPreview,
    clearPreview,
    download,
    loadTrace,
    downloadTrace,
    reset,
    clearFile,
  };
}
