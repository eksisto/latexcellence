import { useState, useCallback } from "react";
import { Link } from "react-router-dom";
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
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ArrowLeft, CheckCheck, Eye, Loader2, ListChecks } from "lucide-react";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import MarkerText from "@/components/MarkerText";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";
import useStore from "@/store/useStore";
import { toast } from "sonner";

export default function TerminologyCheckPage() {
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const [customReplacements, setCustomReplacements] = useState({});
  const {
    projectPath,
    activeFile,
    texFiles,
    paragraphs: terminologyParagraphs,
    results: terminologyResults,
    isLoading,
    progress,
    restoring,
    applyingAll,
    error,
    isPaused,
    message,
    resolveInput,
    setResolveInput,
    activeFiles,
    workspaceId,
    handlePreview,
    handleCheck,
    handlePauseResume,
    handleStop,
    handleApplyChange,
    handleApplyAll,
    handleIgnoreChange,
    handleFileChange,
  } = useParagraphCheck({
    apiModule: api.terminologyCheck,
    checkMethodName: "check",
    correctedTextField: "corrected",
    storeSlice: {
      paragraphsKey: "terminologyParagraphs",
      setParagraphsKey: "setTerminologyParagraphs",
      resultsKey: "terminologyResults",
      setResultsKey: "setTerminologyResults",
      loadingKey: "terminologyCheck",
    },
    messages: {
      checkLoading: "正在检查术语一致性...",
      checkSuccess: (count) => `发现 ${count} 个段落存在术语问题`,
      applySuccess: (idx) => `已应用段落 ${idx} 的修改`,
      applyAllSuccess: (count) => `已批量应用 ${count} 个段落的修改`,
      applyAllEmpty: "没有可应用的修改",
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
  } = useResultFilter(terminologyResults, { idPrefix: "term-result" });

  const buildCorrectedText = useCallback((result, overrides) => {
    let corrected = result.original;
    const sorted = [...(result.changes || [])].sort(
      (a, b) => b.original.length - a.original.length
    );
    for (const change of sorted) {
      const key = `${result.paragraph_index}:${change.original}`;
      const replacement = overrides?.[key] ?? change.corrected;
      if (replacement && change.original !== replacement) {
        corrected = corrected.split(change.original).join(replacement);
      }
    }
    return corrected;
  }, []);

  const handleApplyWithCustom = useCallback(
    (result) => {
      const corrected = buildCorrectedText(result, customReplacements);
      handleApplyChange(result.paragraph_index, corrected, result.file_path);
    },
    [customReplacements, buildCorrectedText, handleApplyChange]
  );

  const handleApplyAllWithCustom = useCallback(async () => {
    const pending = terminologyResults.filter(
      (r) => r.changes && r.changes.length > 0
    );
    if (pending.length === 0) {
      toast.info("没有可应用的修改");
      return;
    }
    const toastId = toast.loading("正在批量应用修改...", {
      description: `共 ${pending.length} 个段落`,
    });
    try {
      const changes = pending.map((r) => ({
        paragraph_index: r.paragraph_index,
        new_text: buildCorrectedText(r, customReplacements),
        file_path: r.file_path || activeFile,
      }));
      await api.terminologyCheck.apply({
        file_path: activeFile,
        changes,
        operation_id: operationId?.current || null,
      }, workspaceId);
      const appliedIndices = new Set(pending.map((r) => r.paragraph_index));
      useStore.setState((state) => ({
        terminologyResults: state.terminologyResults.filter(
          (r) => !appliedIndices.has(r.paragraph_index)
        ),
      }));
      toast.success(`已批量应用 ${pending.length} 个段落的修改`, {
        id: toastId,
      });
    } catch (e) {
      toast.error("批量应用失败", {
        id: toastId,
        description: e.message,
      });
    }
  }, [terminologyResults, customReplacements, buildCorrectedText, activeFile]);

  const renderParagraphsByFile = (paragraphs) => {
    if (activeFiles.length > 1) {
      const groups = groupByFile(paragraphs);
      return (
        <Tabs defaultValue={activeFiles[0]}>
          <TabsList>
            {activeFiles.map((f) => (
              <TabsTrigger key={f} value={f}>
                {f.split("/").pop()}
              </TabsTrigger>
            ))}
          </TabsList>
          {activeFiles.map((f) => (
            <TabsContent key={f} value={f}>
              <div className="space-y-3">
                {(groups[f] || []).map((p) => (
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
      );
    }
    return (
      <div className="space-y-3">
        {paragraphs.map((p) => (
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
    );
  };

  const renderResultCard = (result) => {
    const hasChanges =
      result.changes && result.changes.length > 0;
    return (
      <Card key={`${result.file_path}-term-result-P${result.paragraph_index}`} id={`term-result-P${result.paragraph_index}`}>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">
              段落 {result.paragraph_index}
            </CardTitle>
            {hasChanges && (
              <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                {result.changes.length} 处问题
              </span>
            )}
          </div>
          {result.terms && result.terms.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {result.terms.map((t, i) => (
                <span
                  key={i}
                  className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {hasChanges ? (
              <>
                <ul className="space-y-2">
                  {result.changes.map((change, i) => {
                    const cKey = `${result.paragraph_index}:${change.original}`;
                    return (
                      <li
                        key={i}
                        className="rounded border border-border p-2 text-sm"
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className="rounded bg-orange-500/10 text-orange-600 dark:text-orange-400 px-1 py-0.5 text-xs font-medium">
                            术语不一致
                          </span>
                          {change.concept && (
                            <span className="text-muted-foreground text-xs font-mono">
                              → {change.concept}
                            </span>
                          )}
                        </div>
                        {change.explanation && (
                          <p className="text-xs text-muted-foreground mb-2">
                            {change.explanation}
                          </p>
                        )}
                        <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                          <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                            原文：
                          </span>
                          <span className="line-through text-foreground/60 break-all mt-0.5">
                            <MarkerText text={change.original} />
                          </span>
                          <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                            替换为：
                          </span>
                          <div className="mt-0.5 flex items-center gap-2">
                            <Input
                              type="text"
                              value={
                                customReplacements[cKey] ??
                                change.corrected
                              }
                              onChange={(e) =>
                                setCustomReplacements((prev) => ({
                                  ...prev,
                                  [cKey]: e.target.value,
                                }))
                              }
                              className="h-7 text-sm font-medium"
                            />
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => handleApplyWithCustom(result)}
                  >
                    应用修改
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
                此段落术语使用一致。
              </p>
            )}
            <ParagraphContext currentIndex={result.paragraph_index} paragraphs={terminologyParagraphs} currentText={result.plain_text || result.original} />
          </div>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">术语一致性检查</h1>
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
                  先转到项目工作区，打开一个项目，然后选择要检查的 .tex 文件
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
                      variant="rect"
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
                    onClick={handleCheck}
                    disabled={
                      isLoading || terminologyParagraphs.length === 0
                    }
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <CheckCheck />
                    )}
                    {isLoading ? "检查中..." : "检查术语一致性"}
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

            {terminologyParagraphs.length > 0 && terminologyResults.length === 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    段落 ({terminologyParagraphs.length})
                  </CardTitle>
                  <CardDescription>
                    剥离 LaTeX 后的纯文本（将交由 LLM 提取术语并检查一致性）
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {renderParagraphsByFile(terminologyParagraphs)}
                </CardContent>
              </Card>
            )}

            {terminologyResults.length > 0 && (
              <div className="space-y-4">
                {terminologyResults.some((r) => r.changes && r.changes.length > 0) && (
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
                  typeLabels={{ inconsistency: "术语不一致" }}
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    没有匹配的检查结果
                  </p>
                ) : activeFiles.length > 1 ? (
                  <Tabs defaultValue={activeFiles[0]}>
                    <TabsList>
                      {activeFiles.map((f) => (
                        <TabsTrigger key={f} value={f}>
                          {f.split("/").pop()}
                        </TabsTrigger>
                      ))}
                    </TabsList>
                    {activeFiles.map((f) => (
                      <TabsContent key={f} value={f}>
                        <div className="space-y-4">
                          {filteredResults
                            .filter((r) => (r.file_path || "") === f)
                            .map((result) => renderResultCard(result))}
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>
                ) : (
                  <div className="space-y-4">
                    {filteredResults.map((result) => renderResultCard(result))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmApplyAllOpen}
        onOpenChange={setConfirmApplyAllOpen}
        title="全部应用修改？"
        description="将一次性把所有术语问题的段落修改写回源文件。建议在应用前逐段审核。"
        confirmText="全部应用"
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAllWithCustom();
        }}
      />
    </div>
  );
}
