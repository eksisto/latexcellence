import { useState, useMemo, useCallback, useEffect } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
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
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { ArrowLeft, Eye, Loader2, ListChecks, Search } from "lucide-react";
import React from "react";
import { diffLines } from "diff";
import { cn } from "@/lib/utils";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";
import MarkerText from "@/components/MarkerText";

const severityBadgeClass = {
  info: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  warning: "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400",
  error: "bg-red-500/10 text-red-600 dark:text-red-400",
};

const CodeReviewResultCard = React.memo(function CodeReviewResultCard({
  result,
  onApply,
  onIgnore,
  paragraphs,
  typeLabels,
  severityLabels,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const hasSuggestions = result.suggestions && result.suggestions.length > 0;
  const hasFormattingDiff =
    result.new_text && result.new_text !== (result.text || result.original);
  const [showFormatted, setShowFormatted] = useState(
    hasFormattingDiff && !hasSuggestions,
  );
  const toggleFormatted = useCallback(() => setShowFormatted((v) => !v), []);
  const diffParts = useMemo(() => {
    const orig = result.text || result.original;
    const mod = result.new_text;
    if (!mod) return null;
    return diffLines(orig, mod);
  }, [result.text, result.original, result.new_text]);
  const [editedText, setEditedText] = useState(result.new_text || "");
  useEffect(() => {
    setEditedText(result.new_text || "");
  }, [result.new_text]);
  const handleApplySuggestion = useCallback((original, suggested) => {
    setEditedText((prev) => {
      const idx = prev.indexOf(original);
      if (idx === -1) return prev;
      return prev.slice(0, idx) + suggested + prev.slice(idx + original.length);
    });
  }, []);
  const handleResetEdit = useCallback(() => {
    setEditedText(result.new_text || "");
  }, [result.new_text]);
  return (
    <Card
      key={`${result.file_path}-cr-result-P${result.paragraph_index}`}
      id={`cr-result-P${result.paragraph_index}`}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">
            {t("pages:code_review.paragraph", {
              index: result.paragraph_index,
            })}
          </CardTitle>
          {hasSuggestions && (
            <span className="rounded bg-primary/10 text-primary px-1.5 py-0.5 text-xs font-medium">
              {t("pages:code_review.suggestionsCount", {
                count: result.suggestions.length,
              })}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {hasSuggestions ? (
            <>
              <ul className="space-y-2">
                {result.suggestions.map((suggestion, i) => (
                  <li
                    key={i}
                    className="rounded border border-border p-2 text-sm"
                  >
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 px-1 py-0.5 text-xs font-medium">
                        {typeLabels?.[suggestion.type] || suggestion.type}
                      </span>
                      <Badge
                        variant="outline"
                        className={`text-xs py-0 ${severityBadgeClass[suggestion.severity] || ""}`}
                      >
                        {severityLabels?.[suggestion.severity] ||
                          suggestion.severity}
                      </Badge>
                      {suggestion.description && (
                        <span className="text-muted-foreground text-xs">
                          {suggestion.description}
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                        {t("common:original")}
                      </span>
                      <pre className="line-through text-foreground/60 break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                        <MarkerText text={result.text || result.original} />
                      </pre>
                      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                        {t("pages:code_review.suggested")}
                      </span>
                      <pre className="font-medium break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                        <MarkerText text={suggestion.suggested} />
                      </pre>
                    </div>
                    <div className="mt-2 flex justify-end">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 text-xs"
                        onClick={() =>
                          handleApplySuggestion(
                            suggestion.original,
                            suggestion.suggested,
                          )
                        }
                      >
                        {t("pages:code_review.applySuggestion")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {hasFormattingDiff
                ? t("pages:code_review.formattingAvailable")
                : t("pages:code_review.noIssues")}
            </p>
          )}
          {result.new_text && (
            <div className="rounded border border-border">
              <button
                type="button"
                onClick={toggleFormatted}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium hover:bg-muted/50 cursor-pointer"
              >
                <svg
                  className={`size-4 shrink-0 transition-transform duration-300 ${showFormatted ? "rotate-90" : ""}`}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M9 18l6-6-6-6" />
                </svg>
                {showFormatted
                  ? t("pages:code_review.hideFormattedDiff")
                  : t("pages:code_review.showFormattedDiff")}
              </button>
              <div
                className={cn(
                  "grid transition-all duration-300 ease-in-out",
                  showFormatted && diffParts
                    ? "grid-rows-[1fr]"
                    : "grid-rows-[0fr]",
                )}
              >
                <div className="overflow-hidden">
                  <div className="border-t border-border">
                    {diffParts?.map((part, i) => {
                      const lines = part.value.split("\n");
                      return lines.map((line, j) => {
                        if (j === lines.length - 1 && line === "") return null;
                        return (
                          <div
                            key={`${i}-${j}`}
                            className={cn(
                              "flex px-3 py-px font-mono text-xs leading-relaxed",
                              part.added && "bg-green-500/10",
                              part.removed && "bg-red-500/10",
                            )}
                          >
                            <span
                              className={cn(
                                "w-5 shrink-0 select-none",
                                part.added &&
                                  "text-green-600 dark:text-green-400",
                                part.removed &&
                                  "text-red-600 dark:text-red-400",
                                !part.added &&
                                  !part.removed &&
                                  "text-muted-foreground/40",
                              )}
                            >
                              {part.added ? "+" : part.removed ? "−" : " "}
                            </span>
                            <span
                              className={cn(
                                "whitespace-pre-wrap break-all",
                                part.added &&
                                  "text-green-800 dark:text-green-200",
                                part.removed &&
                                  "text-red-800 dark:text-red-200",
                              )}
                            >
                              <MarkerText text={line} />
                            </span>
                          </div>
                        );
                      });
                    })}
                  </div>
                </div>
              </div>
            </div>
          )}
          {result.new_text && (
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                {t("pages:code_review.editFormatted")}
                <button
                  type="button"
                  onClick={handleResetEdit}
                  className="text-xs text-muted-foreground/60 hover:text-foreground underline cursor-pointer"
                >
                  {t("pages:code_review.resetEdit")}
                </button>
              </label>
              <textarea
                value={editedText}
                onChange={(e) => setEditedText(e.target.value)}
                className="w-full min-h-[120px] rounded border border-border bg-muted/20 p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap resize-y focus:outline-none focus:ring-1 focus:ring-ring"
                placeholder={t("pages:code_review.editPlaceholder")}
                spellCheck={false}
              />
            </div>
          )}
          {result.new_text &&
            editedText !== (result.text || result.original) && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() =>
                    onApply(
                      result.paragraph_index,
                      editedText,
                      result.file_path,
                    )
                  }
                >
                  {t("common:applyChange")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    onIgnore(result.paragraph_index, result.file_path)
                  }
                >
                  {t("common:ignore")}
                </Button>
              </div>
            )}
          <ParagraphContext
            currentIndex={result.paragraph_index}
            paragraphs={paragraphs}
            currentText={result.text || result.original}
          />
        </div>
      </CardContent>
    </Card>
  );
});

export default function CodeReviewPage() {
  const { t } = useTranslation(["pages"]);
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const {
    projectPath,
    activeFile,
    texFiles,
    paragraphs: codeReviewParagraphs,
    results: codeReviewResults,
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
    handlePreview,
    handleCheck,
    handlePauseResume,
    handleStop,
    handleApplyChange,
    handleApplyAll,
    handleIgnoreChange,
    handleFileChange,
  } = useParagraphCheck({
    apiModule: api.codeReview,
    checkMethodName: "check",
    correctedTextField: "new_text",
    /** code_review 额外将工具格式化差异视为"有问题"，使这些段落在结果中置顶并纳入统计 */
    hasResultIssue: (r) => {
      const hasChanges = r.changes && r.changes.length > 0;
      const hasSuggestions = r.suggestions && r.suggestions.length > 0;
      const corrected = r.new_text;
      const original = r.text || r.original;
      const hasFormatDiff = corrected && original && corrected !== original;
      return hasChanges || hasSuggestions || hasFormatDiff;
    },
    storeSlice: {
      paragraphsKey: "codeReviewParagraphs",
      setParagraphsKey: "setCodeReviewParagraphs",
      resultsKey: "codeReviewResults",
      setResultsKey: "setCodeReviewResults",
      loadingKey: "codeReview",
    },
    messages: {
      checkLoading: t("pages:code_review.checkLoading"),
      checkSuccess: (count) => t("pages:code_review.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:code_review.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:code_review.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:code_review.applyAllEmpty"),
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
  } = useResultFilter(codeReviewResults, { idPrefix: "cr-result" });

  const paragraphGroups = useMemo(
    () => groupByFile(codeReviewParagraphs),
    [codeReviewParagraphs],
  );
  const resultGroups = useMemo(
    () => groupByFile(filteredResults),
    [filteredResults],
  );

  const suggestionTypeLabels = useMemo(
    () => ({
      code_smell: t("pages:code_review.suggestionTypes.codeSmell"),
      modernization: t("pages:code_review.suggestionTypes.modernization"),
      best_practice: t("pages:code_review.suggestionTypes.bestPractice"),
      portability: t("pages:code_review.suggestionTypes.portability"),
      consistency: t("pages:code_review.suggestionTypes.consistency"),
    }),
    [t],
  );

  const severityLabels = useMemo(
    () => ({
      info: t("pages:code_review.severity.info"),
      warning: t("pages:code_review.severity.warning"),
      error: t("pages:code_review.severity.error"),
    }),
    [t],
  );

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
          <h1 className="text-3xl font-bold tracking-tight">
            {t("pages:code_review.title")}
          </h1>
        </div>
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-md w-full">
              <CardHeader>
                <CardTitle>{t("common:restoring")}</CardTitle>
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
                <CardTitle>{t("pages:code_review.noProjectTitle")}</CardTitle>
                <CardDescription>
                  {t("pages:code_review.noProjectDesc")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button
                  className="w-full"
                  nativeButton={false}
                  render={<Link to="/workspace" />}
                >
                  {t("common:openWorkspace")}
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
                      <SelectValue placeholder={t("common:selectTexFile")} />
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
                    <Label
                      htmlFor="resolve-input"
                      className="text-sm cursor-pointer"
                    >
                      {t("common:includeSubfiles")}
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
                    {t("common:loadAndPreview")}
                  </Button>
                  <Button
                    onClick={handleCheck}
                    disabled={isLoading || codeReviewParagraphs.length === 0}
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <Search />
                    )}
                    {isLoading
                      ? t("pages:code_review.llmRequesting")
                      : t("pages:code_review.start")}
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

            {/* paragraph preview */}
            {codeReviewParagraphs.length > 0 &&
              codeReviewResults.length === 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:code_review.paragraphs", {
                        count: codeReviewParagraphs.length,
                      })}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:code_review.paragraphsDescription")}
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
                                    {p.text || t("common:blank")}
                                  </pre>
                                </div>
                              ))}
                            </div>
                          </TabsContent>
                        ))}
                      </Tabs>
                    ) : (
                      <div className="space-y-3">
                        {codeReviewParagraphs.map((p) => (
                          <div
                            key={p.index}
                            className="rounded-lg border border-border p-3"
                          >
                            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                              P{p.index}
                            </span>
                            <pre className="text-sm text-foreground/80 mt-1 max-h-40 overflow-auto font-mono leading-relaxed whitespace-pre-wrap break-words bg-muted/30 rounded p-2">
                              {p.text || t("common:blank")}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

            {/* code review results */}
            {codeReviewResults.length > 0 && (
              <div className="space-y-4">
                {codeReviewResults.some(
                  (r) =>
                    (r.suggestions && r.suggestions.length > 0) ||
                    (r.new_text && r.new_text !== (r.text || r.original)),
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
                      {t("common:applyAll")}
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
                  typeLabels={suggestionTypeLabels}
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t("pages:code_review.noResults")}
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
                          {(resultGroups[f] || []).map((result) => (
                            <CodeReviewResultCard
                              key={`${result.file_path}-cr-result-P${result.paragraph_index}`}
                              result={result}
                              onApply={handleApplyChange}
                              onIgnore={handleIgnoreChange}
                              paragraphs={codeReviewParagraphs}
                              typeLabels={suggestionTypeLabels}
                              severityLabels={severityLabels}
                            />
                          ))}
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>
                ) : (
                  filteredResults.map((result) => (
                    <CodeReviewResultCard
                      key={`${result.file_path}-cr-result-P${result.paragraph_index}`}
                      result={result}
                      onApply={handleApplyChange}
                      onIgnore={handleIgnoreChange}
                      paragraphs={codeReviewParagraphs}
                      typeLabels={suggestionTypeLabels}
                      severityLabels={severityLabels}
                    />
                  ))
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmApplyAllOpen}
        onOpenChange={setConfirmApplyAllOpen}
        title={t("common:applyAllConfirmTitle")}
        description={t("common:applyAllConfirmDesc")}
        confirmText={t("common:applyAll")}
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAll();
        }}
      />
    </div>
  );
}
