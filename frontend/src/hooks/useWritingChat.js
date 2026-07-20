import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import { flattenTexFiles } from "@/hooks/useParagraphCheck";
import { translateError } from "@/utils/errorMessages";

const uid = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36));

const DRAFT_PREFIX = "__draft__";
const isDraftSession = (id) => typeof id === "string" && id.startsWith(DRAFT_PREFIX);

function parseThinking(rawText) {
  const thinkOpen = rawText.indexOf("<thinking>");
  if (thinkOpen === -1) {
    return { thinking: "", display: rawText, thinkingOpen: false, thinkingClosed: true };
  }
  const thinkClose = rawText.indexOf("</thinking>", thinkOpen + 10);
  const thinking = rawText.slice(
    thinkOpen + 10,
    thinkClose !== -1 ? thinkClose : undefined
  );
  const beforeThink = rawText.slice(0, thinkOpen);
  const afterThink = thinkClose !== -1 ? rawText.slice(thinkClose + 11) : "";
  const display = (beforeThink + afterThink).trim();
  return {
    thinking,
    display,
    thinkingOpen: true,
    thinkingClosed: thinkClose !== -1,
  };
}

/** Process loaded messages to extract thinking content from raw content field */
function processLoadedMessages(messages) {
  return messages.map((msg) => {
    if (msg.role !== "assistant" || !msg.content) return msg;
    if (!/<thinking>[\s\S]*?<\/thinking>/.test(msg.content)) return msg;
    // Already has parsed thinking fields
    if (msg.thinkingContent !== undefined) return msg;
    const parsed = parseThinking(msg.content);
    return {
      ...msg,
      thinkingContent: parsed.thinking,
      displayContent: parsed.display,
      thinkingOpen: false,
      thinkingClosed: parsed.thinkingClosed,
    };
  });
}

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
  // 助手（提示词）状态
  const [assistants, setAssistants] = useState([]);
  const [activeAssistant, setActiveAssistant] = useState("");
  const [assistantContent, setAssistantContent] = useState(null);
  const restoringRef = useRef(false);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

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

  // ── 首次挂载：加载会话列表 ───────────────────────────────────────
  const refreshSessions = useCallback(async () => {
    try {
      const data = await api.writingChat.listSessions(workspaceId);
      setWritingChatSessions(data.sessions || []);
    } catch (e) {
      setError(e.message);
    }
  }, [workspaceId, setWritingChatSessions]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // ── 加载助手列表 ─────────────────────────────────────────────────
  const refreshAssistants = useCallback(async () => {
    try {
      const data = await api.writingChat.assistants.list();
      setAssistants(data.prompts || []);
      const active = (data.prompts || []).find((p) => p.active);
      if (active) {
        setActiveAssistant(active.name);
        try {
          const d = await api.writingChat.assistants.load(active.name);
          setAssistantContent(d.content || "");
        } catch { setAssistantContent(""); }
      } else {
        setActiveAssistant("");
        setAssistantContent(null);
      }
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    refreshAssistants();
  }, [refreshAssistants]);

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

  // ── 页面刷新：自动加载上次选中的会话 ──────────────────────────
  useEffect(() => {
    if (writingChatCurrentSessionId && writingChatMessages.length === 0) {
      selectSession(writingChatCurrentSessionId);
    }
  }, []); // 仅挂载时执行

  // ── 切换会话 ─────────────────────────────────────────────────────
  const selectSession = useCallback(
    async (sessionId) => {
      if (!sessionId) {
        setWritingChatCurrentSessionId(null);
        setWritingChatMessages([]);
        setWritingChatCompressed(false);
        return;
      }
      if (isDraftSession(sessionId)) {
        setWritingChatCurrentSessionId(sessionId);
        setWritingChatMessages([]);
        setWritingChatCompressed(false);
        return;
      }
      try {
        const data = await api.writingChat.loadSession(sessionId);
        setWritingChatCurrentSessionId(sessionId);
        setWritingChatMessages(processLoadedMessages(data.messages || []));
        const hasCompressed = (data.messages || []).some(
          (m) => m.compressed && m.role === "summary"
        );
        setWritingChatCompressed(hasCompressed);
        if (data.file_path && data.file_path !== activeFile) {
          setActiveFile(data.file_path);
        }
      } catch (e) {
        setError(e.message);
        toast.error("加载会话失败", { description: e.message });
      }
    },
    [
      activeFile,
      setActiveFile,
      setWritingChatCurrentSessionId,
      setWritingChatMessages,
      setWritingChatCompressed,
    ]
  );

  // ── 新建会话 ─────────────────────────────────────────────────────
  const newSession = useCallback(() => {
    const existingDraft = writingChatSessions.find((s) => isDraftSession(s.id));
    if (existingDraft) {
      setWritingChatCurrentSessionId(existingDraft.id);
      setWritingChatMessages([]);
      setWritingChatCompressed(false);
      return;
    }
    const draftId = DRAFT_PREFIX + uid();
    const draftSession = { id: draftId, title: "" };
    setWritingChatSessions([draftSession, ...writingChatSessions]);
    setWritingChatCurrentSessionId(draftId);
    setWritingChatMessages([]);
    setWritingChatCompressed(false);
  }, [
    writingChatSessions,
    setWritingChatSessions,
    setWritingChatCurrentSessionId,
    setWritingChatMessages,
    setWritingChatCompressed,
  ]);

  // ── 删除会话 ─────────────────────────────────────────────────────
  const deleteSession = useCallback(
    async (sessionId) => {
      try {
        if (isDraftSession(sessionId)) {
          setWritingChatSessions((prev) => prev.filter((s) => s.id !== sessionId));
          if (writingChatCurrentSessionId === sessionId) {
            setWritingChatCurrentSessionId(null);
            setWritingChatMessages([]);
            setWritingChatCompressed(false);
          }
          return;
        }
        await api.writingChat.deleteSession(sessionId);
        if (writingChatCurrentSessionId === sessionId) {
          newSession();
        }
        await refreshSessions();
      } catch (e) {
        setError(e.message);
        toast.error("删除会话失败", { description: e.message });
      }
    },
    [writingChatCurrentSessionId, newSession, refreshSessions, setWritingChatSessions, setWritingChatCurrentSessionId, setWritingChatMessages, setWritingChatCompressed]
  );

  const renameSession = useCallback(
    async (sessionId, title) => {
      try {
        if (isDraftSession(sessionId)) {
          setWritingChatSessions((prev) =>
            prev.map((s) => (s.id === sessionId ? { ...s, title } : s))
          );
          return;
        }
        await api.writingChat.renameSession(sessionId, title);
        await refreshSessions();
      } catch (e) {
        toast.error("重命名会话失败", { description: e.message });
      }
    },
    [refreshSessions, setWritingChatSessions]
  );

  // ── 助手提示词管理 ─────────────────────────────────────────────────
  const setAssistantActive = useCallback(
    async (name) => {
      if (name === activeAssistant) {
        // 取消激活
        try { await api.writingChat.assistants.setActive(""); } catch {}
        setActiveAssistant("");
        setAssistantContent(null);
        return;
      }
      try {
        await api.writingChat.assistants.setActive(name);
        setActiveAssistant(name);
        const d = await api.writingChat.assistants.load(name);
        setAssistantContent(d.content || "");
      } catch (e) {
        toast.error("切换助手失败", { description: e.message });
      }
    },
    [activeAssistant]
  );

  const saveAssistant = useCallback(
    async (name, content) => {
      if (!name?.trim() || !content?.trim()) return;
      try {
        await api.writingChat.assistants.save(name, content);
        await refreshAssistants();
        toast.success("助手已保存");
      } catch (e) {
        toast.error("保存助手失败", { description: e.message });
      }
    },
    [refreshAssistants]
  );

  const deleteAssistant = useCallback(
    async (name) => {
      try {
        await api.writingChat.assistants.delete(name);
        if (activeAssistant === name) {
          setActiveAssistant("");
          setAssistantContent(null);
        }
        await refreshAssistants();
      } catch (e) {
        toast.error("删除助手失败", { description: e.message });
      }
    },
    [activeAssistant, refreshAssistants]
  );

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

      const onEvent = (event) => {
        if (event.type === "token") {
          setWritingChatMessages((prev) =>
            prev.map((m) => {
              if (m.id !== tmpAssistantId) return m;
              const raw = (m.rawContent || "") + event.content;
              const parsed = parseThinking(raw);
              return {
                ...m,
                rawContent: raw,
                content: raw,
                thinkingContent: parsed.thinking,
                displayContent: parsed.display,
                thinkingOpen: parsed.thinkingOpen,
                thinkingClosed: parsed.thinkingClosed,
              };
            })
          );
        } else if (event.type === "compressed") {
          setWritingChatCompressed(true);
          toast.info("会话已压缩", {
            description: "上下文累计 token 超阈值，已生成历史摘要",
          });
        } else if (event.type === "done") {
          setWritingChatMessages((prev) =>
            prev.map((m) =>
              m.id === tmpAssistantId
                ? {
                    ...m,
                    id: event.message_id || m.id,
                    streaming: false,
                    tokens: event.tokens || 0,
                  }
                : m
            )
          );
          if (event.session_id) {
            if (isDraftSession(writingChatCurrentSessionId)) {
              setWritingChatSessions((prev) =>
                prev.map((s) =>
                  s.id === writingChatCurrentSessionId
                    ? { id: event.session_id, title: s.title }
                    : s
                )
              );
            }
            if (event.session_id !== writingChatCurrentSessionId) {
              setWritingChatCurrentSessionId(event.session_id);
            }
          }
        } else if (event.type === "error") {
          const msg = translateError(event.message);
          setError(msg);
          // 移除占位的 assistant 消息
          setWritingChatMessages((prev) =>
            prev.filter((m) => m.id !== tmpAssistantId)
          );
          toast.error("对话出错", { description: msg });
        }
      };

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
        toast.error("发送失败", { description: e.message });
      } finally {
        setStreaming(false);
        setLoading("writingChat", false);
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
        toast.error("请先选择文件和段落");
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
        toast.error("生成编辑失败", { description: e.message });
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
        toast.success("已应用编辑");
      } catch (e) {
        setError(e.message);
        toast.error("应用编辑失败", { description: e.message });
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
        toast.error("删除消息失败", { description: e.message });
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
        if (result?.restored) {
          toast.success("已撤回压缩消息，原消息已还原");
        } else if (result?.removed) {
          toast.success("已撤回消息");
        }
      } catch (e) {
        setError(e.message);
        toast.error("撤回消息失败", { description: e.message });
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