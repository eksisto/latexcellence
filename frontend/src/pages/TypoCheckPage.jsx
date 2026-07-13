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
import { ArrowLeft, CheckCheck, Eye, Loader2, ListChecks } from "lucide-react";
import { api } from "@/api/index";
import useParagraphCheck from "@/hooks/useParagraphCheck";
import MarkerText from "@/components/MarkerText";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";

export default function TypoCheckPage() {
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const {
    projectPath,
    activeFile,
    texFiles,
    paragraphs: typoParagraphs,
    results: typoResults,
    isLoading,
    progress,
    restoring,
    applyingAll,
    error,
    message,
    handlePreview,
    handleCheck,
    handleApplyChange,
    handleApplyAll,
    handleFileChange,
  } = useParagraphCheck({
    apiModule: api.typoCheck,
    checkMethodName: "check",
    correctedTextField: "corrected",
    storeSlice: {
      paragraphsKey: "typoParagraphs",
      setParagraphsKey: "setTypoParagraphs",
      resultsKey: "typoResults",
      setResultsKey: "setTypoResults",
      loadingKey: "typoCheck",
    },
    messages: {
      checkLoading: "正在检查错别字...",
      checkSuccess: (count) => `发现 ${count} 个段落可能有错字`,
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
  } = useResultFilter(typoResults, { idPrefix: "typo-result" });

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">文本校对</h1>
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
                      isLoading || typoParagraphs.length === 0
                    }
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <CheckCheck />
                    )}
                    {isLoading ? "检查中..." : "检查错字"}
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
              <ProgressBar current={progress.current} total={progress.total} />
            )}

            {/* paragraph preview */}
            {typoParagraphs.length > 0 && typoResults.length === 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    段落 ({typoParagraphs.length})
                  </CardTitle>
                  <CardDescription>
                    剥离 LaTeX 后的纯文本（将交由 LLM 检查）
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {typoParagraphs.map((p) => (
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
                </CardContent>
              </Card>
            )}

            {/* typo check results */}
            {typoResults.length > 0 && (
              <div className="space-y-4">
                {typoResults.some((r) => r.changes && r.changes.length > 0) && (
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
                    没有匹配的检查结果
                  </p>
                ) : (
                  filteredResults.map((result) => {
                  const hasChanges =
                    result.changes && result.changes.length > 0;
                  return (
                    <Card key={result.paragraph_index} id={`typo-result-P${result.paragraph_index}`}>
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
                                      <span className="rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 px-1 py-0.5 text-xs font-medium">
                                        {change.type}
                                      </span>
                                      {change.explanation && (
                                        <span className="text-muted-foreground text-xs">
                                          {change.explanation}
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
                                        <MarkerText text={change.corrected} />
                                      </span>
                                    </div>
                                  </li>
                                ))}
                              </ul>
                              <Button
                                size="sm"
                                onClick={() =>
                                  handleApplyChange(
                                    result.paragraph_index,
                                    result.corrected
                                  )
                                }
                              >
                                应用修改
                              </Button>
                            </>
                          ) : (
                            <p className="text-sm text-muted-foreground">
                                此段落未发现错字。
                            </p>
                          )}
                          <ParagraphContext currentIndex={result.paragraph_index} paragraphs={typoParagraphs} currentText={result.plain_text || result.original} />
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
        title="全部应用修改？"
        description="将一次性把所有有错字的段落修改写回源文件。建议在应用前逐段审核。"
        confirmText="全部应用"
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAll();
        }}
      />
    </div>
  );
}
