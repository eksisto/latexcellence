import { useState } from "react";
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
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import {
  ArrowLeft,
  AlignLeft,
  Eye,
  Loader2,
  ListChecks,
} from "lucide-react";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";

const CHANGE_TYPE_LABELS = {
  spacing: "间距",
  environment_alignment: "环境对齐",
  code_formatting: "代码格式",
  consistency: "一致性",
};

export default function FormattingPage() {
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const {
    projectPath,
    activeFile,
    texFiles,
    paragraphs: formattingParagraphs,
    results: formattingResults,
    isLoading,
    progress,
    restoring,
    applyingAll,
    error,
    message,
    resolveInput,
    setResolveInput,
    activeFiles,
    handlePreview,
    handleCheck,
    handleApplyChange,
    handleApplyAll,
    handleIgnoreChange,
    handleFileChange,
  } = useParagraphCheck({
    apiModule: api.formatting,
    checkMethodName: "check",
    correctedTextField: "corrected",
    storeSlice: {
      paragraphsKey: "formattingParagraphs",
      setParagraphsKey: "setFormattingParagraphs",
      resultsKey: "formattingResults",
      setResultsKey: "setFormattingResults",
      loadingKey: "formatting",
    },
    messages: {
      checkLoading: "正在使用 LLM 获取格式化建议...",
      checkSuccess: (count) => `发现 ${count} 个段落有格式建议`,
      applySuccess: (idx) => `已应用段落 ${idx} 的格式修改`,
      applyAllSuccess: (count) => `已批量应用 ${count} 个段落的格式修改`,
      applyAllEmpty: "没有可应用的格式修改",
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
  } = useResultFilter(formattingResults, { idPrefix: "fmt-result" });

  const paragraphGroups = groupByFile(formattingParagraphs);
  const resultGroups = groupByFile(filteredResults);

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="icon"
            nativeButton={false}
            render={<Link to="/" />}
          >
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">源码格式化</h1>
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
                  先转到项目工作区，打开一个项目，然后选择要排版的 .tex 文件
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button
                  className="w-full"
                  nativeButton={false}
                  render={<Link to="/workspace" />}
                >
                  打开工作区
                </Button>
              </CardContent>
            </Card>
          </div>
        ) : (
          <div className="space-y-4">
            {/* file selector + controls */}
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
                    <Label
                      htmlFor="resolve-input"
                      className="text-sm cursor-pointer"
                    >
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
                      isLoading || formattingParagraphs.length === 0
                    }
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <AlignLeft />
                    )}
                    {isLoading ? "LLM 请求中..." : "开始"}
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
              />
            )}

            {/* paragraph preview - raw LaTeX source */}
            {formattingParagraphs.length > 0 &&
              formattingResults.length === 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      段落（{formattingParagraphs.length}）
                    </CardTitle>
                    <CardDescription>
                      原始 LaTeX 源码（将交由 LLM 排版优化）
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {activeFiles.length > 1 ? (
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
                              {(paragraphGroups[f] || []).map((p) => (
                                <div
                                  key={p.index}
                                  className="rounded-lg border border-border p-3"
                                >
                                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                                    P{p.index}
                                  </span>
                                  <pre className="text-sm text-foreground/80 mt-1 max-h-40 overflow-auto font-mono leading-relaxed whitespace-pre-wrap break-words bg-muted/30 rounded p-2">
                                    {p.text || "（空白）"}
                                  </pre>
                                </div>
                              ))}
                            </div>
                          </TabsContent>
                        ))}
                      </Tabs>
                    ) : (
                      <div className="space-y-3">
                        {formattingParagraphs.map((p) => (
                          <div
                            key={p.index}
                            className="rounded-lg border border-border p-3"
                          >
                            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                              P{p.index}
                            </span>
                            <pre className="text-sm text-foreground/80 mt-1 max-h-40 overflow-auto font-mono leading-relaxed whitespace-pre-wrap break-words bg-muted/30 rounded p-2">
                              {p.text || "（空白）"}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

            {/* formatting results */}
            {formattingResults.length > 0 && (
              <div className="space-y-4">
                {formattingResults.some(
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
                  typeLabels={CHANGE_TYPE_LABELS}
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    没有匹配的格式建议
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
                        <div className="space-y-3">
                          {(resultGroups[f] || []).map((result) => {
                            const hasChanges =
                              result.changes && result.changes.length > 0;
                            return (
                              <Card
                                key={`${result.file_path}-fmt-result-P${result.paragraph_index}`}
                                id={`fmt-result-P${result.paragraph_index}`}
                              >
                                <CardHeader className="pb-2">
                                  <div className="flex items-center justify-between">
                                    <CardTitle className="text-sm">
                                      段落 {result.paragraph_index}
                                    </CardTitle>
                                    {hasChanges && (
                                      <span className="rounded bg-primary/10 text-primary px-1.5 py-0.5 text-xs font-medium">
                                        {result.changes.length} 处排版建议
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
                                                <span className="rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 px-1 py-0.5 text-xs font-medium">
                                                  {CHANGE_TYPE_LABELS[
                                                    change.type
                                                  ] || change.type}
                                                </span>
                                                {change.description && (
                                                  <span className="text-muted-foreground text-xs">
                                                    {change.description}
                                                  </span>
                                                )}
                                              </div>
                                              <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                                                <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                                  原文：
                                                </span>
                                                <pre className="line-through text-foreground/60 break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                                                  {change.original}
                                                </pre>
                                                <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                                  排版后：
                                                </span>
                                                <pre className="font-medium break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                                                  {change.corrected}
                                                </pre>
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
                                                result.corrected,
                                                result.file_path
                                              )
                                            }
                                          >
                                            应用排版
                                          </Button>
                                          <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() =>
                                              handleIgnoreChange(
                                                result.paragraph_index,
                                                result.file_path
                                              )
                                            }
                                          >
                                            忽略
                                          </Button>
                                        </div>
                                      </>
                                    ) : (
                                      <p className="text-sm text-muted-foreground">
                                        此段落无需排版调整。
                                      </p>
                                    )}
                                    <ParagraphContext
                                      currentIndex={result.paragraph_index}
                                      paragraphs={formattingParagraphs}
                                      currentText={
                                        result.text || result.original
                                      }
                                    />
                                  </div>
                                </CardContent>
                              </Card>
                            );
                          })}
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>
                ) : (
                  filteredResults.map((result) => {
                    const hasChanges =
                      result.changes && result.changes.length > 0;
                    return (
                      <Card
                        key={`${result.file_path}-fmt-result-P${result.paragraph_index}`}
                        id={`fmt-result-P${result.paragraph_index}`}
                      >
                        <CardHeader className="pb-2">
                          <div className="flex items-center justify-between">
                            <CardTitle className="text-sm">
                              段落 {result.paragraph_index}
                            </CardTitle>
                            {hasChanges && (
                              <span className="rounded bg-primary/10 text-primary px-1.5 py-0.5 text-xs font-medium">
                                {result.changes.length} 处排版建议
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
                                        <span className="rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 px-1 py-0.5 text-xs font-medium">
                                          {CHANGE_TYPE_LABELS[
                                            change.type
                                          ] || change.type}
                                        </span>
                                        {change.description && (
                                          <span className="text-muted-foreground text-xs">
                                            {change.description}
                                          </span>
                                        )}
                                      </div>
                                      <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                                        <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                          原文：
                                        </span>
                                        <pre className="line-through text-foreground/60 break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                                          {change.original}
                                        </pre>
                                        <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                                          排版后：
                                        </span>
                                        <pre className="font-medium break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                                          {change.corrected}
                                        </pre>
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
                                        result.corrected,
                                        result.file_path
                                      )
                                    }
                                  >
                                    应用排版
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() =>
                                      handleIgnoreChange(
                                        result.paragraph_index,
                                        result.file_path
                                      )
                                    }
                                  >
                                    忽略
                                  </Button>
                                </div>
                              </>
                            ) : (
                              <p className="text-sm text-muted-foreground">
                                此段落无需排版调整。
                              </p>
                            )}
                            <ParagraphContext
                              currentIndex={result.paragraph_index}
                              paragraphs={formattingParagraphs}
                              currentText={result.text || result.original}
                            />
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmApplyAllOpen}
        onOpenChange={setConfirmApplyAllOpen}
        title="全部应用格式修改？"
        description="将一次性把所有有格式建议的段落修改写回源文件。建议在应用前逐段审核。"
        confirmText="全部应用"
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAll();
        }}
      />
    </div>
  );
}
