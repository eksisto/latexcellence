import { useEffect, useRef, useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
      className={`group flex flex-col gap-1 ${
        isUser ? "items-end pr-2" : "items-start"
      }`}
    >
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <span>
          {isUser ? "用户" : isSummary ? "会话摘要" : "助手"}
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
            会话已压缩 — 可撤回还原原始消息
          </div>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap text-sm">
            {message.referenced_paragraph != null && (
              <span className="block text-xs opacity-75 mb-1">
                锚点段落：P{message.referenced_paragraph}
              </span>
            )}
            {message.content}
          </div>
        ) : (
          <div className="text-sm">
            {/* 深度思考折叠框 */}
            {message.thinkingContent && (
              <div className="mb-2 rounded bg-background/30 border border-border p-2">
                <button
                  type="button"
                  className="cursor-pointer text-xs font-medium text-amber-600 dark:text-amber-400 flex items-center gap-1 select-none w-full text-left"
                  onClick={() => setThinkingExpanded((v) => !v)}
                >
                  <Lightbulb className="size-3 shrink-0" />
                  深度思考
                  {!message.thinkingClosed && (
                    <span className="inline-flex items-center gap-1 ml-1 text-muted-foreground font-normal">
                      <Loader2 className="size-2.5 animate-spin" />
                    </span>
                  )}
                  <ChevronDown className={`size-3 ml-auto transition-transform duration-300 ${thinkingExpanded ? "rotate-180" : ""}`} />
                </button>
                <div
                  className="grid transition-[grid-template-rows] duration-300 ease-in-out"
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
            {/* 显示内容 */}
            {isStreaming && !message.displayContent && !message.thinkingOpen ? (
              <span className="flex items-center gap-1 text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                思考中...
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
            title="复制原始文本"
            onClick={handleCopy}
          >
            {copied ? <CopyCheck className="size-3 text-green-500" /> : <Copy className="size-3" />}
          </Button>
        )}
        {!isUser && !isStreaming && canEdit && !message.edit_op && (
          <Button
            size="icon-xs"
            variant="ghost"
            title="生成编辑建议"
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
            title="撤回压缩消息（恢复原始对话）"
            onClick={() => undoMessage(message.id)}
          >
            <Undo2 className="size-3" />
          </Button>
        ) : (
          <Button
            size="icon-xs"
            variant="ghost"
            title="撤回消息"
            onClick={() => deleteMessage(message.id)}
          >
            <Trash2 className="size-3" />
          </Button>
        )}
      </div>
    </div>
  );
}

function EditOpCard({ edit_op, reason, onApply, applying }) {
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
          编辑建议 · 段落 {edit_op.paragraph_index} ({edit_op.file_path})
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-1 space-y-2">
        {reason && (
          <p className="text-xs text-muted-foreground">{reason}</p>
        )}
        <div className={`grid grid-cols-1 ${isEditing ? '' : 'md:grid-cols-2'} gap-2`}>
          <div>
            <div className="text-xs text-muted-foreground mb-1">原文</div>
            <pre className="text-xs font-mono bg-muted rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
              {edit_op.original_latex}
            </pre>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">新文本</div>
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
              查看 unified diff
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
              已应用
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
                {isEditing ? "取消编辑" : "手动编辑"}
              </Button>
              <Button size="sm" onClick={handleApply} disabled={applying}>
                {applying && <Loader2 className="size-3 animate-spin" />}
                应用到 .tex
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function WritingChatPage() {
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
            写作助手 Chat
          </h1>
          {compressed && (
            <span className="ml-2 text-xs px-2 py-0.5 rounded bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/30">
              会话已压缩
            </span>
          )}
        </div>
      </div>

      {!projectPath || texFiles.length === 0 ? (
        <div className="max-w-7xl mx-auto">
          <Card className="max-w-md mx-auto mt-12">
            <CardHeader>
              <CardTitle>未选择项目</CardTitle>
            </CardHeader>
            <CardContent>
              <Button nativeButton={false} render={<Link to="/workspace" />}>
                打开工作区
              </Button>
            </CardContent>
          </Card>
        </div>
      ) : (
        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-[200px_1fr_2fr] gap-4">
          {/* 左：会话列表 */}
          <Card className="h-[calc(100vh-160px)] min-h-[400px] flex flex-col">
            <CardHeader className="pb-2 flex items-center justify-between">
              <CardTitle className="text-sm">会话</CardTitle>
              <Button
                size="icon-xs"
                variant="ghost"
                title="新建会话"
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
                      暂无会话，发送消息即可新建
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
                          title="双击重命名"
                        >
                          {s.title || "(无标题)"}
                        </span>
                      )}
                      <div className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity duration-150 flex items-center gap-0.5 bg-inherit pl-2">
                        <Button
                          size="icon-xs"
                          variant="ghost"
                          title="重命名"
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
                          title="删除会话"
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
                上下文锚点
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 flex-1 min-h-0 overflow-y-auto">
              <Select
                value={activeFile || ""}
                onValueChange={handleFileChange}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="选择 .tex 文件" />
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
                  <SelectValue placeholder="选择段落（可选）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">（不指定段落）</SelectItem>
                  {paragraphs.map((p) => (
                    <SelectItem key={p.index} value={String(p.index)}>
                      P{p.index} · {(p.text || "").slice(0, 30).replace(/\n/g, " ")}
                      {(p.text || "").length > 30 ? "..." : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <div className="text-xs text-muted-foreground">
                <p>提示：</p>
                <div className="mt-2 pl-2 space-y-1">
                  <p>
                    1. 不指定段落时，将以文件全文（最多 12 段）作为对话背景。
                  </p>
                  <p>
                    2. 选择段落，并在助手回复下方点击
                    <PencilLine className="inline mx-0.5 size-3" />
                    才会生成可应用的编辑建议。
                  </p>
                </div>
              </div>

              <Separator />

              {/* 助手（提示词）选择 */}
              <div className="space-y-2">
                <div className="text-xs font-medium flex items-center gap-1">
                  <Sparkles className="size-3" />
                  助手
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
                    placeholder="助手名称"
                    value={newAssistantName}
                    onChange={(e) => setNewAssistantName(e.target.value)}
                  />
                  <Textarea
                    className="min-h-16 resize-none text-xs"
                    placeholder="输入提示词..."
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
                      保存
                    </Button>
                    {activeAssistant && (
                      <span className="text-xs text-muted-foreground">
                        当前：{activeAssistant}
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
                      ? "（空会话）"
                      : "发送消息开始一次新的对话"}
                  </div>
                )}
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} actions={messageActions} />
                ))}
              </div>

              {error && (
                <p className="text-xs text-destructive py-1">{error}</p>
              )}

              <div className="border-t border-border pt-2 flex items-end gap-2">
                <Textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder={
                    activeFile
                      ? "向助手提问，Shift+Enter 换行"
                      : "请先选择文件..."
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
    </div>
  );
}