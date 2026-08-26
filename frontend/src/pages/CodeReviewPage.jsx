import {
  useState,
  useMemo,
  useCallback,
  useDeferredValue,
  useRef,
} from "react";
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
import Eye from "lucide-react/dist/esm/icons/eye.mjs";
import ListChecks from "lucide-react/dist/esm/icons/list-checks.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Search from "lucide-react/dist/esm/icons/search.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import UndoRedoButtons from "@/components/UndoRedoButtons";
import React from "react";
import { diffLines } from "diff";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import useIsOpened from "@/hooks/useIsOpened";
import { emptyStateCta } from "@/utils/projectAccess";
import { isMultiUser } from "@/api/connection";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import StaleCheckBanner from "@/components/editor/StaleCheckBanner";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";
import MarkerText from "@/components/MarkerText";
import CodeBlock, { latexEditorClass } from "@/components/CodeBlock";
import AnnotatedSource from "@/pages/code-review/AnnotatedSource";
import DiffView from "@/pages/code-review/DiffView";
import {
  buildAnchors,
  cumulativeShift,
  locateSpan,
} from "@/pages/code-review/anchors";

const severityBadgeClass = {
  info: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  warning: "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400",
  error: "bg-red-500/10 text-red-600 dark:text-red-400",
};

const CodeReviewResultCard = React.memo(function CodeReviewResultCard({
  result,
  onApply,
  onIgnore,
  applyDisabled = false,
  paragraphs,
  paragraphMap,
  typeLabels,
  severityLabels,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const hasSuggestions = result.suggestions && result.suggestions.length > 0;
  const originalText = result.text || result.original;
  const formattedText = result.new_text;
  const hasFormattingDiff =
    Boolean(formattedText) && formattedText !== originalText;
  const [showFormatted, setShowFormatted] = useState(hasFormattingDiff);
  const toggleFormatted = useCallback(() => setShowFormatted((v) => !v), []);
  const diffParts = useMemo(() => {
    if (!formattedText) return null;
    return diffLines(originalText, formattedText);
  }, [formattedText, originalText]);
  const anchors = useMemo(() => {
    if (!formattedText || !hasSuggestions) return [];
    return buildAnchors(formattedText, result.suggestions);
  }, [formattedText, result.suggestions, hasSuggestions]);
  const [editedText, setEditedText] = useState(formattedText || "");
  /** 已采纳建议在 formattedText 坐标下的编辑记录（含对后续锚点位移），reset/换结果时清空。 */
  const appliedEditsRef = useRef([]);
  const [prevFormatted, setPrevFormatted] = useState(formattedText);
  if (formattedText !== prevFormatted) {
    setPrevFormatted(formattedText);
    setEditedText(formattedText || "");
    appliedEditsRef.current = [];
  }
  /** 建议编号 → 锚点 offset 的查找表（无锚点的建议取不到，回退首现匹配）。 */
  const anchorOffsetBySuggestion = useMemo(() => {
    const map = new Map();
    anchors.forEach((a) => map.set(a.index, a.offset));
    return map;
  }, [anchors]);
  const handleApplySuggestion = useCallback(
    (original, suggested, anchorOffset) => {
      if (!original) return;
      const prev = editedText;
      // 锚点 offset 是 formattedText 坐标：加上左侧已采纳编辑的累计位移，估算其在
      // 当前文本中的位置，再就近命中同一片段——重复片段时不再总是替换首次出现处。
      const estimated = Number.isFinite(anchorOffset)
        ? anchorOffset + cumulativeShift(appliedEditsRef.current, anchorOffset)
        : 0;
      // 与锚点定位共用同一套容错逻辑：精确匹配失败时折叠空白宽松匹配；
      // 找不到匹配时给出可见反馈，而不是静默失效。
      const span = locateSpan(prev, original, estimated);
      if (!span) {
        toast.warning(t("pages:code_review.applySuggestionNoMatch"));
        return;
      }
      const next = prev.slice(0, span.start) + suggested + prev.slice(span.end);
      appliedEditsRef.current = Number.isFinite(anchorOffset)
        ? [
            ...appliedEditsRef.current.filter((e) => e.offset !== anchorOffset),
            {
              offset: anchorOffset,
              removedLen: original.length,
              addedLen: suggested.length,
            },
          ]
        : appliedEditsRef.current;
      setEditedText(next);
    },
    [editedText, t],
  );
  const handleResetEdit = useCallback(() => {
    setEditedText(formattedText || "");
    appliedEditsRef.current = [];
  }, [formattedText]);
  const userChanged = Boolean(formattedText) && editedText !== formattedText;
  const deferredEditedText = useDeferredValue(editedText);
  const userDiffParts = useMemo(() => {
    if (!userChanged) return null;
    return diffLines(formattedText || "", deferredEditedText);
  }, [userChanged, formattedText, deferredEditedText]);
  const diffIsStale = editedText !== deferredEditedText;
  const [showUserDiff, setShowUserDiff] = useState(false);
  const statusKey = userChanged
    ? "pages:code_review.finalStatusChanged"
    : hasFormattingDiff
      ? "pages:code_review.finalStatusFormatted"
      : "pages:code_review.finalStatusOriginal";
  return (
    <Card
      key={`${result.file_path}-cr-result-P${result.paragraph_index}`}
      id={`cr-result-P${result.paragraph_index}`}
      className="[content-visibility:auto] [contain-intrinsic-size:auto_180px]"
    >
      <CardHeader className="pb-2">
        <div className="flex items-baseline justify-between gap-3">
          <CardTitle className="text-sm font-medium">
            {t("pages:code_review.paragraph", {
              index: result.paragraph_index,
            })}
          </CardTitle>
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {hasFormattingDiff
              ? t("pages:code_review.formatDiffYes")
              : t("pages:code_review.formatDiffNo")}
            {hasSuggestions &&
              ` · ${t("pages:code_review.suggestionsCount", {
                count: result.suggestions.length,
              })}`}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          <div className="md:grid md:grid-cols-[minmax(0,1fr)_300px] md:gap-6">
            {/* 样张：源码 + 格式化标记 + 批注锚点 */}
            {formattedText && (
              <div>
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    {t("pages:code_review.sourceCaption")}
                  </span>
                  {hasFormattingDiff && (
                    <button
                      type="button"
                      onClick={toggleFormatted}
                      className="text-xs text-muted-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:text-foreground cursor-pointer"
                    >
                      {showFormatted
                        ? t("pages:code_review.formatMarksOn")
                        : t("pages:code_review.formatMarksOff")}
                    </button>
                  )}
                </div>
                <div className="code-block p-3 text-xs">
                  <AnnotatedSource
                    parts={diffParts}
                    anchors={anchors}
                    showMarks={showFormatted}
                  />
                </div>
                {!hasFormattingDiff && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("pages:code_review.noFormattingDiff")}
                  </p>
                )}
              </div>
            )}

            {/* 边注：LLM 批注 */}
            <aside className="mt-4 md:mt-0 md:border-l md:border-border/70 md:pl-6">
              <div className="mb-1 text-xs text-muted-foreground">
                {t("pages:code_review.notesCaption")}
              </div>
              {hasSuggestions ? (
                <ol className="space-y-2">
                  {result.suggestions.map((suggestion, i) => (
                    <li
                      key={i}
                      className="rounded-md border border-border/70 bg-background p-2.5 text-xs"
                    >
                      <div className="mb-1 flex items-center gap-1.5 flex-wrap">
                        <span className="font-semibold text-amber-600 dark:text-amber-400">
                          {i + 1}
                        </span>
                        <span className="font-medium">
                          {typeLabels?.[suggestion.type] || suggestion.type}
                        </span>
                        <span
                          className={cn(
                            "rounded px-1 py-px text-[10px]",
                            severityBadgeClass[suggestion.severity] ||
                              "bg-muted text-muted-foreground",
                          )}
                        >
                          {severityLabels?.[suggestion.severity] ||
                            suggestion.severity}
                        </span>
                      </div>
                      {suggestion.description && (
                        <p className="mb-1.5 text-muted-foreground">
                          {suggestion.description}
                        </p>
                      )}
                      <div className="rounded bg-code-bg/70 p-1.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all">
                        <span className="text-muted-foreground line-through">
                          <MarkerText
                            text={suggestion.original}
                            variant="text"
                          />
                        </span>
                        <span className="text-muted-foreground/50"> → </span>
                        <span className="text-amber-700 dark:text-amber-300">
                          <MarkerText
                            text={suggestion.suggested}
                            variant="text"
                          />
                        </span>
                      </div>
                      <div className="mt-1.5 flex justify-end">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 px-2 text-xs"
                          onClick={() =>
                            handleApplySuggestion(
                              suggestion.original,
                              suggestion.suggested,
                              anchorOffsetBySuggestion.get(i + 1),
                            )
                          }
                        >
                          {t("pages:code_review.applySuggestion")}
                        </Button>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("pages:code_review.noSuggestionsFound")}
                </p>
              )}
            </aside>

            {/* 定稿：用户可编辑的最终文本 */}
            {formattedText && (
              <div className="border-t border-border/70 pt-3 md:col-span-2">
                <div className="mb-1 flex items-baseline gap-2">
                  <span className="text-xs font-medium text-foreground/80">
                    {t("pages:code_review.finalTextLabel")}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t(statusKey)}
                  </span>
                  <button
                    type="button"
                    onClick={handleResetEdit}
                    className="ml-auto text-xs text-muted-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:text-foreground cursor-pointer"
                  >
                    {t("pages:code_review.resetEdit")}
                  </button>
                </div>
                {userChanged && (
                  <div className="mb-1">
                    <button
                      type="button"
                      onClick={() => setShowUserDiff((v) => !v)}
                      className="text-xs text-muted-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:text-foreground cursor-pointer"
                    >
                      {showUserDiff
                        ? t("pages:code_review.hideUserDiff")
                        : t("pages:code_review.showUserDiff")}
                    </button>
                  </div>
                )}
                <textarea
                  value={editedText}
                  onChange={(e) => setEditedText(e.target.value)}
                  className={cn(
                    latexEditorClass,
                    "w-full min-h-[120px] p-2.5 text-xs",
                  )}
                  placeholder={t("pages:code_review.editPlaceholder")}
                  spellCheck={false}
                />
                {showUserDiff && userDiffParts && (
                  <div
                    className={cn(
                      "mt-2 overflow-hidden rounded-md border border-border bg-muted/30 transition-opacity",
                      diffIsStale && "opacity-60",
                    )}
                  >
                    <DiffView parts={userDiffParts} />
                  </div>
                )}
                {editedText !== originalText && (
                  <div className="mt-2 flex items-center gap-2">
                    <Button
                      size="sm"
                      onClick={() =>
                        onApply(
                          result.paragraph_index,
                          editedText,
                          result.file_path,
                        )
                      }
                      disabled={applyDisabled}
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
              </div>
            )}
          </div>
          <ParagraphContext
            currentIndex={result.paragraph_index}
            paragraphs={paragraphs}
            paragraphMap={paragraphMap}
            currentText={result.text || result.original}
          />
        </div>
      </CardContent>
    </Card>
  );
});

export default function CodeReviewPage() {
  const { t } = useTranslation(["pages"]);
  const isOpened = useIsOpened();
  const cta = emptyStateCta(isMultiUser());
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const hasResultIssue = useCallback((r) => {
    const hasChanges = r.changes && r.changes.length > 0;
    const hasSuggestions = r.suggestions && r.suggestions.length > 0;
    const corrected = r.new_text;
    const original = r.text || r.original;
    const hasFormatDiff = corrected && original && corrected !== original;
    return hasChanges || hasSuggestions || hasFormatDiff;
  }, []);
  const paragraphCheckMessages = useMemo(
    () => ({
      checkLoading: t("pages:code_review.checkLoading"),
      checkSuccess: (count) => t("pages:code_review.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:code_review.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:code_review.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:code_review.applyAllEmpty"),
    }),
    [t],
  );
  const {
    workspaceId,
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
    staleFiles,
    handlePreview,
    handleCheck,
    handlePauseResume,
    handleStop,
    handleApplyChange,
    handleApplyAll,
    handleIgnoreChange,
    handleFileChange,
    canWrite,
  } = useParagraphCheck({
    apiModule: api.codeReview,
    checkMethodName: "check",
    correctedTextField: "new_text",
    /** code_review 额外将工具格式化差异视为"有问题"，使这些段落在结果中置顶并纳入统计 */
    hasResultIssue,
    storeSlice: {
      paragraphsKey: "codeReviewParagraphs",
      setParagraphsKey: "setCodeReviewParagraphs",
      resultsKey: "codeReviewResults",
      setResultsKey: "setCodeReviewResults",
      loadingKey: "codeReview",
    },
    messages: paragraphCheckMessages,
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
  const paragraphMap = useMemo(
    () => new Map(codeReviewParagraphs.map((p) => [p.index, p])),
    [codeReviewParagraphs],
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
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:code_review.title")}
          groupKey="code_review"
        />
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <EmptyState title={t("common:restoring")} className="mt-12">
            <Loader2 className="animate-spin size-6 text-muted-foreground" />
          </EmptyState>
        ) : !isOpened || texFiles.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title={t("pages:code_review.noProjectTitle")}
            description={t("pages:code_review.noProjectDesc")}
            className="mt-12"
          >
            <Button nativeButton={false} render={<Link to={cta.to} />}>
              {t(`common:${cta.labelKey}`)}
            </Button>
          </EmptyState>
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
                  <div className="ml-auto">
                    {canWrite && (
                      <UndoRedoButtons
                        activeFile={activeFile}
                        workspaceId={workspaceId}
                        onAfterUndoRedo={handlePreview}
                      />
                    )}
                  </div>
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

            <StaleCheckBanner files={staleFiles} />

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
                                  className="rounded-lg border border-border bg-background p-3"
                                >
                                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                                    P{p.index}
                                  </span>
                                  <CodeBlock
                                    code={p.text || t("common:blank")}
                                    language="latex"
                                    className="mt-1 max-h-40 p-2 text-sm"
                                  />
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
                            className="rounded-lg border border-border bg-background p-3"
                          >
                            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                              P{p.index}
                            </span>
                            <CodeBlock
                              code={p.text || t("common:blank")}
                              language="latex"
                              className="mt-1 max-h-40 p-2 text-sm"
                            />
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
                      disabled={applyingAll || isLoading || !canWrite}
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
                              applyDisabled={!canWrite}
                              paragraphs={codeReviewParagraphs}
                              paragraphMap={paragraphMap}
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
                      applyDisabled={!canWrite}
                      paragraphs={codeReviewParagraphs}
                      paragraphMap={paragraphMap}
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
        title={t("common:applyAllConfirm")}
        description={t("common:applyAllConfirm")}
        confirmText={t("common:applyAll")}
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAll();
        }}
      />
    </div>
  );
}
