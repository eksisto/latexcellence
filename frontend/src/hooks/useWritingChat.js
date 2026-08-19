import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import i18n, { getUiLanguage } from "@/i18n";
import { flattenTexFiles } from "@/hooks/useParagraphCheck";
import { translateError } from "@/utils/errorMessages";
import { useWritingChatSessions } from "./writing-chat/sessions";
import { useWritingChatAssistants } from "./writing-chat/assistants";
import { useWritingChatCaptcha } from "./writing-chat/captcha";
import { createSSEEventHandler } from "./writing-chat/sse";
import {
  isDraftSession,
  processLoadedMessages,
  uid,
} from "./writing-chat/utils";

export default function useWritingChat() {
  const projectPath = useStore((s) => s.projectPath);
  const workspaceId = useStore((s) => s.workspaceId);
  const fileTree = useStore((s) => s.fileTree);
  const activeFile = useStore((s) => s.activeFile);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const setRootPath = useStore((s) => s.setRootPath);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setMainFile = useStore((s) => s.setMainFile);
  const setLoading = useStore((s) => s.setLoading);
  const writingChatLoading = useStore((s) => s.loadingStates.writingChat);
  const writingChatSessions = useStore((s) => s.writingChatSessions);
  const writingChatMessages = useStore((s) => s.writingChatMessages);
  const writingChatCurrentSessionId = useStore(
    (s) => s.writingChatCurrentSessionId,
  );
  const writingChatCompressed = useStore((s) => s.writingChatCompressed);
  const setWritingChatSessions = useStore((s) => s.setWritingChatSessions);
  const setWritingChatMessages = useStore((s) => s.setWritingChatMessages);
  const setWritingChatCurrentSessionId = useStore(
    (s) => s.setWritingChatCurrentSessionId,
  );
  const setWritingChatCompressed = useStore((s) => s.setWritingChatCompressed);

  const [paragraphIndex, setParagraphIndex] = useState(null);
  const [paragraphs, setParagraphs] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const [editingMessageId, setEditingMessageId] = useState(null); // generate-edit 进行中
  const [applyingMessageId, setApplyingMessageId] = useState(null);
  // 工具调用状态（{ tool, label, args }），SSE tool_status/tool_result 事件控制
  const [toolStatus, _setToolStatus] = useState(null);
  const toolStatusStartRef = useRef(null);
  const clearToolTimeoutRef = useRef(null);
  const MIN_TOOL_STATUS_DISPLAY_TIME = 1500; // ms，工具状态指示器最短显示时间
  const setToolStatus = useCallback((value) => {
    if (value !== null) {
      toolStatusStartRef.current = Date.now();
      if (clearToolTimeoutRef.current) {
        clearTimeout(clearToolTimeoutRef.current);
        clearToolTimeoutRef.current = null;
      }
      _setToolStatus(value);
    } else {
      const elapsed = Date.now() - (toolStatusStartRef.current || 0);
      const remaining = MIN_TOOL_STATUS_DISPLAY_TIME - elapsed;
      if (remaining > 0) {
        clearToolTimeoutRef.current = setTimeout(
          () => _setToolStatus(null),
          remaining,
        );
      } else {
        _setToolStatus(null);
      }
    }
  }, []);

  // 待用户确认的工具调用（写操作：edit_paragraph / undo_last_edit）
  const [pendingTool, _setPendingTool] = useState(null);
  const setPendingTool = useCallback((value) => _setPendingTool(value), []);
  const pendingToolRef = useRef(null);
  useEffect(() => {
    pendingToolRef.current = pendingTool;
  }, [pendingTool]);

  const confirmPendingTool = useCallback(async (modifiedArgs) => {
    const tool = pendingToolRef.current;
    if (!tool || !tool.pending_id) return;
    try {
      await api.writingChat.confirmTool(
        tool.pending_id,
        modifiedArgs ?? undefined,
      );
    } catch (e) {
      // silent
    }
    _setPendingTool(null);
  }, []);

  const cancelPendingTool = useCallback(async () => {
    const tool = pendingToolRef.current;
    if (!tool || !tool.pending_id) return;
    try {
      await api.writingChat.cancelTool(tool.pending_id);
    } catch (e) {
      // silent
    }
    _setPendingTool(null);
  }, []);

  // 待用户回复的澄清请求（request_clarification）
  const [pendingClarification, _setPendingClarification] = useState(null);
  const setPendingClarification = useCallback(
    (value) => _setPendingClarification(value),
    [],
  );
  const pendingClarificationRef = useRef(null);
  useEffect(() => {
    pendingClarificationRef.current = pendingClarification;
  }, [pendingClarification]);

  const submitClarification = useCallback(async (answer) => {
    const c = pendingClarificationRef.current;
    if (!c || !c.clarify_id) return;
    try {
      await api.writingChat.clarify(c.clarify_id, answer);
    } catch (e) {
      // silent
    }
    _setPendingClarification(null);
  }, []);

  const cancelClarification = useCallback(async () => {
    const c = pendingClarificationRef.current;
    if (c && c.clarify_id) {
      try {
        await api.writingChat.cancelClarify(c.clarify_id);
      } catch (e) {
        // silent
      }
    }
    _setPendingClarification(null);
  }, []);

  const restoringRef = useRef(false);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

  // ── 使用子 hooks ──────────────────────────────────────────────────

  const {
    refreshSessions,
    selectSession,
    newSession,
    deleteSession,
    renameSession,
  } = useWritingChatSessions({
    workspaceId,
    activeFile,
    setActiveFile,
    setWritingChatSessions,
    setWritingChatMessages,
    setWritingChatCurrentSessionId,
    setWritingChatCompressed,
    writingChatSessions,
    writingChatCurrentSessionId,
    writingChatMessages,
  });

  const {
    assistants,
    activeAssistant,
    assistantContent,
    setAssistantActive,
    saveAssistant,
    deleteAssistant,
  } = useWritingChatAssistants();

  const {
    captchaChallenge,
    setCaptchaChallenge,
    captchaSubmitting,
    submitCaptcha,
    dismissCaptcha,
  } = useWritingChatCaptcha({ setError });

  // ── 最新值 refs：让事件回调保持稳定引用（流式更新时不重建回调） ──
  const activeFileRef = useRef(activeFile);
  const paragraphIndexRef = useRef(paragraphIndex);
  const inputRef = useRef(input);
  const streamingRef = useRef(streaming);
  const sessionIdRef = useRef(writingChatCurrentSessionId);
  const activeAssistantRef = useRef(activeAssistant);
  const assistantContentRef = useRef(assistantContent);
  const messagesRef = useRef(writingChatMessages);

  useEffect(() => {
    activeFileRef.current = activeFile;
    paragraphIndexRef.current = paragraphIndex;
    inputRef.current = input;
    streamingRef.current = streaming;
    sessionIdRef.current = writingChatCurrentSessionId;
    activeAssistantRef.current = activeAssistant;
    assistantContentRef.current = assistantContent;
    messagesRef.current = writingChatMessages;
  });

  // ── 项目恢复 ──────────────────────────────────────────────────────
  useEffect(() => {
    if (restoringRef.current) return;
    if (!projectPath || fileTree.length > 0) return;
    restoringRef.current = true;
    (async () => {
      try {
        const data = await api.project.open(projectPath, workspaceId);
        setRootPath(data.root_path);
        setProjectPath(projectPath);
        setFileTree(data.file_tree);
        setMainFile(data.main_file);
        if (!activeFile) setActiveFile(data.main_file);
      } catch (_) {
        // silent
      }
    })();
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

  // ── 页面刷新：水合完成后以后端 JSON 为准重新加载上次选中的会话 ────
  // 持久化的 writingChatMessages 可能因流式中断、多轮工具调用或会话
  // 删除/重建而滞后于磁盘 JSON；因此刷新后先清空再重新拉取，避免展示
  // 在 JSON 中找不到的"幽灵"内容。草稿会话仅存在于前端，直接保留。
  const hydrated = useStore((s) => s.hydrated);
  const hydrationRestoredRef = useRef(false);
  useEffect(() => {
    if (!hydrated || hydrationRestoredRef.current) return;
    hydrationRestoredRef.current = true;
    const sessionId = writingChatCurrentSessionId;
    if (!sessionId || isDraftSession(sessionId)) return;
    setWritingChatMessages([]);
    selectSession(sessionId);
  }, [
    hydrated,
    writingChatCurrentSessionId,
    selectSession,
    setWritingChatMessages,
  ]);

  // ── 加载段落预览（供段落选择 dropdown 使用） ─────────────────────
  const loadParagraphs = useCallback(async () => {
    if (!activeFile) {
      setParagraphs([]);
      return;
    }
    try {
      const data = await api.project.paragraphs(activeFile, workspaceId);
      setParagraphs(data.paragraphs || []);
    } catch (e) {
      setParagraphs([]);
    }
  }, [activeFile]);

  useEffect(() => {
    loadParagraphs();
  }, [loadParagraphs]);

  // ── 发送消息（流式） ────────────────────────────────────────────
  const sendMessage = useCallback(
    async (text) => {
      const content = (text ?? inputRef.current).trim();
      const currentActiveFile = activeFileRef.current;
      const currentParagraphIndex = paragraphIndexRef.current;
      const currentSessionId = sessionIdRef.current;
      const currentAssistant = activeAssistantRef.current;
      const currentAssistantContent = assistantContentRef.current;
      if (!content || !currentActiveFile || streamingRef.current) return;

      setInput("");
      setError(null);
      setStreaming(true);
      setLoading("writingChat", true);

      // 临时占位 user message + streaming assistant message
      const tmpUserId = uid();
      const tmpAssistantId = uid();
      setWritingChatMessages((prev) => [
        ...prev,
        {
          id: tmpUserId,
          role: "user",
          content,
          referenced_file: currentActiveFile,
          referenced_paragraph: currentParagraphIndex,
          tokens: 0,
        },
        {
          id: tmpAssistantId,
          role: "assistant",
          content: "",
          rawContent: "",
          thinkingContent: "",
          displayContent: "",
          thinkingOpen: false,
          thinkingClosed: true,
          tokens: 0,
          streaming: true,
          evidenceSources: [],
          pageReads: [],
          citedEvidenceIds: [],
          unverifiedEvidenceIds: [],
        },
      ]);

      const onEvent = createSSEEventHandler({
        tmpAssistantId,
        setWritingChatMessages,
        setToolStatus,
        setPendingTool,
        setPendingClarification,
        setCaptchaChallenge,
        setWritingChatCompressed,
        setWritingChatSessions,
        setWritingChatCurrentSessionId,
        setError,
        writingChatCurrentSessionId: currentSessionId,
      });

      try {
        await api.writingChat.send({
          sessionId: isDraftSession(currentSessionId) ? null : currentSessionId,
          filePath: currentActiveFile,
          paragraphIndex: currentParagraphIndex,
          userMessage: content,
          assistantPrompt:
            currentAssistant && currentAssistantContent
              ? currentAssistantContent
              : null,
          workspaceId,
          onEvent,
        });
      } catch (e) {
        const msg = translateError(e.message);
        setError(msg);
        setWritingChatMessages((prev) =>
          prev.filter((m) => m.id !== tmpAssistantId),
        );
        toast.error(msg, { description: msg });
      } finally {
        setStreaming(false);
        setLoading("writingChat", false);
        setToolStatus(null);
        // 后端 send 后可能新建会话或更新现有，刷新 session 列表
        refreshSessions();
      }
    },
    [
      setWritingChatMessages,
      setWritingChatCurrentSessionId,
      setLoading,
      setToolStatus,
      setPendingTool,
      setPendingClarification,
      setCaptchaChallenge,
      setWritingChatCompressed,
      setWritingChatSessions,
      setError,
      refreshSessions,
    ],
  );

  // ── 生成编辑（第二轮） ──────────────────────────────────────────
  const generateEdit = useCallback(
    async (messageId, instruction) => {
      const currentActiveFile = activeFileRef.current;
      const currentSessionId = sessionIdRef.current;
      const currentParagraphIndex = paragraphIndexRef.current;
      if (
        !currentActiveFile ||
        !currentSessionId ||
        currentParagraphIndex == null ||
        currentParagraphIndex < 0
      ) {
        toast.error(i18n.t("hooks:writingChat.selectFileAndParagraph"));
        return null;
      }
      setEditingMessageId(messageId);
      try {
        const data = await api.writingChat.generateEdit(
          {
            session_id: currentSessionId,
            file_path: currentActiveFile,
            paragraph_index: currentParagraphIndex,
            instruction:
              instruction ||
              "请基于刚才的对话内容修改此段落，使其更符合学术写作规范",
            language: getUiLanguage(),
          },
          workspaceId,
        );
        // 把 edit_op 附挂到对应 assistant 消息
        setWritingChatMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, edit_op: data.edit_op, edit_reason: data.reason }
              : m,
          ),
        );
        return data;
      } catch (e) {
        setError(e.message);
        toast.error(i18n.t("hooks:writingChat.generateEditFailed"), {
          description: e.message,
        });
        return null;
      } finally {
        setEditingMessageId(null);
      }
    },
    [setWritingChatMessages],
  );

  // ── 应用编辑 ────────────────────────────────────────────────────
  const applyEdit = useCallback(
    async (messageId, customText) => {
      const currentSessionId = sessionIdRef.current;
      if (!currentSessionId) return;
      // 若有自定义文本，先更新消息中的 new_latex
      if (customText) {
        setWritingChatMessages((prev) =>
          prev.map((m) =>
            m.id === messageId && m.edit_op
              ? { ...m, edit_op: { ...m.edit_op, new_latex: customText } }
              : m,
          ),
        );
      }
      setApplyingMessageId(messageId);
      try {
        // 若有自定义文本（用户手动编辑），将其传递给后端
        const payload = {
          session_id: currentSessionId,
          edit_message_id: messageId,
        };
        if (customText.trim()) {
          payload.new_latex = customText;
        }
        await api.writingChat.applyEdit(payload, workspaceId);
        setWritingChatMessages((prev) =>
          prev.map((m) =>
            m.id === messageId && m.edit_op
              ? { ...m, edit_op: { ...m.edit_op, applied: true } }
              : m,
          ),
        );
        toast.success(i18n.t("hooks:writingChat.editApplied"));
      } catch (e) {
        setError(e.message);
        toast.error(i18n.t("hooks:writingChat.applyEditFailed"), {
          description: e.message,
        });
      } finally {
        setApplyingMessageId(null);
      }
    },
    [setWritingChatMessages],
  );

  // ── 删除消息（summary 消息会还原快照） ────────────────────────
  const deleteMessage = useCallback(
    async (messageId) => {
      const currentSessionId = sessionIdRef.current;
      const currentMessages = messagesRef.current;
      if (!currentSessionId) return;
      let result;
      try {
        result = await api.writingChat.deleteMessage({
          session_id: currentSessionId,
          message_id: messageId,
        });
      } catch (e) {
        setError(e.message);
        toast.error(i18n.t("hooks:writingChat.deleteMessageFailed"), {
          description: e.message,
        });
        return;
      }
      try {
        // 重新拉取会话完整数据以同步 snapshot 还原结构
        const fresh = await api.writingChat.loadSession(currentSessionId);
        setWritingChatMessages(processLoadedMessages(fresh.messages || []));
        const hasCompressed = (fresh.messages || []).some(
          (m) => m.compressed && m.role === "summary",
        );
        setWritingChatCompressed(hasCompressed);
      } catch (e) {
        // 删除已成功，仅本地同步失败：尽力在本地应用等价变更
        setError(e.message);
        const next = [...currentMessages];
        const idx = next.findIndex((m) => m.id === messageId);
        if (idx !== -1) {
          const target = next[idx];
          if (target?.compressed && target?.snapshot?.length) {
            next.splice(idx, 1, ...target.snapshot);
          } else {
            next.splice(idx, 1);
          }
        }
        setWritingChatMessages(next);
        setWritingChatCompressed(
          next.some((m) => m.compressed && m.role === "summary"),
        );
        toast.warning(i18n.t("hooks:writingChat.deleteSyncFailed"), {
          description: e.message,
        });
        return;
      }
      if (result?.restored) {
        toast.success(i18n.t("hooks:writingChat.deleteCompressedSuccess"));
      } else if (result?.removed) {
        toast.success(i18n.t("hooks:writingChat.deleteSuccess"));
      }
    },
    [setWritingChatMessages, setWritingChatCompressed],
  );

  // ── 手动压缩会话 ──────────────────────────────────────────────
  const compactSession = useCallback(async () => {
    const sessionId = sessionIdRef.current;
    if (!sessionId || isDraftSession(sessionId)) return;
    let result;
    try {
      result = await api.writingChat.compactSession(sessionId);
    } catch (e) {
      setError(e.message);
      toast.error(i18n.t("hooks:writingChat.compactFailed"), {
        description: e.message,
      });
      return;
    }
    // 消息过少或压缩失败时后端不改变会话结构，无需二次拉取
    if (!result?.compressed) {
      toast.info(i18n.t("hooks:writingChat.compactTooShort"));
      return;
    }
    try {
      const fresh = await api.writingChat.loadSession(sessionId);
      setWritingChatMessages(processLoadedMessages(fresh.messages || []));
      const hasCompressed = (fresh.messages || []).some(
        (m) => m.compressed && m.role === "summary",
      );
      setWritingChatCompressed(hasCompressed);
      toast.success(i18n.t("hooks:writingChat.compactSuccess"));
    } catch (e) {
      setError(e.message);
      toast.error(i18n.t("hooks:writingChat.compactFailed"), {
        description: e.message,
      });
    }
  }, [setWritingChatMessages, setWritingChatCompressed]);

  const handleFileChange = useCallback(
    (path) => {
      // 选择当前已选中的文件时直接返回，否则 setActiveFile 同值不触发 re-render，
      // 导致后续 setParagraphs([]) 清空段落却不会重新加载
      if (path === activeFile) return;
      setActiveFile(path);
      setParagraphIndex(null);
      setParagraphs([]);
    },
    [setActiveFile, activeFile],
  );

  return {
    // store
    projectPath,
    activeFile,
    texFiles,
    paragraphs,
    paragraphIndex,
    setParagraphIndex,
    sessions: writingChatSessions,
    currentSessionId: writingChatCurrentSessionId,
    isCurrentSessionDraft: isDraftSession(writingChatCurrentSessionId),
    messages: writingChatMessages,
    compressed: writingChatCompressed,
    input,
    setInput,
    streaming,
    loading: writingChatLoading,
    error,
    editingMessageId,
    applyingMessageId,
    toolStatus,
    pendingTool,
    confirmPendingTool,
    cancelPendingTool,
    pendingClarification,
    submitClarification,
    cancelClarification,
    // DDG 验证码
    captchaChallenge,
    captchaSubmitting,
    submitCaptcha,
    dismissCaptcha,
    // 助手
    assistants,
    activeAssistant,
    setAssistantActive,
    saveAssistant,
    deleteAssistant,
    // actions
    sendMessage,
    selectSession,
    newSession,
    deleteSession,
    renameSession,
    generateEdit,
    applyEdit,
    deleteMessage,
    compactSession,
    handleFileChange,
    refreshSessions,
  };
}
