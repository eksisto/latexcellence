import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";

import useStore from "@/store/useStore";
import useEditorStore from "@/store/editorStore";
import { api } from "@/api/index";
import i18n from "@/i18n";
import { translateError } from "@/utils/errorMessages";

/**
 * 编辑器 AI 助手 hook（editor-plan §7，阶段四）。
 *
 * 职责：
 *   - 项目切换守卫：assistantProjectKey 与当前项目不一致时清空本地视图
 *     （后端按项目 scope 隔离会话，本地只跟随）；
 *   - 发送消息：读取编辑器上下文（文件/缓冲区/光标/选区）经 SSE 流式对话；
 *   - SSE 事件归约：token / round_start / tool_status / compressed /
 *     done（含结构化建议）/ error。
 *
 * 建议应用动作由 AssistantPanel 经 EditorPage 提供的缓冲区回调执行，
 * 本 hook 只负责对话流与应用标记上报。
 */

/** 生成临时 assistant 占位消息 id（done 后替换为服务端 message_id） */
function tempId() {
  return `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function useEditorAssistant({ getEditorContext }) {
  const workspaceId = useStore((s) => s.workspaceId);
  const projectPath = useStore((s) => s.projectPath);
  const projectId = useStore((s) => s.projectId);

  const sessionId = useEditorStore((s) => s.assistantSessionId);
  const messages = useEditorStore((s) => s.assistantMessages);
  const streaming = useEditorStore((s) => s.assistantStreaming);
  const toolStatus = useEditorStore((s) => s.assistantToolStatus);
  const ensureAssistantProject = useEditorStore(
    (s) => s.ensureAssistantProject,
  );

  /** 在途请求序号：丢弃过期请求的迟到事件 */
  const seqRef = useRef(0);

  // 项目键：单用户看 projectPath，多用户看 projectId（useIsOpened 同一判定口径）
  useEffect(() => {
    ensureAssistantProject(projectPath || projectId || null);
  }, [projectPath, projectId, ensureAssistantProject]);

  const sendMessage = useCallback(
    async (text, { includeFullFile = false } = {}) => {
      const trimmed = (text || "").trim();
      if (!trimmed) return;
      const store = useEditorStore.getState();
      if (store.assistantStreaming) return;

      const ctx = getEditorContext ? getEditorContext() : {};
      const userMessage = {
        id: tempId(),
        role: "user",
        content: trimmed,
        filePath: ctx.filePath ?? null,
      };
      const placeholderId = tempId();
      store.appendAssistantMessage(userMessage);
      store.appendAssistantMessage({
        id: placeholderId,
        role: "assistant",
        content: "",
        streaming: true,
        suggestions: [],
        suggestionApplied: false,
        appliedSuggestions: {},
      });
      store.setAssistantStreaming(true);
      store.setAssistantToolStatus(null);

      const patchPlaceholder = (patch) =>
        useEditorStore.getState().patchAssistantMessage(placeholderId, patch);
      const appendToken = (content) => {
        const cur = useEditorStore
          .getState()
          .assistantMessages.find((m) => m.id === placeholderId);
        if (cur) {
          patchPlaceholder({ content: (cur.content || "") + content });
        }
      };

      const seq = ++seqRef.current;
      try {
        await api.writingChat.editorSend({
          sessionId: store.assistantSessionId,
          filePath: ctx.filePath ?? "",
          buffer: ctx.buffer ?? "",
          cursorLine: ctx.cursorLine ?? null,
          selection: ctx.selection ?? "",
          includeFullFile,
          userMessage: trimmed,
          workspaceId,
          onEvent: (event) => {
            // 过期请求的迟到事件直接丢弃
            if (seq !== seqRef.current && event.type !== "error") return;
            handleEvent(event);
          },
        });
      } catch (e) {
        const msg = e?.code
          ? translateError({ error: e.code, message: e.message })
          : translateError(e?.message);
        toast.error(i18n.t("components:assistant.chatError"), {
          description: msg,
        });
        useEditorStore.getState().removeAssistantMessage(placeholderId);
      } finally {
        const latest = useEditorStore.getState();
        latest.setAssistantStreaming(false);
        latest.setAssistantToolStatus(null);
        // 占位消息若未被 done 替换（异常中断），移除避免"幽灵"回复
        if (
          useEditorStore
            .getState()
            .assistantMessages.some(
              (m) => m.id === placeholderId && m.streaming,
            )
        ) {
          useEditorStore.getState().removeAssistantMessage(placeholderId);
        }
      }

      function handleEvent(event) {
        const st = useEditorStore.getState();
        switch (event.type) {
          case "token":
            appendToken(event.content);
            break;
          case "round_start":
            // 新一轮工具轮次：重置占位消息内容，与后端落盘内容保持一致
            patchPlaceholder({ content: "" });
            break;
          case "tool_status":
            st.setAssistantToolStatus(event);
            break;
          case "tool_result":
            st.setAssistantToolStatus(null);
            break;
          case "compressed":
            toast.info(i18n.t("components:assistant.compressed"));
            break;
          case "done": {
            st.setAssistantToolStatus(null);
            patchPlaceholder({
              id: event.message_id || placeholderId,
              streaming: false,
              suggestions: Array.isArray(event.suggestions)
                ? event.suggestions
                : [],
              tokens: event.tokens || 0,
            });
            // patch 用旧 id 定位；替换服务端 id 后同步会话 id
            if (event.session_id) {
              st.setAssistantSessionId(event.session_id);
            }
            break;
          }
          case "error": {
            const msg = translateError(event.message);
            toast.error(i18n.t("components:assistant.chatError"), {
              description: msg,
            });
            st.removeAssistantMessage(placeholderId);
            break;
          }
          default:
            break;
        }
      }
    },
    [getEditorContext, workspaceId],
  );

  /** 用户点击建议应用成功后：前端标记 + 上报消息级应用标记 */
  const reportSuggestionApplied = useCallback(
    async (messageId, index) => {
      useEditorStore.getState().markSuggestionApplied(messageId, index);
      const sid = useEditorStore.getState().assistantSessionId;
      if (!sid || !messageId || messageId.startsWith("tmp-")) return;
      try {
        await api.writingChat.applyEditorSuggestion(
          sid,
          messageId,
          workspaceId,
        );
      } catch (_) {
        // 标记上报失败不影响已应用的缓冲区修改
      }
    },
    [workspaceId],
  );

  const clearConversation = useCallback(() => {
    useEditorStore.getState().clearAssistantConversation();
  }, []);

  return {
    sessionId,
    messages,
    streaming,
    toolStatus,
    sendMessage,
    reportSuggestionApplied,
    clearConversation,
  };
}
