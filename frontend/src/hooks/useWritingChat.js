import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { flattenTexFiles } from "@/hooks/useParagraphCheck";
import {
  useWritingChatSessions,
  useWritingChatAssistants,
  useWritingChatCaptcha,
  createSSEEventHandler,
  isDraftSession,
  uid,
} from "./writing-chat";

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
  const writingChatCurrentSessionId = useStore((s) => s.writingChatCurrentSessionId);
  const writingChatCompressed = useStore((s) => s.writingChatCompressed);
  const setWritingChatSessions = useStore((s) => s.setWritingChatSessions);
  const setWritingChatMessages = useStore((s) => s.setWritingChatMessages);
  const setWritingChatCurrentSessionId = useStore((s) => s.setWritingChatCurrentSessionId);
  const setWritingChatCompressed = useStore((s) => s.setWritingChatCompressed);

  const [paragraphIndex, setParagraphIndex] = useState(null);
  const [paragraphs, setParagraphs] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const [editingMessageId, setEditingMessageId] = useState(null); // generate-edit 进行中
  const [applyingMessageId, setApplyingMessageId] = useState(null);
  // 联网搜索状态
  const [searchStatus, setSearchStatus] = useState(null); // { query, engine } | null
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

  // ── 页面刷新：水合完成后自动加载上次选中的会话 ──────────────────
  const hydrated = useStore((s) => s.hydrated);
  useEffect(() => {
    if (hydrated && writingChatCurrentSessionId && writingChatMessages.length === 0) {
      selectSession(writingChatCurrentSessionId);
    }
  }, [hydrated, writingChatCurrentSessionId, writingChatMessages.length, selectSession]);

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
      const content = (text ?? input).trim();
      if (!content || !activeFile || streaming) return;

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
          referenced_file: activeFile,
          referenced_paragraph: paragraphIndex,
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
        },
      ]);

      const onEvent = createSSEEventHandler({
        tmpAssistantId,
        setWritingChatMessages,
        setSearchStatus,
        setCaptchaChallenge,
        setWritingChatCompressed,
        setWritingChatSessions,
        setWritingChatCurrentSessionId,
        setError,
        writingChatCurrentSessionId,
      });

      try {
        await api.writingChat.send({
          sessionId: isDraftSession(writingChatCurrentSessionId) ? null : writingChatCurrentSessionId,
          filePath: activeFile,
          paragraphIndex: paragraphIndex,
          userMessage: content,
          assistantPrompt: activeAssistant && assistantContent ? assistantContent : null,
          workspaceId,
          onEvent,
        });
      } catch (e) {
        setError(e.message);
        setWritingChatMessages((prev) =>
          prev.filter((m) => m.id !== tmpAssistantId)
        );
        const { translateError } = await import("@/utils/errorMessages");
        const msg = translateError(e.message);
        toast.error(msg, { description: e.message });
      } finally {
        setStreaming(false);
        setLoading("writingChat", false);
        setSearchStatus(null);
        // 后端 send 后可能新建会话或更新现有，刷新 session 列表
        refreshSessions();
      }
    },
    [
      activeFile,
      input,
      paragraphIndex,
      streaming,
      writingChatCurrentSessionId,
      activeAssistant,
      assistantContent,
      setWritingChatMessages,
      setWritingChatCurrentSessionId,
      setLoading,
      refreshSessions,
    ]
  );

  // ── 生成编辑（第二轮） ──────────────────────────────────────────
  const generateEdit = useCallback(
    async (messageId, instruction) => {
      if (!activeFile || !writingChatCurrentSessionId || paragraphIndex == null) {
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        toast.error(i18n.t("hooks:writingChat.selectFileAndParagraph"));
        return null;
      }
      setEditingMessageId(messageId);
      try {
        const data = await api.writingChat.generateEdit({
          session_id: writingChatCurrentSessionId,
          file_path: activeFile,
          paragraph_index: paragraphIndex,
          instruction:
            instruction ||
            "请基于刚才的对话内容修改此段落，使其更符合学术写作规范",
        }, workspaceId);
        // 把 edit_op 附挂到对应 assistant 消息
        setWritingChatMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, edit_op: data.edit_op, edit_reason: data.reason }
              : m
          )
        );
        return data;
      } catch (e) {
        setError(e.message);
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        toast.error(i18n.t("hooks:writingChat.generateEditFailed"), { description: e.message });
        return null;
      } finally {
        setEditingMessageId(null);
      }
    },
    [
      activeFile,
      paragraphIndex,
      writingChatCurrentSessionId,
      setWritingChatMessages,
    ]
  );

  // ── 应用编辑 ────────────────────────────────────────────────────
  const applyEdit = useCallback(
    async (messageId, customText) => {
      if (!writingChatCurrentSessionId) return;
      // 若有自定义文本，先更新消息中的 new_latex
      if (customText) {
        setWritingChatMessages((prev) =>
          prev.map((m) =>
            m.id === messageId && m.edit_op
              ? { ...m, edit_op: { ...m.edit_op, new_latex: customText } }
              : m
          )
        );
      }
      setApplyingMessageId(messageId);
      try {
        // 若有自定义文本（用户手动编辑），将其传递给后端
        const payload = {
          session_id: writingChatCurrentSessionId,
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
              : m
          )
        );
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        toast.success(i18n.t("hooks:writingChat.editApplied"));
      } catch (e) {
        setError(e.message);
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        toast.error(i18n.t("hooks:writingChat.applyEditFailed"), { description: e.message });
      } finally {
        setApplyingMessageId(null);
      }
    },
    [writingChatCurrentSessionId, setWritingChatMessages]
  );

  // ── 删除消息 ────────────────────────────────────────────────────
  const deleteMessage = useCallback(
    async (messageId) => {
      if (!writingChatCurrentSessionId) return;
      try {
        await api.writingChat.deleteMessage({
          session_id: writingChatCurrentSessionId,
          message_id: messageId,
        });
        setWritingChatMessages((prev) => prev.filter((m) => m.id !== messageId));
      } catch (e) {
        setError(e.message);
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        toast.error(i18n.t("hooks:writingChat.deleteMessageFailed"), { description: e.message });
      }
    },
    [writingChatCurrentSessionId, setWritingChatMessages]
  );

  // ── 撤回消息（summary 消息会还原快照） ────────────────────────
  const undoMessage = useCallback(
    async (messageId) => {
      if (!writingChatCurrentSessionId) return;
      try {
        const result = await api.writingChat.undoMessage({
          session_id: writingChatCurrentSessionId,
          message_id: messageId,
        });
        // 重新拉取会话完整数据以同步 snapshot 还原结构
        const fresh = await api.writingChat.loadSession(writingChatCurrentSessionId);
        setWritingChatMessages(fresh.messages || []);
        const hasCompressed = (fresh.messages || []).some(
          (m) => m.compressed && m.role === "summary"
        );
        setWritingChatCompressed(hasCompressed);
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        if (result?.restored) {
          toast.success(i18n.t("hooks:writingChat.undoCompressedSuccess"));
        } else if (result?.removed) {
          toast.success(i18n.t("hooks:writingChat.undoSuccess"));
        }
      } catch (e) {
        setError(e.message);
        const { toast } = await import("sonner");
        const { default: i18n } = await import("@/i18n");
        toast.error(i18n.t("hooks:writingChat.undoFailed"), { description: e.message });
      }
    },
    [writingChatCurrentSessionId, setWritingChatMessages, setWritingChatCompressed]
  );

  const handleFileChange = useCallback(
    (path) => {
      // 选择当前已选中的文件时直接返回，否则 setActiveFile 同值不触发 re-render，
      // 导致后续 setParagraphs([]) 清空段落却不会重新加载
      if (path === activeFile) return;
      setActiveFile(path);
      setParagraphIndex(null);
      setParagraphs([]);
    },
    [setActiveFile, activeFile]
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
    messages: writingChatMessages,
    compressed: writingChatCompressed,
    input,
    setInput,
    streaming,
    loading: writingChatLoading,
    error,
    editingMessageId,
    applyingMessageId,
    searchStatus,
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
    undoMessage,
    handleFileChange,
    refreshSessions,
  };
}
