import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  Lightbulb,
  ChevronDown,
  User,
  Bot,
  Globe,
  Copy,
  CopyCheck,
  PencilLine,
  Trash2,
  Undo2,
  History,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import MarkdownRenderer from "@/components/MarkdownRenderer";

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export default function MessageBubble({ message, actions }) {
  const { t } = useTranslation(["pages", "common"]);
  const {
    generateEdit,
    applyEdit,
    deleteMessage,
    undoMessage,
    editingMessageId,
    applyingMessageId,
    canEdit,
  } = actions;

  const isUser = message.role === "user";
  const isSummary = message.role === "summary" && message.compressed;
  const isStreaming = message.streaming;
  const [copied, setCopied] = useState(false);
  const [thinkingExpanded, setThinkingExpanded] = useState(() => !message.thinkingClosed);
  const [searchExpanded, setSearchExpanded] = useState(true);

  // 在流式更新 thinkingClosed 时同步展开状态
  useEffect(() => {
    if (!message.thinkingClosed) {
      setThinkingExpanded(true);
    }
  }, [message.thinkingClosed]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback for older browsers
      const ta = document.createElement("textarea");
      ta.value = message.content;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      className={`group flex gap-2 ${
        isUser ? "flex-row-reverse pr-2" : "pl-2"
      }`}
    >
      {/* 头像 */}
      <div
        className={`shrink-0 size-7 rounded-full flex items-center justify-center text-xs mt-2 ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {isUser ? <User className="size-3.5" /> : <Bot className="size-3.5" />}
      </div>

      <div className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"} min-w-0 flex-1`}>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <span>
            {isUser ? t("pages:writingChat.roleUser") : isSummary ? t("pages:writingChat.roleSummary") : t("pages:writingChat.roleAssistant")}
          </span>
          {message.created_at && (
            <span>· {formatTime(message.created_at)}</span>
          )}
        </div>
        <div
          className={`rounded-lg px-3 py-2 max-w-[85%] ${
            isUser
              ? "bg-primary text-primary-foreground"
              : isSummary
                ? "bg-amber-500/10 border border-amber-500/30 dark:border-amber-400/30"
                : "bg-muted"
          }`}
        >
        {isSummary && (
          <div className="text-xs font-medium text-amber-600 dark:text-amber-400 mb-1 flex items-center gap-1">
            <History className="size-3" />
            {t("pages:writingChat.compressedHint")}
          </div>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap text-sm">
            {message.referenced_paragraph != null && (
              <span className="block text-xs opacity-75 mb-1">
                {t("pages:writingChat.anchorParagraph", { index: message.referenced_paragraph })}
              </span>
            )}
            {message.content}
          </div>
        ) : (
          <div className="text-sm">
            {/* 深度思考折叠框 */}
            {message.thinkingContent && (
              <div
                className="mb-2 rounded bg-background/30 border p-2"
                style={{ borderColor: "color-mix(in oklch, var(--primary) 20%, transparent)" }}
              >
                <button
                  type="button"
                  className="cursor-pointer text-xs font-medium flex items-center gap-1 select-none w-full text-left"
                  onClick={() => setThinkingExpanded((v) => !v)}
                  style={{ color: "color-mix(in oklch, var(--primary) 75%, transparent)" }}
                >
                  <Lightbulb className="size-3 shrink-0" />
                  {t("pages:writingChat.deepThinking")}
                  {!message.thinkingClosed && (
                    <span className="inline-flex items-center gap-1 ml-1 text-muted-foreground font-normal">
                      <Loader2 className="size-2.5 animate-spin" />
                    </span>
                  )}
                  <ChevronDown className={`size-3 ml-auto transition-transform duration-400 ${thinkingExpanded ? "rotate-180" : ""}`} />
                </button>
                <div
                  className="grid transition-[grid-template-rows] duration-400 ease-in-out"
                  style={{ gridTemplateRows: thinkingExpanded ? "1fr" : "0fr" }}
                >
                  <div className="overflow-hidden">
                    <div className="mt-2 text-xs leading-relaxed text-muted-foreground">
                      <MarkdownRenderer>
                        {message.thinkingContent}
                      </MarkdownRenderer>
                    </div>
                  </div>
                </div>
              </div>
            )}
            {/* 搜索结果折叠框 */}
            {message.searchResults && message.searchResults.length > 0 && (
              <div
                className="mb-2 rounded bg-background/30 border p-2"
                style={{ borderColor: "color-mix(in oklch, #3b82f6 20%, transparent)" }}
              >
                <button
                  type="button"
                  className="cursor-pointer text-xs font-medium flex items-center gap-1 select-none w-full text-left"
                  onClick={() => setSearchExpanded((v) => !v)}
                  style={{ color: "color-mix(in oklch, #3b82f6 75%, transparent)" }}
                >
                  <Globe className="size-3 shrink-0" />
                  {t("pages:writingChat.searchResults")}
                  <span className="text-muted-foreground font-normal">
                    {message.searchQuery && ` — ${message.searchQuery}`}
                  </span>
                  <ChevronDown className={`size-3 ml-auto transition-transform duration-400 ${searchExpanded ? "rotate-180" : ""}`} />
                </button>
                <div
                  className="grid transition-[grid-template-rows] duration-400 ease-in-out"
                  style={{ gridTemplateRows: searchExpanded ? "1fr" : "0fr" }}
                >
                  <div className="overflow-hidden">
                    <div className="mt-2 text-xs leading-relaxed">
                      <ul className="space-y-2">
                        {message.searchResults.map((result) => (
                          <li key={result.index} className="flex flex-col gap-0.5">
                            <span className="font-medium text-foreground">
                              [{result.index}] {result.title}
                            </span>
                            {result.snippet && (
                              <span className="text-muted-foreground">{result.snippet}</span>
                            )}
                            {result.url && (
                              <a
                                href={result.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-500 hover:underline truncate"
                              >
                                {result.url}
                              </a>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              </div>
            )}
            {/* 显示内容 */}
            {isStreaming && !message.displayContent && !message.thinkingOpen ? (
              <span className="flex items-center gap-1 text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                {t("pages:writingChat.thinking")}
              </span>
            ) : message.displayContent || (!message.thinkingOpen && message.content) ? (
              <MarkdownRenderer>
                {(message.displayContent || message.content)}
              </MarkdownRenderer>
            ) : null}
          </div>
        )}
      </div>

      {/* edit_op 卡片 */}
      {message.edit_op && (
        <EditOpCard
          edit_op={message.edit_op}
          reason={message.edit_reason}
          onApply={(customText) => applyEdit(message.id, customText)}
          applying={applyingMessageId === message.id}
        />
      )}

      {/* 消息操作栏 */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {!isStreaming && (
          <Button
            size="icon-xs"
            variant="ghost"
            title={t("pages:writingChat.copyRaw")}
            onClick={handleCopy}
          >
            {copied ? <CopyCheck className="size-3 text-green-500" /> : <Copy className="size-3" />}
          </Button>
        )}
        {!isUser && !isStreaming && canEdit && !message.edit_op && (
          <Button
            size="icon-xs"
            variant="ghost"
            title={t("pages:writingChat.generateEdit")}
            onClick={() => generateEdit(message.id, "")}
            disabled={editingMessageId === message.id}
          >
            {editingMessageId === message.id ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <PencilLine className="size-3" />
            )}
          </Button>
        )}
        {isSummary ? (
          <Button
            size="icon-xs"
            variant="ghost"
            title={t("pages:writingChat.undoCompressed")}
            onClick={() => undoMessage(message.id)}
          >
            <Undo2 className="size-3" />
          </Button>
        ) : (
          <Button
            size="icon-xs"
            variant="ghost"
            title={t("pages:writingChat.undoMessage")}
            onClick={() => deleteMessage(message.id)}
          >
            <Trash2 className="size-3" />
          </Button>
        )}
      </div>
      </div>
    </div>
  );
}
