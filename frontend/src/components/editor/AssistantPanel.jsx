/**
 * AssistantPanel —— 编辑器 AI 助手侧栏（editor-design §7，阶段四；
 * 阶段五a 接入联网/学术检索工具与 DDG 挑战侧栏内完成）。
 *
 * Copilot 式右栏面板：SSE 流式对话（复用 Writing Chat 会话持久化与
 * 只读项目工具/联网搜索/学术检索），三种建议应用形态齐全（§7.2 / 决策 Q23）：
 *   1. 插入光标处 —— snippet 建议一键插入（纯前端 CM6 transaction）；
 *   2. 替换选区   —— 划词改写场景替换当前选区；
 *   3. diff 卡片   —— unified diff 预览逐卡审阅、一键应用到缓冲区。
 *
 * 应用动作只改缓冲区并置脏标记，是否保存始终由用户决定；viewer 可
 * 问答但应用按钮禁用。建议不经后端落盘，落盘仍走 save-file 单一路径。
 * DDG 挑战（阶段五a）：后端 captcha_challenge 事件终流后弹共享
 * CaptchaDialog，挑战在面板提交/放弃，用户随后手动重发原提问。
 */

import {
  lazy,
  memo,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import Bot from "lucide-react/dist/esm/icons/bot.mjs";
import Check from "lucide-react/dist/esm/icons/check.mjs";
import FileDiff from "lucide-react/dist/esm/icons/file-diff.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import SendHorizontal from "lucide-react/dist/esm/icons/send-horizontal.mjs";
import SquareDashedMousePointer from "lucide-react/dist/esm/icons/square-dashed-mouse-pointer.mjs";
import TextCursorInput from "lucide-react/dist/esm/icons/text-cursor-input.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import Wrench from "lucide-react/dist/esm/icons/wrench.mjs";

import useEditorStore, { activeBuffer } from "@/store/editorStore";
import useEditorAssistant from "@/hooks/useEditorAssistant";
import CaptchaDialog from "@/components/CaptchaDialog";
import { applyUnifiedDiff, parseUnifiedDiff } from "@/lib/editor/applyDiff";

/**
 * 单条建议的操作卡（snippet 两键 / diff 审阅卡，EditOpCard 模式）。
 * props 均为原始值 + 稳定回调（onApply 经 useCallback 固定），配合
 * React.memo：单卡展开态切换不再牵连同消息其余卡片重渲染。
 *
 * tab 守卫（editor-productivity-design Q17）：卡片携带发送时的来源
 * filePath（sourceFilePath），活跃 tab 不匹配时禁用应用并提示切换，
 * 不自动切 tab。
 */
const SuggestionCard = memo(function SuggestionCard({
  messageId,
  suggestion,
  applied,
  readOnly,
  hasSelection,
  mismatchedPath,
  onApply,
}) {
  const { t } = useTranslation("components");
  const [expanded, setExpanded] = useState(false);
  const isDiff = suggestion.kind === "diff";
  const guardBlocked = Boolean(mismatchedPath);
  // diff 解析随内容记忆化：父级因流式 token / 输入重渲染时不重复解析
  const parsed = useMemo(
    () => (isDiff ? parseUnifiedDiff(suggestion.content) : null),
    [isDiff, suggestion.content],
  );

  const guardHint = guardBlocked ? (
    <span className="text-[10px] text-amber-600 dark:text-amber-400">
      {t("assistant.fileMismatchHint", { file: mismatchedPath })}
    </span>
  ) : null;

  if (isDiff) {
    return (
      <div className="rounded-md border border-blue-500/30 bg-blue-500/5 p-2 text-xs">
        <button
          type="button"
          className="flex w-full items-center gap-1.5 text-left font-medium"
          onClick={() => setExpanded((v) => !v)}
        >
          <FileDiff className="size-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">
            {t("assistant.diffCard", {
              additions: parsed?.ok ? parsed.additions : 0,
              deletions: parsed?.ok ? parsed.deletions : 0,
            })}
          </span>
        </button>
        {expanded && (
          <pre className="mt-1.5 max-h-40 overflow-auto rounded bg-muted/60 p-2 font-mono text-[11px] leading-relaxed">
            {suggestion.content}
          </pre>
        )}
        <div className="mt-1.5 flex items-center justify-between gap-2">
          {!applied ? (
            <Button
              size="xs"
              variant="outline"
              onClick={() => onApply(messageId, suggestion, "diff")}
              disabled={readOnly || guardBlocked}
              title={
                guardBlocked
                  ? t("assistant.fileMismatchHint", { file: mismatchedPath })
                  : undefined
              }
            >
              <Check />
              {t("assistant.applyToBuffer")}
            </Button>
          ) : (
            <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
              <Check className="size-3.5" />
              {t("assistant.applied")}
            </span>
          )}
          {guardHint ?? (
            <span className="text-[10px] text-muted-foreground">
              {t("assistant.applyBufferHint")}
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-blue-500/30 bg-blue-500/5 p-2 text-xs">
      <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed">
        {suggestion.content}
      </pre>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {applied ? (
          <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
            <Check className="size-3.5" />
            {t("assistant.applied")}
          </span>
        ) : (
          <>
            <Button
              size="xs"
              variant="outline"
              onClick={() => onApply(messageId, suggestion, "insert")}
              disabled={readOnly || guardBlocked}
              title={
                guardBlocked
                  ? t("assistant.fileMismatchHint", { file: mismatchedPath })
                  : undefined
              }
            >
              <TextCursorInput />
              {t("assistant.insertAtCursor")}
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => onApply(messageId, suggestion, "replace")}
              disabled={readOnly || guardBlocked || !hasSelection}
              title={
                guardBlocked
                  ? t("assistant.fileMismatchHint", { file: mismatchedPath })
                  : hasSelection
                    ? undefined
                    : t("assistant.noSelectionHint")
              }
            >
              <SquareDashedMousePointer />
              {t("assistant.replaceSelection")}
            </Button>
          </>
        )}
      </div>
      {guardHint && <div className="mt-1">{guardHint}</div>}
    </div>
  );
});

// bundle-dynamic-imports：react-markdown + katex 系列较重，且仅在
// 面板渲染出首条 Markdown 消息时才真正需要，改为按需加载
const LazyMarkdownRenderer = lazy(
  () => import("@/components/MarkdownRenderer"),
);

/**
 * 消息正文渲染：useDeferredValue 把流式 token 驱动的 Markdown 重解析
 * 降为低优先级（React 可跳过中间态直接渲染较新内容），输入框与滚动
 * 保持跟手；memo 使已完成消息在面板重渲染时彻底跳过。对齐 writing-chat
 * MessageBubble 的既有先例。
 */
const MessageMarkdown = memo(function MessageMarkdown({ content }) {
  const deferredContent = useDeferredValue(content);
  return (
    <Suspense fallback={<div className="min-h-[1em]" aria-hidden />}>
      <LazyMarkdownRenderer>{deferredContent}</LazyMarkdownRenderer>
    </Suspense>
  );
});

function AssistantPanel({
  readOnly = false,
  getEditorContext,
  onApplySnippet,
  onApplyDiffText,
  pollSelection = true,
}) {
  const { t } = useTranslation("components");
  const [input, setInput] = useState("");
  const [includeFullFile, setIncludeFullFile] = useState(false);
  /** 当前选区状态（用于启用/禁用"替换选区"按钮） */
  const [hasSelection, setHasSelection] = useState(false);
  const listRef = useRef(null);

  // 激活 tab 路径（原始值订阅）：建议卡 tab 守卫与左下角上下文展示共用
  const activeTabPath = useEditorStore((s) => s.activeTabPath);
  const {
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
  } = useEditorAssistant({ getEditorContext });

  /** 稳定引用：轮询 effect 经 ref 读取最新回调，不因内联函数重建重启 */
  const getCtxRef = useRef(getEditorContext);
  getCtxRef.current = getEditorContext;

  // 大文件判定与后端 SMALL_FILE_LINE_THRESHOLD 对齐：超过时显示"纳入全文"开关。
  // A2 渲染解耦：刻意不订阅 content——按键级订阅会让面板随每次键入重渲染
  // 并对大缓冲全文 split。行数是信息性指标（仅控制开关显隐），按文件切换
  // 与消息数变化时重算即可；发送时刻的实时 buffer 由 getEditorContext 现取。
  const bufferLineCount = useMemo(() => {
    const c = activeBuffer(useEditorStore.getState());
    return c ? c.split("\n").length : 0;
  }, [activeTabPath, messages.length]);
  const largeBuffer = bufferLineCount >= 400;

  // 选区状态跟随编辑器上下文快照（发送时读取实时值，按钮态低频刷新）。
  // pollSelection=false（面板折叠 / 助手 Tab 未激活）时暂停轮询，避免
  // keepMounted 下隐藏态空转；恢复时立即对齐一次，按钮态不滞留旧值
  useEffect(() => {
    if (!pollSelection) return undefined;
    const poll = () => {
      const ctx = getCtxRef.current ? getCtxRef.current() : null;
      setHasSelection(Boolean(ctx?.selection));
    };
    poll();
    const id = window.setInterval(poll, 800);
    return () => window.clearInterval(id);
  }, [pollSelection]);

  // 新消息自动滚动到底部
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, streaming, toolStatus]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    await sendMessage(text, { includeFullFile });
  };

  /**
   * 统一建议应用出口：snippet 按 mode 插入光标处 / 替换选区，diff 走
   * 整段应用回调；应用成功后逐条标记并上报后端回写权威状态。
   * useCallback 固定身份供 SuggestionCard 的 memo 生效。
   */
  const handleApply = useCallback(
    async (messageId, suggestion, mode) => {
      if (readOnly) return;
      try {
        let ok = false;
        if (suggestion.kind === "diff") {
          ok = onApplyDiffText(suggestion.content);
        } else {
          ok = onApplySnippet(suggestion.content, {
            replace: mode === "replace",
          });
        }
        if (!ok) return;
        await reportSuggestionApplied(messageId, suggestion.index);
      } catch (e) {
        toast.error(t("assistant.applyFailed"), {
          description: String(e?.message || e),
        });
      }
    },
    [readOnly, onApplyDiffText, onApplySnippet, reportSuggestionApplied, t],
  );

  return (
    <Card className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="inline-flex items-center gap-1.5 text-sm font-semibold">
          <Bot className="size-4" />
          {t("assistant.title")}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={clearConversation}
          disabled={streaming || messages.length === 0}
          title={t("assistant.clear")}
        >
          <Trash2 />
          <span className="sr-only">{t("assistant.clear")}</span>
        </Button>
      </div>

      {/* 消息列表 */}
      <div
        ref={listRef}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3"
      >
        {messages.length === 0 && (
          <p className="py-6 text-center text-xs text-muted-foreground">
            {t("assistant.emptyHint")}
          </p>
        )}
        {messages.map((message) =>
          message.role === "user" ? (
            <div key={message.id} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-primary px-3 py-1.5 text-sm text-primary-foreground">
                {message.content}
                {message.filePath && (
                  <div className="mt-0.5 text-[10px] opacity-70">
                    {message.filePath}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div key={message.id} className="space-y-2">
              <div className="max-w-[95%] rounded-lg bg-muted px-3 py-2 text-sm">
                {message.content ? (
                  <MessageMarkdown content={message.content} />
                ) : streaming ? (
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                ) : null}
              </div>
              {(message.suggestions || []).length > 0 && (
                <div className="space-y-1.5">
                  {/* 已应用判定单一来源：appliedSuggestions[index]。历史会话
                      加载时（含旧数据 suggestionApplied=true）已由 editorStore
                      注入点归一化为同一 map，此处不再读消息级标记——
                      应用第 1 条不会误禁其余卡片 */}
                  {message.suggestions.map((suggestion) => (
                    <SuggestionCard
                      key={`${message.id}-${suggestion.index}`}
                      messageId={message.id}
                      suggestion={suggestion}
                      applied={Boolean(
                        message.appliedSuggestions?.[suggestion.index],
                      )}
                      readOnly={readOnly}
                      hasSelection={hasSelection}
                      // tab 守卫（Q17）：活跃 tab 与发送时来源不一致 →
                      // 禁用应用并提示切换（不自动切 tab）
                      mismatchedPath={
                        message.filePath && message.filePath !== activeTabPath
                          ? message.filePath
                          : null
                      }
                      onApply={handleApply}
                    />
                  ))}
                </div>
              )}
            </div>
          ),
        )}
        {toolStatus && (
          <div className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2 py-1 text-[11px] text-muted-foreground">
            <Wrench className="size-3 animate-pulse" />
            {toolStatus.tool}
          </div>
        )}
      </div>

      {/* DDG 挑战（阶段五a）：终流后弹出，侧栏内提交/放弃（三语键沿用
          pages:writingChat.captcha*，挑战解决后由用户手动重发原提问） */}
      <CaptchaDialog
        challenge={captchaChallenge}
        submitting={captchaSubmitting}
        onSubmit={submitCaptcha}
        onDismiss={dismissCaptcha}
      />

      {/* 输入区 */}
      <div className="space-y-2 border-t p-3">
        {largeBuffer && (
          <div className="flex items-center gap-2">
            <Checkbox
              id="assistant-include-full"
              checked={includeFullFile}
              onCheckedChange={(checked) =>
                setIncludeFullFile(checked === true)
              }
            />
            <Label htmlFor="assistant-include-full" className="text-xs">
              {t("assistant.includeFullFile")}
            </Label>
          </div>
        )}
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder={t("assistant.inputPlaceholder")}
          className="min-h-16 resize-none text-sm"
          disabled={streaming}
        />
        <div className="flex items-center justify-between">
          <span className="truncate text-[10px] text-muted-foreground">
            {activeTabPath || t("assistant.noFileHint")}
          </span>
          <Button
            size="sm"
            onClick={handleSend}
            disabled={streaming || !input.trim()}
          >
            {streaming ? (
              <Loader2 className="animate-spin" />
            ) : (
              <SendHorizontal />
            )}
            {t("assistant.send")}
          </Button>
        </div>
      </div>
    </Card>
  );
}

/** React.memo：EditorPage 传入的 readOnly 与三个回调均为稳定引用
 * （useCallback），父页面因缓冲区键入重渲染时本面板可跳过 */
export default memo(AssistantPanel);
