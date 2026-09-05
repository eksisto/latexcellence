import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";

import useStore from "@/store/useStore";
import useEditorStore from "@/store/editorStore";
import { api } from "@/api/index";
import i18n from "@/i18n";
import { translateError } from "@/utils/errorMessages";
import {
  createStreamingPlaceholder,
  isStaleEvent,
  reduceAssistantCaptcha,
  reduceAssistantMessages,
  reduceAssistantSessionId,
  reduceAssistantToolStatus,
} from "@/lib/editor/assistantStream";
import { useWritingChatCaptcha } from "@/hooks/writing-chat/captcha";

/**
 * 编辑器 AI 助手 hook（editor-design §7，阶段四）。
 *
 * 职责：
 *   - 项目切换守卫：assistantProjectKey 与当前项目不一致时清空本地视图
 *     （后端按项目 scope 隔离会话，本地只跟随）；切换时同步使所有在途流
 *     过期并经 AbortController 中止在途请求，组件卸载时同样中止；
 *   - 发送消息：读取编辑器上下文（文件/缓冲区/光标/选区）经 SSE 流式对话；
 *   - SSE 事件归约：事件 → 状态的纯归约收敛在 lib/editor/assistantStream.js
 *     （token / round_start / tool_status / tool_result / compressed /
 *     done（含结构化建议与 captcha_pending 终流形态）/ captcha_challenge /
 *     error），本 hook 只负责把归约结果写回 store，以及 toast / 中止 /
 *     过期计数等副作用编排；
 *   - DDG 挑战（阶段五a）：captcha_challenge 事件写入 useWritingChatCaptcha
 *     通道，挑战在助手侧栏内提交/放弃；captcha done 删占位不留幽灵消息，
 *     会话 id 照常回写（user 消息已在服务端落盘）。
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

  // sessionId 无消费方（AssistantPanel 不解构），刻意不做组件级订阅：
  // 会话建立/清空不再白付一次面板重渲染；需要时经
  // useEditorStore.getState().assistantSessionId 现取（rerender-defer-reads）
  const messages = useEditorStore((s) => s.assistantMessages);
  const streaming = useEditorStore((s) => s.assistantStreaming);
  const toolStatus = useEditorStore((s) => s.assistantToolStatus);
  const ensureAssistantProject = useEditorStore(
    (s) => s.ensureAssistantProject,
  );

  /** 在途请求序号：丢弃过期请求的迟到事件 */
  const seqRef = useRef(0);
  /** 当前在途请求的 AbortController（项目切换/卸载时 abort，风格对齐
   * useParagraphCheck 的 EventSource close() 先例） */
  const abortRef = useRef(null);

  // DDG 挑战通道（阶段五a）：复用 Writing Chat 的 UI 无关 hook（内部调
  // /api/search/ddg-challenge 提交端点）。hook 的 setError 副作用在本侧
  // 无对应错误横幅——提交失败已由 hook 内 toast 呈现，传空实现即可。
  const {
    captchaChallenge,
    setCaptchaChallenge,
    captchaSubmitting,
    submitCaptcha,
    dismissCaptcha,
  } = useWritingChatCaptcha({ setError: () => {} });

  // 项目键：单用户看 projectPath，多用户看 projectId（useIsOpened 同一判定口径）
  useEffect(() => {
    // 项目切换守卫第一步：同步使所有在途流过期并中止在途请求，
    // 迟到的 token/error 不再落入新项目的会话视图
    seqRef.current += 1;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    ensureAssistantProject(projectPath || projectId || null);
  }, [projectPath, projectId, ensureAssistantProject]);

  // 组件卸载：使在途流过期并中止请求（AbortError 由 sendMessage 静默吞掉）
  useEffect(() => {
    return () => {
      seqRef.current += 1;
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, []);

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
      // 占位消息携带发送时的来源 filePath（Q17 tab 守卫）：done 落位保留
      // 该字段，建议卡片据此在活跃 tab 切换后禁用跨文件应用
      store.appendAssistantMessage({
        ...createStreamingPlaceholder(placeholderId),
        filePath: ctx.filePath ?? null,
      });
      store.setAssistantStreaming(true);
      store.setAssistantToolStatus(null);

      const seq = ++seqRef.current;
      // 每次发送持有新的 AbortController：项目切换/卸载时经 ref 中止在途流
      const controller = new AbortController();
      abortRef.current = controller;
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
            // 过期请求的迟到事件一律丢弃（含 error：新鲜事件的 seq 本就匹配，
            // 过期请求的失败提示对当前会话已无意义）
            if (isStaleEvent(seq, seqRef.current)) return;
            handleEvent(event);
          },
          signal: controller.signal,
        });
      } catch (e) {
        if (e?.name !== "AbortError") {
          const msg = e?.code
            ? translateError({ error: e.code, message: e.message })
            : translateError(e?.message);
          toast.error(i18n.t("components:assistant.chatError"), {
            description: msg,
          });
          useEditorStore.getState().removeAssistantMessage(placeholderId);
        }
        // AbortError：主动取消（项目切换/组件卸载），静默吞掉，由 finally 收尾
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
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

      /**
       * 把事件归约出的新消息数组写回 store（引用未变——无关事件或占位
       * 已不存在——则不产生写入，与原逐字段 patch 的写入次数一致）。
       */
      function commitMessages(event) {
        const { assistantMessages } = useEditorStore.getState();
        const next = reduceAssistantMessages(assistantMessages, event, {
          placeholderId,
        });
        if (next !== assistantMessages) {
          useEditorStore.setState({ assistantMessages: next });
        }
      }

      function handleEvent(event) {
        const st = useEditorStore.getState();
        switch (event.type) {
          case "token":
            commitMessages(event);
            break;
          case "round_start":
            commitMessages(event);
            break;
          case "tool_status":
          case "tool_result":
            // 工具指示器是独立于消息列表的 store 字段（不在占位消息上）
            st.setAssistantToolStatus(
              reduceAssistantToolStatus(st.assistantToolStatus, event),
            );
            break;
          case "captcha_challenge":
            // DDG 挑战接管的流暂停态：清工具指示器，挑战对象归一化后
            // 写入 captcha 通道（由 AssistantPanel 渲染挑战卡）
            st.setAssistantToolStatus(
              reduceAssistantToolStatus(st.assistantToolStatus, event),
            );
            setCaptchaChallenge(reduceAssistantCaptcha(null, event));
            break;
          case "compressed":
            toast.info(i18n.t("components:assistant.compressed"));
            break;
          case "done": {
            st.setAssistantToolStatus(
              reduceAssistantToolStatus(st.assistantToolStatus, event),
            );
            commitMessages(event);
            // patch 用旧 id 定位；替换服务端 id 后同步会话 id
            const nextSessionId = reduceAssistantSessionId(
              st.assistantSessionId,
              event,
            );
            if (nextSessionId !== st.assistantSessionId) {
              st.setAssistantSessionId(nextSessionId);
            }
            break;
          }
          case "error": {
            const msg = translateError(event.message);
            toast.error(i18n.t("components:assistant.chatError"), {
              description: msg,
            });
            commitMessages(event);
            break;
          }
          default:
            break;
        }
      }
    },
    [getEditorContext, workspaceId, setCaptchaChallenge],
  );

  /** 用户点击建议应用成功后：逐条乐观标记 + 上报后端回写权威状态
   * （编排细节收敛在 editorStore.markSuggestionApplied） */
  const reportSuggestionApplied = useCallback(
    async (messageId, index) => {
      await useEditorStore
        .getState()
        .markSuggestionApplied(messageId, index, { workspaceId });
    },
    [workspaceId],
  );

  const clearConversation = useCallback(() => {
    useEditorStore.getState().clearAssistantConversation();
  }, []);

  return {
    messages,
    streaming,
    toolStatus,
    captchaChallenge,
    captchaSubmitting,
    submitCaptcha,
    dismissCaptcha,
    sendMessage,
    reportSuggestionApplied,
    clearConversation,
  };
}
