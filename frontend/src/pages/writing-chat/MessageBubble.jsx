import { useEffect, useState, memo, useDeferredValue } from "react";
import { useTranslation } from "react-i18next";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Lightbulb from "lucide-react/dist/esm/icons/lightbulb.mjs";
import ChevronDown from "lucide-react/dist/esm/icons/chevron-down.mjs";
import User from "lucide-react/dist/esm/icons/user.mjs";
import Bot from "lucide-react/dist/esm/icons/bot.mjs";
import Globe from "lucide-react/dist/esm/icons/globe.mjs";
import Copy from "lucide-react/dist/esm/icons/copy.mjs";
import CopyCheck from "lucide-react/dist/esm/icons/copy-check.mjs";
import PencilLine from "lucide-react/dist/esm/icons/pencil-line.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import Undo2 from "lucide-react/dist/esm/icons/undo-2.mjs";
import History from "lucide-react/dist/esm/icons/history.mjs";
import GraduationCap from "lucide-react/dist/esm/icons/graduation-cap.mjs";
import BookOpenText from "lucide-react/dist/esm/icons/book-open-text.mjs";
import ShieldAlert from "lucide-react/dist/esm/icons/shield-alert.mjs";
import { Button } from "@/components/ui/button";
import MarkdownRenderer from "@/components/MarkdownRenderer";

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

const OA_URL_RE = /^https?:\/\//i;

const areResultsEqual = (prevProps, nextProps) => {
  const a = prevProps.result;
  const b = nextProps.result;
  return a.id === b.id && a.doi === b.doi && a.title === b.title;
};

/**
 * MessageBubble 的 memo 比较器：
 * 只比较影响气泡渲染的字段，函数型 actions 依赖上层保持稳定引用，
 * 这样流式更新时只有内容变化的那条消息会重渲染。
 */
const areMessageBubblesEqual = (prevProps, nextProps) => {
  const a = prevProps.message;
  const b = nextProps.message;
  if (
    a.id !== b.id ||
    a.role !== b.role ||
    a.content !== b.content ||
    a.displayContent !== b.displayContent ||
    a.thinkingContent !== b.thinkingContent ||
    a.thinkingOpen !== b.thinkingOpen ||
    a.thinkingClosed !== b.thinkingClosed ||
    a.streaming !== b.streaming ||
    a.tokens !== b.tokens ||
    a.referenced_paragraph !== b.referenced_paragraph ||
    a.created_at !== b.created_at ||
    a.edit_op !== b.edit_op ||
    a.edit_reason !== b.edit_reason ||
    a.searchQuery !== b.searchQuery ||
    a.deepReadPages !== b.deepReadPages ||
    a.citationNote !== b.citationNote ||
    a.rewrittenQueries !== b.rewrittenQueries ||
    a.searchResults !== b.searchResults ||
    a.scholarlyResults !== b.scholarlyResults ||
    a.scholarlyTotal !== b.scholarlyTotal ||
    a.scholarlyQuery !== b.scholarlyQuery ||
    a.evidenceSources !== b.evidenceSources ||
    a.pageReads !== b.pageReads ||
    a.citedEvidenceIds !== b.citedEvidenceIds ||
    a.unverifiedEvidenceIds !== b.unverifiedEvidenceIds ||
    a.compressed !== b.compressed
  ) {
    return false;
  }
  const pa = prevProps.actions;
  const na = nextProps.actions;
  return (
    pa.editingMessageId === na.editingMessageId &&
    pa.applyingMessageId === na.applyingMessageId &&
    pa.canEdit === na.canEdit &&
    pa.canApplyEdit === na.canApplyEdit
  );
};

