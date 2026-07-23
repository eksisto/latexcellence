import { useEffect, useRef, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  ArrowLeft,
  Send,
  Plus,
  Trash2,
  Undo2,
  PencilLine,
  Check,
  FileText,
  MessagesSquare,
  History,
  Copy,
  CopyCheck,
  Sparkles,
  Lightbulb,
  ChevronDown,
  User,
  Bot,
  Globe,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import useWritingChat from "@/hooks/useWritingChat";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import useStore from "@/store/useStore";

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function MessageBubble({ message, actions }) {
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

function EditOpCard({ edit_op, reason, onApply, applying }) {
  const { t } = useTranslation(["pages", "common"]);
  const [isEditing, setIsEditing] = useState(false);
  const [editedText, setEditedText] = useState(edit_op.new_latex);

  const handleApply = () => {
    if (isEditing) {
      onApply(editedText);
    } else {
      onApply();
    }
  };

  return (
    <Card className="w-full max-w-[90%] border-blue-500/30">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs flex items-center gap-2">
          <PencilLine className="size-3.5" />
          {t("pages:writingChat.editSuggestion", { paragraphIndex: edit_op.paragraph_index, filePath: edit_op.file_path })}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-1 space-y-2">
        {reason && (
          <p className="text-xs text-muted-foreground">{reason}</p>
        )}
        <div className={`grid grid-cols-1 ${isEditing ? '' : 'md:grid-cols-2'} gap-2`}>
          <div>
            <div className="text-xs text-muted-foreground mb-1">{t("common:original")}</div>
            <pre className="text-xs font-mono bg-muted rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
              {edit_op.original_latex}
            </pre>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">{t("pages:writingChat.newText")}</div>
            {isEditing ? (
              <textarea
                autoFocus
                className="w-full min-h-32 text-xs font-mono bg-background border border-border rounded p-2 resize-y whitespace-pre-wrap"
                value={editedText}
                onChange={(e) => setEditedText(e.target.value)}
              />
            ) : (
              <pre className="text-xs font-mono bg-green-500/5 border border-green-500/20 rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
                {edit_op.new_latex}
              </pre>
            )}
          </div>
        </div>
        {edit_op.diff_text && !isEditing && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              {t("pages:writingChat.viewUnifiedDiff")}
            </summary>
            <pre className="font-mono bg-muted rounded p-2 mt-1 max-h-64 overflow-y-auto whitespace-pre">
              {edit_op.diff_text}
            </pre>
          </details>
        )}
        <div className="flex items-center gap-2">
          {edit_op.applied ? (
            <span className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
              <Check className="size-3.5" />
              {t("pages:writingChat.applied")}
            </span>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  if (isEditing) {
                    setIsEditing(false);
                    setEditedText(edit_op.new_latex);
                  } else {
                    setIsEditing(true);
                    setEditedText(edit_op.new_latex);
                  }
                }}
              >
                <PencilLine className="size-3" />
                {isEditing ? t("pages:writingChat.cancelEdit") : t("pages:writingChat.manualEdit")}
              </Button>
              <Button size="sm" onClick={handleApply} disabled={applying}>
                {applying && <Loader2 className="size-3 animate-spin" />}
                {t("pages:writingChat.applyToTex")}
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function CaptchaDialog({ challenge, submitting, onSubmit, onDismiss }) {
  const { t } = useTranslation(["pages", "common"]);
  const [selected, setSelected] = useState(new Set());

  const toggle = (hash) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(hash)) next.delete(hash);
      else next.add(hash);
      return next;
    });
  };

  if (!challenge) return null;

  const gridSize = challenge.images.length <= 4 ? 2 : 3;

  return (
    <Dialog open={!!challenge} onOpenChange={(open) => { if (!open) onDismiss(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-amber-500" />
            {t("pages:writingChat.captchaTitle", { instruction: challenge.instruction ? ` — ${challenge.instruction}` : "" })}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {t("pages:writingChat.captchaDesc", { engine: challenge.engine_name ? `("${challenge.engine_name}")` : "" })}
          </p>

          <div
            className="grid gap-2 mx-auto"
            style={{ gridTemplateColumns: `repeat(${gridSize}, minmax(0, 1fr))` }}
          >
            {challenge.images.map((img) => {
              const isSelected = selected.has(img.hash);
              return (
                <button
                  key={img.hash}
                  type="button"
                  onClick={() => toggle(img.hash)}
                  className={`relative rounded-lg overflow-hidden border-2 transition-all cursor-pointer ${
                    isSelected
                      ? "border-primary ring-2 ring-primary/30"
                      : "border-border hover:border-muted-foreground/50"
                  }`}
                >
                  <img
                    src={`data:image/jpeg;base64,${img.base64}`}
                    alt={t("pages:writingChat.captchaImage")}
                    className="w-full aspect-square object-cover"
                    draggable={false}
                  />
                  {isSelected && (
                    <div className="absolute inset-0 bg-primary/20 flex items-center justify-center">
                      <Check className="size-6 text-primary drop-shadow-md" />
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          <p className="text-xs text-muted-foreground text-center">
            {t("pages:writingChat.captchaSelected", { count: selected.size })}
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onDismiss} disabled={submitting}>
            {t("pages:writingChat.captchaSkip")}
          </Button>
          <Button
            onClick={() => onSubmit(Array.from(selected))}
            disabled={submitting || selected.size === 0}
          >
            {submitting && <Loader2 className="size-3 animate-spin mr-1" />}
            {t("pages:writingChat.captchaSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


export default function WritingChatPage() {
  const { t } = useTranslation(["pages", "common"]);
  const {
    projectPath,
    activeFile,
    texFiles,
    paragraphs,
    paragraphIndex,
    setParagraphIndex,
    sessions,
    currentSessionId,
    messages,
    compressed,
    input,
    setInput,
    streaming,
    loading,
    error,
    editingMessageId,
    applyingMessageId,
    searchStatus,
    captchaChallenge,
    captchaSubmitting,
    submitCaptcha,
    dismissCaptcha,
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
    // 助手
    assistants,
    activeAssistant,
    setAssistantActive,
    saveAssistant,
    deleteAssistant,
  } = useWritingChat();

  const scrollRef = useRef(null);
  // 新建助手表单
  const [newAssistantName, setNewAssistantName] = useState("");
  const [newAssistantContent, setNewAssistantContent] = useState("");
  // 重命名会话
  const [renamingId, setRenamingId] = useState(null);
  const [renameTitle, setRenameTitle] = useState("");

  // 打印 deck: 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length, streaming]);

  // Enter 提交（Shift+Enter 换行）
  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const canEdit = paragraphIndex != null && !!activeFile;

  const messageActions = useMemo(() => ({
    generateEdit,
    applyEdit,
    deleteMessage,
    undoMessage,
    editingMessageId,
    applyingMessageId,
    canEdit,
  }), [generateEdit, applyEdit, deleteMessage, undoMessage, editingMessageId, applyingMessageId, canEdit]);

  return (
    <div className="min-h-screen p-4 md:p-8">
      <div className="max-w-7xl mx-auto mb-4">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <MessagesSquare className="size-5" />
            {t("pages:writingChat.title")}
          </h1>
          {compressed && (
            <span className="ml-2 text-xs px-2 py-0.5 rounded bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/30">
              {t("pages:writingChat.compressedToast")}
            </span>
          )}
        </div>
      </div>

      {!projectPath || texFiles.length === 0 ? (
        <div className="max-w-7xl mx-auto">
          <Card className="max-w-md mx-auto mt-12">
            <CardHeader>
              <CardTitle>{t("pages:writingChat.noProjectTitle")}</CardTitle>
            </CardHeader>
            <CardContent>
                <Button nativeButton={false} render={<Link to="/workspace" />}>
                  {t("common:openWorkspace")}
                </Button>
            </CardContent>
          </Card>
        </div>
      ) : (
        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-[200px_1fr_2fr] gap-4">
          {/* 左：会话列表 */}
          <Card className="h-[calc(100vh-160px)] min-h-[400px] flex flex-col">
            <CardHeader className="pb-2 flex items-center justify-between">
              <CardTitle className="text-sm">{t("pages:writingChat.sessions")}</CardTitle>
              <Button
                size="icon-xs"
                variant="ghost"
                title={t("pages:writingChat.newSession")}
                onClick={newSession}
              >
                <Plus className="size-3" />
              </Button>
            </CardHeader>
            <CardContent className="pt-0 flex-1 min-h-0">
              <ScrollArea className="h-full">
                <div className="space-y-1 pr-2">
                  {sessions.length === 0 && (
                    <p className="text-xs text-muted-foreground text-center py-4">
                      {t("pages:writingChat.noSessions")}
                    </p>
                  )}
                  {sessions.map((s) => (
                    <div
                      key={s.id}
                      className={`group flex items-center rounded px-2 py-1.5 cursor-pointer text-xs hover:bg-muted relative ${
                        s.id === currentSessionId
                          ? "bg-muted font-medium"
                          : ""
                      }`}
                    >
                      {renamingId === s.id ? (
                        <input
                          autoFocus
                          className="flex-1 min-w-0 bg-background border border-border rounded px-1 py-0.5 text-xs outline-none"
                          value={renameTitle}
                          onChange={(e) => setRenameTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              if (renameTitle.trim()) renameSession(s.id, renameTitle.trim());
                              setRenamingId(null);
                            }
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                          onBlur={() => {
                            if (renameTitle.trim()) renameSession(s.id, renameTitle.trim());
                            setRenamingId(null);
                          }}
                        />
                      ) : (
                        <span
                          className="truncate flex-1 min-w-0"
                          onClick={() => selectSession(s.id)}
                          onDoubleClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(s.id);
                            setRenameTitle(s.title || "");
                          }}
                          title={t("pages:writingChat.renameHint")}
                        >
                          {s.title || t("pages:writingChat.noTitle")}
                        </span>
                      )}
                      <div className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity duration-150 flex items-center gap-0.5 bg-inherit pl-2">
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          title={t("pages:writingChat.rename")}
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(s.id);
                            setRenameTitle(s.title || "");
                          }}
                        >
                          <PencilLine className="size-3" />
                        </Button>
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          title={t("pages:writingChat.deleteSession")}
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteSession(s.id);
                          }}
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>

          {/* 中：文件/段落选择 */}
          <Card className="h-[calc(100vh-160px)] min-h-[400px] flex flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-1">
                <FileText className="size-3.5" />
                {t("pages:writingChat.contextAnchor")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 flex-1 min-h-0 overflow-y-auto">
              <Select
                value={activeFile || ""}
                onValueChange={handleFileChange}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("pages:writingChat.selectFile")} />
                </SelectTrigger>
                <SelectContent>
                  {texFiles.map((f) => (
                    <SelectItem key={f.path} value={f.path}>
                      {f.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={paragraphIndex == null ? "all" : String(paragraphIndex)}
                onValueChange={(v) =>
                  setParagraphIndex(v === "all" ? null : parseInt(v, 10))
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("pages:writingChat.selectParagraph")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("pages:writingChat.noParagraph")}</SelectItem>
                  {paragraphs.map((p) => (
                    <SelectItem key={p.index} value={String(p.index)}>
                      P{p.index} · {(p.text || "").slice(0, 30).replace(/\n/g, " ")}
                      {(p.text || "").length > 30 ? "..." : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <div className="text-xs text-muted-foreground">
                <p>{t("pages:writingChat.assistantHintTitle")}</p>
                <div className="mt-2 pl-2 space-y-1">
                  <p>
                    {t("pages:writingChat.assistantHint1")}
                  </p>
                  <p>
                    {t("pages:writingChat.assistantHint2")}
                    <PencilLine className="inline mx-0.5 size-3" />
                    {t("pages:writingChat.assistantHint3")}
                  </p>
                </div>
              </div>

              <Separator />

              {/* 助手（提示词）选择 */}
              <div className="space-y-2">
                <div className="text-xs font-medium flex items-center gap-1">
                  <Sparkles className="size-3" />
                  {t("pages:writingChat.assistants")}
                </div>
                <div className="flex items-center gap-1 flex-wrap">
                  {assistants.map((a) => (
                    <Button
                      key={a.name}
                      variant={activeAssistant === a.name ? "default" : "outline"}
                      size="xs"
                      onClick={() => setAssistantActive(a.name)}
                    >
                      {a.name}
                    </Button>
                  ))}
                </div>

                {/* 新建助手 */}
                <div className="space-y-1">
                  <Input
                    className="h-7 text-xs"
                    placeholder={t("pages:writingChat.assistantName")}
                    value={newAssistantName}
                    onChange={(e) => setNewAssistantName(e.target.value)}
                  />
                  <Textarea
                    className="min-h-16 resize-none text-xs"
                    placeholder={t("pages:writingChat.assistantPlaceholder")}
                    value={newAssistantContent}
                    onChange={(e) => setNewAssistantContent(e.target.value)}
                  />
                  <div className="flex items-center gap-1">
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        if (!newAssistantName.trim() || !newAssistantContent.trim()) return;
                        saveAssistant(newAssistantName.trim(), newAssistantContent.trim());
                        setNewAssistantName("");
                        setNewAssistantContent("");
                      }}
                      disabled={!newAssistantName.trim() || !newAssistantContent.trim()}
                    >
                      <Plus className="size-3" />
                      {t("pages:writingChat.saveAssistant")}
                    </Button>
                    {activeAssistant && (
                      <span className="text-xs text-muted-foreground">
                        {t("pages:writingChat.activeAssistant", { name: activeAssistant })}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 右：聊天区 */}
          <Card className="h-[calc(100vh-160px)] min-h-[400px] flex flex-col">
            <CardContent className="flex-1 flex flex-col p-2 overflow-hidden rounded-t-xl">
              <div
                ref={scrollRef}
                className="flex-1 overflow-y-auto space-y-4 pr-1 rounded-t-xl"
              >
                {messages.length === 0 && !streaming && (
                  <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                    {currentSessionId
                      ? t("pages:writingChat.emptySession")
                      : t("pages:writingChat.emptySessionHint")}
                  </div>
                )}
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} actions={messageActions} />
                ))}
              </div>

              {error && (
                <p className="text-xs text-destructive py-1">{error}</p>
              )}

              {searchStatus && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-1 px-2 rounded bg-blue-500/5 border border-blue-500/20">
                  <Globe className="size-3 animate-pulse text-blue-500" />
                  <span>
                    {t("pages:writingChat.searching", { query: searchStatus.query || "..." })}
                  </span>
                </div>
              )}

              <div className="border-t border-border pt-2 flex items-end gap-2">
                <Textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder={
                    activeFile
                      ? t("pages:writingChat.inputPlaceholder")
                      : t("pages:writingChat.inputPlaceholderNoFile")
                  }
                  disabled={!activeFile || streaming}
                  className="min-h-[40px] max-h-40 resize-none text-sm"
                />
                <Button
                  onClick={() => sendMessage()}
                  disabled={!activeFile || streaming || !input.trim()}
                  size="icon"
                >
                  {streaming ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Send className="size-4" />
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 验证码弹窗 */}
      <CaptchaDialog
        challenge={captchaChallenge}
        submitting={captchaSubmitting}
        onSubmit={submitCaptcha}
        onDismiss={dismissCaptcha}
      />
    </div>
  );
}