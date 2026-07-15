import { useState, useCallback, useEffect } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ArrowLeft, PencilLine, Eye, Loader2, Plus, Trash2, ListChecks } from "lucide-react";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import MarkerText from "@/components/MarkerText";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";

export default function ExpressionOptPage() {
  const {
    projectPath,
    activeFile,
    texFiles,
    paragraphs: expressionParagraphs,
    results: expressionResults,
    isLoading,
    progress,
    restoring,
    applyingAll,
    error,
    isPaused,
    message,
    handlePreview,
    handleCheck: handleOptimize,
    handlePauseResume,
    handleStop,
    handleApplyChange,
    handleApplyAll,
    handleIgnoreChange,
    handleFileChange,
    resolveInput,
    setResolveInput,
    activeFiles,
  } = useParagraphCheck({
    apiModule: api.expressionOpt,
    checkMethodName: "optimize",
    correctedTextField: "optimized",
    storeSlice: {
      paragraphsKey: "expressionParagraphs",
      setParagraphsKey: "setExpressionParagraphs",
      resultsKey: "expressionResults",
      setResultsKey: "setExpressionResults",
      loadingKey: "expressionOpt",
    },
    messages: {
      checkLoading: "正在优化...",
      checkSuccess: (count) => `找到 ${count} 个有优化建议的段落`,
      applySuccess: (idx) => `已应用第 ${idx} 段的优化建议`,
      applyAllSuccess: (count) => `已批量应用 ${count} 个段落的优化`,
      applyAllEmpty: "没有可应用的优化建议",
    },
  });

  const {
    filteredResults,
    searchQuery,
    setSearchQuery,
    typeFilter,
    setTypeFilter,
    availableTypes,
    jumpToParagraph,
    reset,
    hasFilters,
  } = useResultFilter(expressionResults, { idPrefix: "expr-result" });

  const [promptError, setPromptError] = useState(null);
  const [promptMessage, setPromptMessage] = useState(null);
  const [confirmDeletePrompt, setConfirmDeletePrompt] = useState(null);
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);

  // prompt management
  const [prompts, setPrompts] = useState([]);
  const [activePrompt, setActivePrompt] = useState("");
  const [newPromptName, setNewPromptName] = useState("");
  const [editingPrompt, setEditingPrompt] = useState(null);
  const [promptContent, setPromptContent] = useState("");
  const [previewContent, setPreviewContent] = useState("");

  // ── load prompts on mount ──────────────────────────────────────────────

  useEffect(() => {
    api.expressionOpt.prompts.list().then((data) => {
      setPrompts(data.prompts || []);
      const active = (data.prompts || []).find((p) => p.active);
      if (active) setActivePrompt(active.name);
    }).catch(() => {});
  }, []);

  // ── prompt actions ─────────────────────────────────────────────────────

  const handleCreatePrompt = useCallback(async () => {
    const name = newPromptName.trim();
    if (!name || !promptContent.trim()) return;
    try {
      await api.expressionOpt.prompts.save(name, promptContent);
      const data = await api.expressionOpt.prompts.list();
      setPrompts(data.prompts || []);
      setNewPromptName("");
      setPromptContent("");
      setEditingPrompt(name);
      setPromptError(null);
      toast.success("提示词已保存", { description: name });
    } catch (e) {
      setPromptError(e.message);
      toast.error("保存提示词失败", { description: e.message });
    }
  }, [newPromptName, promptContent]);

  const handleDeletePrompt = useCallback(async (name) => {
    try {
      await api.expressionOpt.prompts.delete(name);
      const data = await api.expressionOpt.prompts.list();
      setPrompts(data.prompts || []);
      if (editingPrompt === name) {
        setEditingPrompt(null);
        setPromptContent("");
      }
      if (activePrompt === name) setActivePrompt("");
      setPromptError(null);
      toast.success("提示词已删除", { description: name });
    } catch (e) {
      setPromptError(e.message);
      toast.error("删除提示词失败", { description: e.message });
    }
  }, [editingPrompt, activePrompt]);

  const handleSetActivePrompt = useCallback(async (name) => {
    try {
      await api.expressionOpt.prompts.setActive(name);
      setActivePrompt(name);
      setPromptMessage(name ? `当前启用提示词：${name}` : "使用默认提示词");
      setPromptError(null);
      toast.success(name ? `已启用提示词：${name}` : "已切换为默认提示词");
    } catch (e) {
      setPromptError(e.message);
      toast.error("切换提示词失败", { description: e.message });
    }
  }, []);

  const handleEditPrompt = useCallback(async (name) => {
    try {
      const data = await api.expressionOpt.prompts.load(name);
      setEditingPrompt(name);
      setPromptContent(data.content || "");
      setPromptError(null);
    } catch (e) {
      setPromptError(e.message);
      toast.error("加载提示词失败", { description: e.message });
    }
  }, []);

  useEffect(() => {
    if (!activePrompt) {
      setPreviewContent("");
      return;
    }
    let cancelled = false;
    api.expressionOpt.prompts
      .load(activePrompt)
      .then((data) => {
        if (!cancelled) setPreviewContent(data.content || "");
      })
      .catch(() => {
        if (!cancelled) setPreviewContent("");
      });
    return () => {
      cancelled = true;
    };
  }, [activePrompt]);

  const paragraphsByFile = activeFiles.length > 1 ? groupByFile(expressionParagraphs) : [];
  const resultsByFile = activeFiles.length > 1 ? groupByFile(expressionResults) : [];

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">表达优化</h1>
        </div>
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-md w-full">
              <CardHeader>
                <CardTitle>正在恢复项目...</CardTitle>
              </CardHeader>
              <CardContent className="flex justify-center">
                <Loader2 className="animate-spin size-6" />
              </CardContent>
            </Card>
          </div>
        ) : !projectPath || texFiles.length === 0 ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-md w-full">
              <CardHeader>
                <CardTitle>未选择文件</CardTitle>
                <CardDescription>
                  先转到项目工作区，打开一个项目，然后选择要优化的 .tex 文件
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button className="w-full" nativeButton={false} render={<Link to="/workspace" />}>
                  打开工作区
                </Button>
              </CardContent>
            </Card>
          </div>
        ) : (
          <div className="space-y-4">
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center gap-3">
                  <Select
                    value={activeFile || ""}
                    onValueChange={handleFileChange}
                  >
                    <SelectTrigger className="min-w-60">
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
                  <div className="flex items-center gap-2 ml-auto">
                    <Switch
                      id="resolve-input"
                      checked={resolveInput}
                      onCheckedChange={setResolveInput}
                    />
                    <Label htmlFor="resolve-input" className="text-sm cursor-pointer">
                      包含子文件（展开 \input）
                    </Label>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-3">
                  <Button
                    onClick={handlePreview}
                    disabled={isLoading || !activeFile}
                    variant="outline"
                  >
                    <Eye />
                    加载并预览
                  </Button>
                  <Button
                    onClick={handleOptimize}
                    disabled={isLoading || expressionParagraphs.length === 0}
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <PencilLine />
                    )}
                    {isLoading ? "正在优化..." : "开始优化"}
                  </Button>
                </div>
                {message && (
                  <p className="text-sm text-muted-foreground mt-2">
                    {message}
                  </p>
                )}
              </CardContent>
            </Card>

            {progress && (
              <ProgressBar
                current={progress.current}
                total={progress.total}
                fileName={progress.file_path}
                isPaused={isPaused}
                onPauseToggle={handlePauseResume}
                onStop={handleStop}
              />
            )}

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">优化提示词</CardTitle>
                <CardDescription>
                  选择或自定义 LLM 的优化策略
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  {prompts.map((p) => (
                    <div key={p.name} className="flex items-center gap-1">
                      <Button
                        variant={activePrompt === p.name ? "default" : "outline"}
                        size="sm"
                        onClick={() =>
                          handleSetActivePrompt(
                            activePrompt === p.name ? "" : p.name
                          )
                        }
                      >
                        {p.name}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6"
                        onClick={() => handleEditPrompt(p.name)}
                      >
                        <PencilLine className="size-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6"
                        onClick={() => setConfirmDeletePrompt(p.name)}
                      >
                        <Trash2 className="size-3" />
                      </Button>
                    </div>
                  ))}
                </div>
                {prompts.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    暂无自定义提示词，请在下方创建一个
                  </p>
                )}

                {activePrompt && previewContent && (
                  <div className="rounded-md border border-border bg-muted/30 p-3">
                    <span className="text-xs font-medium text-muted-foreground">
                      当前启用提示词：{activePrompt}
                    </span>
                    <pre className="whitespace-pre-wrap text-sm text-foreground/80 font-sans leading-relaxed max-h-48 overflow-y-auto mt-2">
                      {previewContent}
                    </pre>
                  </div>
                )}

                {editingPrompt && (
                  <p className="text-xs text-muted-foreground">
                    正在编辑：{editingPrompt}
                  </p>
                )}

                <div className="flex items-end gap-2">
                  <div className="flex-1 space-y-1">
                    <Label htmlFor="prompt-name" className="text-xs">
                      名称
                    </Label>
                    <Input
                      id="prompt-name"
                      className="h-7 text-sm"
                      value={newPromptName}
                      onChange={(e) => setNewPromptName(e.target.value)}
                      placeholder="例如：academic-formal"
                    />
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleCreatePrompt}
                    disabled={!newPromptName.trim() || !promptContent.trim()}
                  >
                    <Plus />
                    保存
                  </Button>
                </div>
                <Textarea
                  className="min-h-24 resize-none text-sm"
                  value={promptContent}
                  onChange={(e) => setPromptContent(e.target.value)}
                  placeholder="输入给 LLM 的自定义提示词说明..."
                />
                {promptMessage && (
                  <p className="text-sm text-muted-foreground mt-2">
                    {promptMessage}
                  </p>
                )}
                {promptError && (
                  <p className="text-sm text-destructive mt-2">{promptError}</p>
                )}
              </CardContent>
            </Card>

            {expressionParagraphs.length > 0 && expressionResults.length === 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    段落（{expressionParagraphs.length}）
                  </CardTitle>
                  <CardDescription>
                    去除 LaTeX 标记后的纯文本
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {activeFiles.length > 1 ? (
                    <Tabs defaultValue={activeFiles[0]}>
                      <TabsList>
                        {activeFiles.map((f) => (
                          <TabsTrigger key={f} value={f}>{f.split('/').pop()}</TabsTrigger>
                        ))}
                      </TabsList>
                      {activeFiles.map((f) => (
                        <TabsContent key={f} value={f}>
                          <div className="space-y-3">
                            {(paragraphsByFile[f] || []).map((p) => (
                              <div
                                key={p.index}
                                className="rounded-lg border border-border p-3"
                              >
                                <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                                  P{p.index}
                                </span>
                                <div className="whitespace-pre-wrap text-sm text-foreground/80 mt-1 max-h-32 overflow-y-auto font-sans leading-relaxed">
                                  {p.plain_text ? <MarkerText text={p.plain_text} /> : "（空白）"}
                                </div>
                              </div>
                            ))}
                          </div>
                        </TabsContent>
                      ))}
                    </Tabs>
                  ) : (
                    <div className="space-y-3">
                      {expressionParagraphs.map((p) => (
                        <div
                          key={p.index}
                          className="rounded-lg border border-border p-3"
                        >
                          <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                            P{p.index}
                          </span>
                          <div className="whitespace-pre-wrap text-sm text-foreground/80 mt-1 max-h-32 overflow-y-auto font-sans leading-relaxed">
                            {p.plain_text ? <MarkerText text={p.plain_text} /> : "（空白）"}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {expressionResults.length > 0 && (
              <div className="space-y-4">
                {expressionResults.some(
                  (r) => r.changes && r.changes.length > 0
                ) && (
                  <div className="flex justify-end">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setConfirmApplyAllOpen(true)}
                      disabled={applyingAll || isLoading}
                    >
                      {applyingAll ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <ListChecks />
                      )}
                      全部应用
                    </Button>
                  </div>
                )}
                <ResultFilterBar
                  searchQuery={searchQuery}
                  setSearchQuery={setSearchQuery}
                  typeFilter={typeFilter}
                  setTypeFilter={setTypeFilter}
                  availableTypes={availableTypes}
                  onJump={jumpToParagraph}
                  onReset={reset}
                  hasFilters={hasFilters}
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    没有匹配的优化结果
                  </p>
                ) : (
                  activeFiles.length > 1 ? (
                    <Tabs defaultValue={activeFiles[0]}>
                      <TabsList>
                        {activeFiles.map((f) => (
                          <TabsTrigger key={f} value={f}>{f.split('/').pop()}</TabsTrigger>
                        ))}
                      </TabsList>
                      {activeFiles.map((f) => (
                        <TabsContent key={f} value={f}>
                          <div className="space-y-4">
                            {(resultsByFile[f] || []).length === 0 ? (
                              <p className="text-sm text-muted-foreground text-center py-8">
                                该文件没有优化结果
                              </p>
                            ) : (
                              (resultsByFile[f] || []).map((result) => {
                                const hasChanges =
                                  result.changes && result.changes.length > 0;
                                return (
                                  <Card key={`${result.file_path}-expr-result-P${result.paragraph_index}`} id={`expr-result-P${result.paragraph_index}`}>
                                    <CardHeader className="pb-2">
                                      <div className="flex items-center justify-between">
                                        <CardTitle className="text-sm">
                                          第 {result.paragraph_index} 段
                                        </CardTitle>
                                        {hasChanges && (
                                          <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                                            {result.changes.length} 条建议
                                          </span>
                                        )}
                                      </div>
                                    </CardHeader>
                                    <CardContent>
                                      <div className="space-y-3">
                                        {hasChanges ? (
                                          <>
                                            <ul className="space-y-2">
                                              {result.changes.map((change, i) => (
                                                <li
                                                  key={i}
                                                  className="rounded border border-border p-2 text-sm"
                                                >
                                                  <div className="flex items-center gap-2 mb-1">
                                                    <span className="rounded bg-green-500/10 text-green-600 dark:text-green-400 px-1 py-0.5 text-xs font-medium">
                                                      {change.type}
                                                    </span>
                                                    {change.reason && (
                                                      <span className="text-muted-foreground text-xs">
                                                        {change.reason}
                                                      </span>
                                                    )}
                                                  </div>
                                                  <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                                                    <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                                      原文：
                                                    </span>
                                                    <span className="line-through text-foreground/60 break-all mt-0.5">
                                                      <MarkerText text={change.original} />
                                                    </span>
                                                    <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                                      修改后：
                                                    </span>
                                                    <span className="font-medium break-all mt-0.5">
                                                      <MarkerText text={change.optimized} />
                                                    </span>
                                                  </div>
                                                </li>
                                              ))}
                                            </ul>
                                            <div className="flex items-center gap-2">
                                              <Button
                                                size="sm"
                                                onClick={() =>
                                                  handleApplyChange(
                                                    result.paragraph_index,
                                                    result.optimized,
                                                    result.file_path
                                                  )
                                                }
                                              >
                                                应用优化
                                              </Button>
                                              <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() =>
                                                  handleIgnoreChange(result.paragraph_index, result.file_path)
                                                }
                                              >
                                                忽略
                                              </Button>
                                            </div>
                                          </>
                                        ) : (
                                          <p className="text-sm text-muted-foreground">
                                            当前段落没有优化建议
                                          </p>
                                        )}
                                        <ParagraphContext currentIndex={result.paragraph_index} paragraphs={expressionParagraphs} currentText={result.plain_text || result.original} />
                                      </div>
                                    </CardContent>
                                  </Card>
                                );
                              })
                            )}
                          </div>
                        </TabsContent>
                      ))}
                    </Tabs>
                  ) : (
                    filteredResults.map((result) => {
                      const hasChanges =
                        result.changes && result.changes.length > 0;
                      return (
                        <Card key={result.paragraph_index} id={`expr-result-P${result.paragraph_index}`}>
                          <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                              <CardTitle className="text-sm">
                                第 {result.paragraph_index} 段
                              </CardTitle>
                              {hasChanges && (
                                <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                                  {result.changes.length} 条建议
                                </span>
                              )}
                            </div>
                          </CardHeader>
                          <CardContent>
                            <div className="space-y-3">
                              {hasChanges ? (
                                <>
                                  <ul className="space-y-2">
                                    {result.changes.map((change, i) => (
                                      <li
                                        key={i}
                                        className="rounded border border-border p-2 text-sm"
                                      >
                                        <div className="flex items-center gap-2 mb-1">
                                          <span className="rounded bg-green-500/10 text-green-600 dark:text-green-400 px-1 py-0.5 text-xs font-medium">
                                            {change.type}
                                          </span>
                                          {change.reason && (
                                            <span className="text-muted-foreground text-xs">
                                              {change.reason}
                                            </span>
                                          )}
                                        </div>
                                        <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                                          <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                            原文：
                                          </span>
                                          <span className="line-through text-foreground/60 break-all mt-0.5">
                                            <MarkerText text={change.original} />
                                          </span>
                                          <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                            修改后：
                                          </span>
                                          <span className="font-medium break-all mt-0.5">
                                            <MarkerText text={change.optimized} />
                                          </span>
                                        </div>
                                      </li>
                                    ))}
                                  </ul>
                                  <div className="flex items-center gap-2">
                                    <Button
                                      size="sm"
                                      onClick={() =>
                                        handleApplyChange(
                                          result.paragraph_index,
                                          result.optimized,
                                          result.file_path
                                        )
                                      }
                                    >
                                      应用优化
                                    </Button>
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() =>
                                        handleIgnoreChange(result.paragraph_index, result.file_path)
                                      }
                                    >
                                      忽略
                                    </Button>
                                  </div>
                                </>
                              ) : (
                                <p className="text-sm text-muted-foreground">
                                  当前段落没有优化建议
                                </p>
                              )}
                              <ParagraphContext currentIndex={result.paragraph_index} paragraphs={expressionParagraphs} currentText={result.plain_text || result.original} />
                            </div>
                          </CardContent>
                        </Card>
                      );
                    })
                  )
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmDeletePrompt !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDeletePrompt(null);
        }}
        title="删除提示词？"
        description={`将永久删除提示词「${confirmDeletePrompt}」，此操作不可撤销。`}
        confirmText="删除"
        variant="destructive"
        onConfirm={async () => {
          if (confirmDeletePrompt) {
            await handleDeletePrompt(confirmDeletePrompt);
          }
          setConfirmDeletePrompt(null);
        }}
      />
      <ConfirmDialog
        open={confirmApplyAllOpen}
        onOpenChange={setConfirmApplyAllOpen}
        title="全部应用优化建议？"
        description="将一次性把所有有建议的段落修改写回源文件。建议在应用前逐段审核。"
        confirmText="全部应用"
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAll();
        }}
      />
    </div>
  );
}
