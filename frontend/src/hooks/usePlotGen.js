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

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(
    () => () => {
      previewSeqRef.current += 1;
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    [],
  );

  const handleEvent = useCallback((ev) => {
    if (ev.type === "error") {
      setError(translateError(ev.message || ""));
      return;
    }
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
  }, []);

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
    [sessionId, handleEvent, handleStreamError],
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
    [sessionId, handleEvent, handleStreamError],
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
        setFileContent(e.message);
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
      setError(e.message);
    }
  }, [sessionId]);

  const loadTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoadingTrace(true);
    try {
      const data = await api.plotGen.trace(sessionId);
      setTrace(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingTrace(false);
    }
  }, [sessionId]);

  const downloadTrace = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.plotGen.downloadTrace(sessionId);
    } catch (e) {
      setError(e.message);
    }
  }, [sessionId]);

  const reset = useCallback(() => {
    previewSeqRef.current += 1;
    setSessionId(null);
    setDataProfile(null);
    setChartPlan(null);
    setSteps([]);
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
  }, []);

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