const ScholarlyResultItem = memo(({ result, t }) => {
  return (
    <li className="flex flex-col gap-0.5 border-b border-border/50 pb-2 last:border-b-0 last:pb-0">
      <span className="text-xs font-medium leading-snug text-foreground">
        {result.title}
      </span>
      <span className="text-[11px] text-muted-foreground">
        {result.authors?.join(", ") ||
          t("pages:writingChat.scholarlyUnknownAuthor")}
        {result.year > 0 && ` (${result.year})`}
      </span>
      {result.journal && (
        <span className="text-[11px] italic text-muted-foreground">
          {result.journal}
        </span>
      )}
      <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
        {result.work_type && (
          <span className="rounded bg-violet-500/10 px-1 py-0.5 text-[10px] font-medium text-violet-600 dark:text-violet-400">
            {t(`pages:writingChat.scholarlyType.${result.work_type}`, {
              defaultValue: result.work_type,
            })}
          </span>
        )}
        {result.citation_count > 0 && (
          <span className="rounded bg-blue-500/10 px-1 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
            {t("pages:writingChat.scholarlyCiteCount", {
              count: result.citation_count,
            })}
          </span>
        )}
        {result.is_oa && (
          <span className="rounded bg-green-500/10 px-1 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400">
            {t("pages:writingChat.scholarlyOa")}
          </span>
        )}
        {result.doi && (
          <a
            href={`https://doi.org/${result.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="link-shrink rounded bg-zinc-500/10 px-1 py-0.5 font-mono text-[10px] text-zinc-600 dark:text-zinc-400"
          >
            DOI
          </a>
        )}
        {!result.doi && OA_URL_RE.test(result.oa_url || "") && (
          <a
            href={result.oa_url}
            target="_blank"
            rel="noopener noreferrer"
            className="link-shrink rounded bg-green-500/10 px-1 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400"
          >
            {t("pages:writingChat.scholarlyOpen")}
          </a>
        )}
      </div>
    </li>
  );
}, areResultsEqual);

function MessageBubble({ message, actions }) {
  const { t } = useTranslation(["pages", "common"]);
  const {
    generateEdit,
    applyEdit,
    deleteMessage,
    editingMessageId,
    applyingMessageId,
    canEdit,
    canApplyEdit = true,
    prepareSectionRead,
  } = actions;

  const isUser = message.role === "user";
  const isSummary = message.role === "summary" && message.compressed;
  const isStreaming = message.streaming;
  const [copied, setCopied] = useState(false);
  const [thinkingExpanded, setThinkingExpanded] = useState(
    () => !message.thinkingClosed,
  );
  const [searchExpanded, setSearchExpanded] = useState(true);
  const [scholarlyExpanded, setScholarlyExpanded] = useState(true);
  const [evidenceExpanded, setEvidenceExpanded] = useState(true);
  const [pagesExpanded, setPagesExpanded] = useState(false);
  // 流式期间内容高频变化，Markdown 解析延迟到空闲帧，输入与滚动保持流畅
  const markdownContent = message.displayContent || message.content;
  const deferredMarkdownContent = useDeferredValue(markdownContent);

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
      className={`group flex gap-2 [content-visibility:auto] [contain-intrinsic-size:auto_120px] ${
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

      <div
        className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"} min-w-0 flex-1`}
      >
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <span>
            {isUser
              ? t("pages:writingChat.roleUser")
              : isSummary
                ? t("pages:writingChat.roleSummary")
                : t("pages:writingChat.roleAssistant")}
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
              {message.referenced_paragraph != null &&
                message.referenced_paragraph >= 0 && (
                  <span className="block text-xs opacity-75 mb-1">
                    {t("pages:writingChat.anchorParagraph", {
                      index: message.referenced_paragraph,
                    })}
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
                  style={{
                    borderColor:
                      "color-mix(in oklch, var(--primary) 20%, transparent)",
                  }}
                >
                  <button
                    type="button"
                    className="cursor-pointer text-xs font-medium flex items-center gap-1 select-none w-full text-left"
                    onClick={() => setThinkingExpanded((v) => !v)}
                    style={{
                      color:
                        "color-mix(in oklch, var(--primary) 75%, transparent)",
                    }}
                  >
                    <Lightbulb className="size-3 shrink-0" />
                    {t("pages:writingChat.deepThinking")}
                    {!message.thinkingClosed && (
                      <span className="inline-flex items-center gap-1 ml-1 text-muted-foreground font-normal">
                        <Loader2 className="size-2.5 animate-spin" />
                      </span>
                    )}
                    <ChevronDown
                      className={`size-3 ml-auto transition-transform duration-400 ${thinkingExpanded ? "rotate-180" : ""}`}
                    />
                  </button>
                  <div
                    className="grid transition-[grid-template-rows] duration-400 ease-in-out"
                    style={{
                      gridTemplateRows: thinkingExpanded ? "1fr" : "0fr",
                    }}
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
                  style={{
                    borderColor:
                      "color-mix(in oklch, #3b82f6 20%, transparent)",
                  }}
                >
                  <button
                    type="button"
                    className="cursor-pointer text-xs font-medium flex items-center gap-1 select-none w-full text-left"
                    onClick={() => setSearchExpanded((v) => !v)}
                    style={{
                      color: "color-mix(in oklch, #3b82f6 75%, transparent)",
                    }}
                  >
                    <Globe className="size-3 shrink-0" />
                    {t("pages:writingChat.searchResults")}
                    <span className="text-muted-foreground font-normal">
                      {message.searchQuery && ` — ${message.searchQuery}`}
                    </span>
                    <ChevronDown
                      className={`size-3 ml-auto transition-transform duration-400 ${searchExpanded ? "rotate-180" : ""}`}
                    />
                  </button>
                  <div
                    className="grid transition-[grid-template-rows] duration-400 ease-in-out"
                    style={{ gridTemplateRows: searchExpanded ? "1fr" : "0fr" }}
                  >
                    <div className="overflow-hidden">
                      <div className="mt-2 text-xs leading-relaxed">
                        <div className="flex items-center gap-1.5 mb-2 flex-wrap">
                          {message.rewrittenQueries?.length > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 font-medium">
                              {t("pages:writingChat.multiQuery", {
                                n: message.rewrittenQueries.length,
                              })}
                            </span>
                          )}
                          {message.deepReadPages > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-600 dark:text-green-400 font-medium">
                              {t("pages:writingChat.deepRead", {
                                n: message.deepReadPages,
                              })}
                            </span>
                          )}
                        </div>
                        <ul className="space-y-2">
                          {message.searchResults.map((result) => (
                            <li
                              key={result.index}
                              className="flex flex-col gap-0.5"
                            >
                              <span className="font-medium text-foreground">
                                [{result.index}] {result.title}
                              </span>
                              {result.snippet && (
                                <span className="text-muted-foreground">
                                  {result.snippet}
                                </span>
                              )}
                              {result.url && (
                                <a
                                  href={result.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="link-shrink text-blue-500 truncate"
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
              {/* 引用校验提示 */}
              {message.pageReads?.length > 0 && (
                <div className="mb-2 rounded border border-cyan-500/20 bg-cyan-500/5 p-2">
                  <button
                    type="button"
                    className="flex w-full cursor-pointer select-none items-center gap-1 text-left text-xs font-medium text-cyan-700 dark:text-cyan-300"
                    onClick={() => setPagesExpanded((value) => !value)}
                  >
                    <BookOpenText className="size-3 shrink-0" />
                    {t("pages:writingChat.pageOutline")}
                    <span className="font-normal text-muted-foreground">
                      · {message.pageReads.length}
                    </span>
                    <ChevronDown
                      className={`ml-auto size-3 transition-transform duration-300 ${pagesExpanded ? "rotate-180" : ""}`}
                    />
                  </button>
                  <div
                    className="grid transition-[grid-template-rows] duration-300 ease-in-out"
                    style={{ gridTemplateRows: pagesExpanded ? "1fr" : "0fr" }}
                  >
                    <div className="overflow-hidden">
                      <div className="mt-2 space-y-2 text-xs">
                        {message.pageReads.map((page, pageIndex) => {
                          const readIds = new Set(
                            message.pageReads
                              .filter(
                                (item) =>
                                  (item.document_id || item.url) ===
                                  (page.document_id || page.url),
                              )
                              .flatMap((item) => item.read_section_ids || []),
                          );
                          const unreadIds = (page.outline || [])
                            .map((section) => section.section_id)
                            .filter((sectionId) => !readIds.has(sectionId));
                          return (
                            <div
                              key={`${page.document_id || page.url}-${pageIndex}`}
                              className="border-t border-border/50 pt-2 first:border-t-0 first:pt-0"
                            >
                              <a
                                href={page.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="link-shrink font-medium text-cyan-700 dark:text-cyan-300"
                              >
                                {page.title || page.url}
                              </a>
                              {page.error_code && (
                                <div className="mt-1 flex items-start gap-1 text-amber-600 dark:text-amber-400">
                                  <ShieldAlert className="mt-0.5 size-3 shrink-0" />
                                  <span>
                                    {t("pages:writingChat.pageReadError", {
                                      code: page.error_code,
                                      message: page.error_message,
                                    })}
                                  </span>
                                </div>
                              )}
                              {page.outline?.length > 0 && (
                                <ul className="mt-1 space-y-1 text-muted-foreground">
                                  {page.outline.map((section) => (
                                    <li
                                      key={section.section_id}
                                      className="flex items-start gap-1"
                                    >
                                      <span className="font-mono text-[10px]">
                                        {readIds.has(section.section_id)
                                          ? "●"
                                          : "○"}
                                      </span>
                                      <span>
                                        {section.heading_path?.join(" › ") ||
                                          section.title}
                                      </span>
                                    </li>
                                  ))}
                                </ul>
                              )}
                              {unreadIds.length > 0 && (
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  className="mt-2 h-7 text-xs"
                                  onClick={() =>
                                    prepareSectionRead(
                                      page,
                                      unreadIds.slice(0, 5),
                                    )
                                  }
                                >
                                  {t("pages:writingChat.readMoreSections")}
                                </Button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {message.evidenceSources?.length > 0 && (
                <div className="mb-2 rounded border border-emerald-500/20 bg-emerald-500/5 p-2">
                  <button
                    type="button"
                    className="flex w-full cursor-pointer select-none items-center gap-1 text-left text-xs font-medium text-emerald-700 dark:text-emerald-300"
                    onClick={() => setEvidenceExpanded((value) => !value)}
                  >
                    <Globe className="size-3 shrink-0" />
                    {t("pages:writingChat.evidenceSources")}
                    <span className="font-normal text-muted-foreground">
                      · {message.evidenceSources.length}
                    </span>
                    <ChevronDown
                      className={`ml-auto size-3 transition-transform duration-300 ${evidenceExpanded ? "rotate-180" : ""}`}
                    />
                  </button>
                  <div
                    className="grid transition-[grid-template-rows] duration-300 ease-in-out"
                    style={{
                      gridTemplateRows: evidenceExpanded ? "1fr" : "0fr",
                    }}
                  >
                    <div className="overflow-hidden">
                      <ul className="mt-2 space-y-2 text-xs">
                        {message.evidenceSources.map((evidence) => (
                          <li
                            key={evidence.evidence_id}
                            className="border-t border-border/50 pt-2 first:border-t-0 first:pt-0"
                          >
                            <div className="flex items-start justify-between gap-2">
                              <a
                                href={evidence.source_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="link-shrink font-medium text-emerald-700 dark:text-emerald-300"
                              >
                                {evidence.title || evidence.source_url}
                              </a>
                              {message.citedEvidenceIds?.includes(
                                evidence.evidence_id,
                              ) && (
                                <span className="shrink-0 text-[10px] text-emerald-600 dark:text-emerald-400">
                                  {t("pages:writingChat.evidenceCited")}
                                </span>
                              )}
                            </div>
                            {evidence.heading_path?.length > 0 && (
                              <div className="mt-0.5 text-[10px] text-muted-foreground">
                                {evidence.heading_path.join(" › ")}
                              </div>
                            )}
                            <p className="mt-1 line-clamp-4 text-muted-foreground">
                              {evidence.text}
                            </p>
                            <code className="mt-1 block break-all text-[10px] text-muted-foreground">
                              {evidence.evidence_id}
                            </code>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              )}
              {message.citationNote && (
                <div className="mb-2 rounded border border-amber-500/25 bg-amber-500/10 p-2">
                  <div className="text-xs font-medium text-amber-600 dark:text-amber-400">
                    {t("pages:writingChat.citationWarning")}
                  </div>
                  <pre className="mt-1 text-[10px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
                    {message.citationNote}
                  </pre>
                </div>
              )}
              {/* 学术检索结果折叠框 */}
              {message.scholarlyResults?.length > 0 && (
                <div className="mb-2 rounded border border-violet-500/20 bg-violet-500/5 p-2">
                  <button
                    type="button"
                    className="flex w-full cursor-pointer select-none items-center gap-1 text-left text-xs font-medium text-violet-600 dark:text-violet-400"
                    onClick={() => setScholarlyExpanded((value) => !value)}
                  >
                    <GraduationCap className="size-3 shrink-0" />
                    {t("pages:writingChat.scholarlyResults")}
                    {message.scholarlyQuery && (
                      <span className="truncate font-normal text-muted-foreground">
                        · {message.scholarlyQuery}
                      </span>
                    )}
                    <ChevronDown
                      className={`ml-auto size-3 transition-transform duration-300 ${scholarlyExpanded ? "rotate-180" : ""}`}
                    />
                  </button>
                  <div
                    className="grid transition-[grid-template-rows] duration-300 ease-in-out"
                    style={{
                      gridTemplateRows: scholarlyExpanded ? "1fr" : "0fr",
                    }}
                  >
                    <div className="overflow-hidden">
                      <div className="mt-2 text-xs leading-relaxed">
                        <div className="mb-2 text-[10px] text-muted-foreground">
                          {t("pages:writingChat.scholarlyResultsCount", {
                            count: message.scholarlyTotal,
                          })}
                        </div>
                        <ul className="space-y-2">
                          {message.scholarlyResults.map((result) => (
                            <ScholarlyResultItem
                              key={result.id || result.doi || result.title}
                              result={result}
                              t={t}
                            />
                          ))}
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>
              )}
              {/* 显示内容 */}
              {isStreaming &&
              !message.displayContent &&
              !message.thinkingOpen ? (
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" />
                  {t("pages:writingChat.thinking")}
                </span>
              ) : message.displayContent ||
                (!message.thinkingOpen && message.content) ? (
                <MarkdownRenderer>{deferredMarkdownContent}</MarkdownRenderer>
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
            applyDisabled={!canApplyEdit}
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
              {copied ? (
                <CopyCheck className="size-3 text-green-500" />
              ) : (
                <Copy className="size-3" />
              )}
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
              title={t("pages:writingChat.deleteCompressed")}
              onClick={() => deleteMessage(message.id)}
            >
              <Undo2 className="size-3" />
            </Button>
          ) : (
            <Button
              size="icon-xs"
              variant="ghost"
              title={t("pages:writingChat.deleteMessage")}
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

export default memo(MessageBubble, areMessageBubblesEqual);
