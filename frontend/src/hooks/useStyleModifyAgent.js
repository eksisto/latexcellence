import { useCallback, useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";

export default function useStyleModifyAgent() {
  const {
    workspaceId,
    sessionId,
    revision,
    operationId,
    mode,
    setStyleModifySession,
    setStyleModifyMode,
  } = useStore(
    useShallow((state) => ({
      workspaceId: state.workspaceId,
      sessionId: state.styleModifySessionId,
      revision: state.styleModifyRevision,
      operationId: state.styleModifyOperationId,
      mode: state.styleModifyMode,
      setStyleModifySession: state.setStyleModifySession,
      setStyleModifyMode: state.setStyleModifyMode,
    })),
  );

  const [proposal, setProposal] = useState(null);
  const [files, setFiles] = useState([]);
  const [steps, setSteps] = useState([]);
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [summary, setSummary] = useState("");
  // 思维链流式文本（只读展示，不参与任何后续流程）
  const [reasoningText, setReasoningText] = useState("");
  const [compile, setCompile] = useState(null);
  const [operation, setOperation] = useState(null);
  const [trace, setTrace] = useState(null);
  const [loadingTrace, setLoadingTrace] = useState(false);

  const abortRef = useRef(null);
  const stepKeyRef = useRef(0);
  const restoredRef = useRef(false);

  useEffect(() => () => abortRef.current?.abort(), []);

  const applyEvent = useCallback(
    (ev) => {
      if (ev.type === "error") {
        setError(translateError(ev.message || ""));
        return;
      }
      if (ev.type === "reasoning") {
        // 增量思维链：追加到思考面板，不进入步骤流
        setReasoningText((prev) => prev + (ev.text || ""));
        return;
      }
      if (ev.type === "warning") {
        setWarning(translateError(ev.message || ""));
        return;
      }
      if (ev.type === "session") {
        setStyleModifySession({ sessionId: ev.session_id });
        return;
      }
      if (ev.type === "proposal") {
        setProposal(ev.proposal);
        setFiles(ev.proposal?.files || []);
        setCompile(ev.proposal?.compile || null);
        setSummary(ev.proposal?.summary || "");
        setStyleModifySession({
          sessionId: useStore.getState().styleModifySessionId,
          revision: ev.proposal?.revision,
        });
        return;
      }
      if (ev.type === "compile") {
        setCompile({
          status: ev.status,
          main_file: ev.main_file,
          log: ev.log,
          errors: ev.errors || [],
        });
      }
      if (ev.type === "done") {
        setSummary(ev.summary || "");
        setStyleModifySession({
          sessionId: ev.session_id || useStore.getState().styleModifySessionId,
          revision: ev.revision || useStore.getState().styleModifyRevision,
        });
        return;
      }
      const step =
        ev.ok === false && ev.summary
          ? { ...ev, summary: translateError(ev.summary) }
          : ev;
      setSteps((prev) => [...prev, { ...step, _key: stepKeyRef.current++ }]);
    },
    [setStyleModifySession],
  );

  const handleStreamError = useCallback((e) => {
    setError(translateError(e?.message || String(e)));
  }, []);

  const loadSession = useCallback(
    async (sid) => {
      const target = sid || sessionId;
      if (!target || !workspaceId) return;
      setError("");
      try {
        const data = await api.styleModify.agent.session(target, workspaceId);
        setProposal(data.proposal);
        setFiles(data.files || []);
        setCompile(data.proposal?.compile || null);
        setSummary(data.proposal?.summary || "");
        setOperation(data.operation || null);
        setStyleModifySession({
          sessionId: target,
          revision: data.proposal?.revision || data.latest_revision,
          operationId: data.operation?.operation_id || null,
        });
      } catch (e) {
        setError(translateError(e.message));
      }
    },
    [sessionId, workspaceId, setStyleModifySession],
  );

  useEffect(() => {
    if (!sessionId || restoredRef.current) return;
    restoredRef.current = true;
    loadSession(sessionId);
  }, [sessionId, loadSession]);

  const run = useCallback(
    async (description) => {
      if (!workspaceId || !description.trim()) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setRunning(true);
      setError("");
      setWarning("");
      setSummary("");
      setSteps([]);
      setReasoningText("");
      setProposal(null);
      setFiles([]);
      setCompile(null);
      setOperation(null);
      try {
        await api.styleModify.agent.run({
          workspaceId,
          description: description.trim(),
          signal: controller.signal,
          onEvent: applyEvent,
          onError: handleStreamError,
        });
      } catch (e) {
        if (e?.name !== "AbortError") setError(translateError(e.message));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setRunning(false);
      }
    },
    [workspaceId, applyEvent, handleStreamError],
  );

  const refine = useCallback(
    async (message) => {
      if (!sessionId || !workspaceId || !message.trim()) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setRunning(true);
      setError("");
      setWarning("");
      setSummary("");
      setSteps([]);
      setReasoningText("");
      try {
        await api.styleModify.agent.refine({
          sessionId,
          workspaceId,
          message: message.trim(),
          signal: controller.signal,
          onEvent: applyEvent,
          onError: handleStreamError,
        });
      } catch (e) {
        if (e?.name !== "AbortError") setError(translateError(e.message));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setRunning(false);
      }
    },
    [sessionId, workspaceId, applyEvent, handleStreamError],
  );

  const apply = useCallback(async () => {
    if (!sessionId || !workspaceId || !revision || !proposal?.applicable)
      return;
    setApplying(true);
    setError("");
    try {
      const data = await api.styleModify.agent.apply(
        sessionId,
        revision,
        workspaceId,
      );
      setOperation(data);
      setStyleModifySession({
        sessionId,
        revision,
        operationId: data.operation_id,
      });
      await loadSession(sessionId);
    } catch (e) {
      setError(translateError(e.message));
    } finally {
      setApplying(false);
    }
  }, [
    sessionId,
    workspaceId,
    revision,
    proposal,
    loadSession,
    setStyleModifySession,
  ]);

  const undo = useCallback(async () => {
    const opId = operation?.operation_id || operationId;
    if (!opId || !workspaceId) return;
    setApplying(true);
    setError("");
    try {
      const data = await api.styleModify.agent.undo(opId, workspaceId);
      setOperation(data);
      setStyleModifySession({
        sessionId,
        revision,
        operationId: null,
      });
      if (sessionId) await loadSession(sessionId);
    } catch (e) {
      setError(translateError(e.message));
    } finally {
      setApplying(false);
    }
  }, [
    operation,
    operationId,
    workspaceId,
    sessionId,
    revision,
    loadSession,
    setStyleModifySession,
  ]);

  const deleteSession = useCallback(async () => {
    if (!sessionId || !workspaceId) return;
    setError("");
    try {
      await api.styleModify.agent.delete(sessionId, workspaceId);
      setStyleModifySession({});
      setProposal(null);
      setFiles([]);
      setSteps([]);
      setReasoningText("");
      setCompile(null);
      setOperation(null);
      setSummary("");
      setTrace(null);
    } catch (e) {
      setError(translateError(e.message));
    }
  }, [sessionId, workspaceId, setStyleModifySession]);

  const loadTrace = useCallback(async () => {
    if (!sessionId || !workspaceId) return;
    setLoadingTrace(true);
    try {
      const data = await api.styleModify.agent.trace(sessionId, workspaceId);
      setTrace(data);
    } catch (e) {
      setError(translateError(e.message));
    } finally {
      setLoadingTrace(false);
    }
  }, [sessionId, workspaceId]);

  const downloadTrace = useCallback(async () => {
    if (!sessionId || !workspaceId) return;
    try {
      await api.styleModify.agent.downloadTrace(sessionId, workspaceId);
    } catch (e) {
      setError(translateError(e.message));
    }
  }, [sessionId, workspaceId]);

  const setMode = useCallback(
    (nextMode) => setStyleModifyMode(nextMode),
    [setStyleModifyMode],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setRunning(false);
  }, []);

  return {
    workspaceId,
    sessionId,
    revision,
    operationId,
    mode,
    setMode,
    proposal,
    files,
    steps,
    reasoningText,
    running,
    applying,
    error,
    warning,
    summary,
    compile,
    operation,
    trace,
    loadingTrace,
    run,
    refine,
    stop,
    apply,
    undo,
    deleteSession,
    loadSession,
    loadTrace,
    downloadTrace,
  };
}
