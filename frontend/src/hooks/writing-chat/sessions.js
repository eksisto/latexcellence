/**
 * Writing Chat 会话管理逻辑。
 */

import { useCallback, useEffect } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import i18n from "@/i18n";
import { isDraftSession, processLoadedMessages, uid, DRAFT_PREFIX } from "./utils";

/**
 * 创建会话管理相关的 hooks 和回调。
 */
export function useWritingChatSessions({
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
}) {
  // ── 首次挂载：加载会话列表 ───────────────────────────────────────
  const refreshSessions = useCallback(async () => {
    try {
      const data = await api.writingChat.listSessions(workspaceId);
      setWritingChatSessions(data.sessions || []);
    } catch (e) {
      // Error handled by parent
    }
  }, [workspaceId, setWritingChatSessions]);

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
        toast.error(i18n.t("hooks:writingChat.loadSessionFailed"), { description: e.message });
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
        toast.error(i18n.t("hooks:writingChat.deleteSessionFailed"), { description: e.message });
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
        toast.error(i18n.t("hooks:writingChat.renameSessionFailed"), { description: e.message });
      }
    },
    [refreshSessions, setWritingChatSessions]
  );

  return {
    refreshSessions,
    selectSession,
    newSession,
    deleteSession,
    renameSession,
  };
}
