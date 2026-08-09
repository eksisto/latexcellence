import { useEffect, useRef, useMemo, useState, lazy, Suspense } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  ArrowUp,
  Plus,
  Trash2,
  PencilLine,
  TextAlignStart,
  Sparkles,
  X,
  FolderOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import useWritingChat from "@/hooks/useWritingChat";
import { isVisibleChatMessage } from "@/hooks/writing-chat";
import MessageBubble from "./writing-chat/MessageBubble";
import CaptchaDialog from "./writing-chat/CaptchaDialog";
import ToolStatusBar from "./writing-chat/ToolStatusBar";
const ToolConfirmDialog = lazy(
  () => import("./writing-chat/ToolConfirmDialog"),
);
const ClarifyDialog = lazy(() => import("./writing-chat/ClarifyDialog"));

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
    toolStatus,
    pendingTool,
    confirmPendingTool,
    cancelPendingTool,
    pendingClarification,
    submitClarification,
    cancelClarification,
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
  const visibleMessages = messages.filter(isVisibleChatMessage);

  const messageActions = useMemo(
    () => ({
      generateEdit,
      applyEdit,
      deleteMessage,
      editingMessageId,
      applyingMessageId,
      canEdit,
    }),
    [
      generateEdit,
      applyEdit,
      deleteMessage,
      editingMessageId,
      applyingMessageId,
      canEdit,
    ],
  );

  return (
    <div className="min-h-screen p-4 md:p-8">
      <div className="max-w-7xl mx-auto">
        <PageHeader title={t("pages:writingChat.title")} groupKey="aiAssist">
          {compressed && (
            <span className="text-xs px-2 py-1 rounded bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/30">
              {t("pages:writingChat.compressedToast")}
            </span>
          )}
        </PageHeader>
      </div>

      {!projectPath || texFiles.length === 0 ? (
        <div className="max-w-7xl mx-auto">
          <EmptyState
            icon={FolderOpen}
            title={t("pages:writingChat.noProjectTitle")}
            className="mt-12"
          >
            <Button nativeButton={false} render={<Link to="/workspace" />}>
              {t("common:openWorkspace")}
            </Button>
          </EmptyState>
        </div>
      ) : (
        <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-[200px_1fr_2fr] gap-4">
          {/* 左：会话列表 */}
          <Card className="h-[calc(100vh-160px)] min-h-[400px] flex flex-col">
            <CardHeader className="pb-2 flex items-center justify-between">
              <CardTitle className="text-sm">
                {t("pages:writingChat.sessions")}
              </CardTitle>
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
                        s.id === currentSessionId ? "bg-muted font-medium" : ""
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
                              if (renameTitle.trim())
                                renameSession(s.id, renameTitle.trim());
                              setRenamingId(null);
                            }
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                          onBlur={() => {
                            if (renameTitle.trim())
                              renameSession(s.id, renameTitle.trim());
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
                <TextAlignStart className="size-3.5" />
                {t("pages:writingChat.contextAnchor")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 flex-1 min-h-0 overflow-y-auto">
              <Select value={activeFile || ""} onValueChange={handleFileChange}>
                <SelectTrigger className="w-full">
                  <SelectValue
                    placeholder={t("pages:writingChat.selectFile")}
                  />
                </SelectTrigger>
                <SelectContent>
                  {texFiles.map((f) => (
                    <SelectItem key={f.path} value={f.path}>
                      {f.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* 上下文锚点状态指示 + 关闭按钮 */}
              {activeFile && (
                <div className="flex items-center gap-1 text-xs">
                  {paragraphIndex === -1 ? (
                    <span className="text-muted-foreground italic">
                      {t("pages:writingChat.contextClosed")}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
                      <TextAlignStart className="size-3 shrink-0" />
                      <span className="truncate max-w-[120px]">
                        {paragraphIndex == null
                          ? t("pages:writingChat.anchorFullText")
                          : t("pages:writingChat.anchorParagraph", {
                              index: paragraphIndex,
                            })}
                      </span>
                      <button
                        type="button"
                        className="ml-0.5 rounded-full hover:bg-primary/20 p-0.5 transition-colors"
                        title={t("pages:writingChat.dismissAnchor")}
                        onClick={() => setParagraphIndex(-1)}
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  )}
                </div>
              )}

              <Select
                value={
                  paragraphIndex === null
                    ? "all"
                    : paragraphIndex === -1
                      ? "none"
                      : String(paragraphIndex)
                }
                onValueChange={(v) => {
                  if (v === "none") setParagraphIndex(-1);
                  else if (v === "all") setParagraphIndex(null);
                  else setParagraphIndex(parseInt(v, 10));
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue
                    placeholder={t("pages:writingChat.selectParagraph")}
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">
                    {t("pages:writingChat.noContext")}
                  </SelectItem>
                  <SelectItem value="all">
                    {t("pages:writingChat.allParagraphs")}
                  </SelectItem>
                  {paragraphs.map((p) => (
                    <SelectItem key={p.index} value={String(p.index)}>
                      P{p.index} ·{" "}
                      {(p.text || "").slice(0, 30).replace(/\n/g, " ")}
                      {(p.text || "").length > 30 ? "..." : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <div className="text-xs text-muted-foreground">
                <p>{t("pages:writingChat.assistantHintTitle")}</p>
                <div className="mt-2 pl-2 space-y-1">
                  <p>{t("pages:writingChat.assistantHint1")}</p>
                  <p>{t("pages:writingChat.assistantHint4")}</p>
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
                      variant={
                        activeAssistant === a.name ? "default" : "outline"
                      }
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
                        if (
                          !newAssistantName.trim() ||
                          !newAssistantContent.trim()
                        )
                          return;
                        saveAssistant(
                          newAssistantName.trim(),
                          newAssistantContent.trim(),
                        );
                        setNewAssistantName("");
                        setNewAssistantContent("");
                      }}
                      disabled={
                        !newAssistantName.trim() || !newAssistantContent.trim()
                      }
                    >
                      <Plus className="size-3" />
                      {t("pages:writingChat.saveAssistant")}
                    </Button>
                    {activeAssistant && (
                      <span className="text-xs text-muted-foreground">
                        {t("pages:writingChat.activeAssistant", {
                          name: activeAssistant,
                        })}
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
                {visibleMessages.length === 0 && !streaming && (
                  <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                    {currentSessionId
                      ? t("pages:writingChat.emptySession")
                      : t("pages:writingChat.emptySessionHint")}
                  </div>
                )}
                {visibleMessages.map((m) => (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    actions={messageActions}
                  />
                ))}
              </div>

              {error && (
                <p className="text-xs text-destructive py-1">{error}</p>
              )}

              <ToolStatusBar toolStatus={toolStatus} />

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
                    <ArrowUp className="size-4" />
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 工具确认弹窗（写操作：edit_paragraph / undo_last_edit） */}
      <Suspense fallback={null}>
        <ToolConfirmDialog
          pendingTool={pendingTool}
          onConfirm={confirmPendingTool}
          onCancel={cancelPendingTool}
        />
      </Suspense>

      {/* 验证码弹窗 */}
      <CaptchaDialog
        challenge={captchaChallenge}
        submitting={captchaSubmitting}
        onSubmit={submitCaptcha}
        onDismiss={dismissCaptcha}
      />

      {/* 澄清请求弹窗（LLM 向用户提问） */}
      <Suspense fallback={null}>
        <ClarifyDialog
          pendingClarification={pendingClarification}
          onSubmit={submitClarification}
          onCancel={cancelClarification}
        />
      </Suspense>
    </div>
  );
}
