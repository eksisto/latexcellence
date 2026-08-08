import { useState, useCallback, useMemo } from "react";
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
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { CheckCheck, Eye, Loader2, ListChecks, FolderOpen } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import ProofDiff from "@/components/ProofDiff";
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
  const { t } = useTranslation(["pages", "common"]);
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
    operationId,
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
      checkLoading: t("pages:terminologyCheck.checkLoading"),
      checkSuccess: (count) =>
        t("pages:terminologyCheck.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:terminologyCheck.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:terminologyCheck.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:terminologyCheck.applyAllEmpty"),
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
    // 必须以 marker 保护的 plain_text 为基础重建修正文本：
    // corrected 中的 marker（如 __MATH_0000__）在写回时由后端还原为
    // 原始 LaTeX，若从 result.original（原始 LaTeX）重建，术语替换会
    // 误伤数学公式 / 命令内部的同名片段。
    let corrected = result.plain_text || result.corrected || result.original;
    const sorted = [...(result.changes || [])].sort(
      (a, b) => b.original.length - a.original.length,
    );
    for (const change of sorted) {
      const key = `${result.file_path || activeFile}:${result.paragraph_index}:${change.original}`;
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
    [customReplacements, buildCorrectedText, handleApplyChange],
  );

  const handleApplyAllWithCustom = useCallback(async () => {
    const pending = terminologyResults.filter(
      (r) => r.changes && r.changes.length > 0,
    );
    if (pending.length === 0) {
      toast.info(t("pages:terminologyCheck.applyAllEmpty"));
      return;
    }
    const toastId = toast.loading(t("pages:terminologyCheck.applying"), {
      description: t("pages:terminologyCheck.applyingDescription", {
        count: pending.length,
      }),
    });
    try {
      const changes = pending.map((r) => ({
        paragraph_index: r.paragraph_index,
        new_text: buildCorrectedText(r, customReplacements),
        file_path: r.file_path || activeFile,
      }));
      await api.terminologyCheck.apply(
        {
          file_path: activeFile,
          changes,
          operation_id: operationId?.current || null,
        },
        workspaceId,
      );
      const appliedKeys = new Set(
        pending.map((r) => `${r.file_path || activeFile}:${r.paragraph_index}`),
      );
      useStore.setState((state) => ({
        terminologyResults: state.terminologyResults.filter(
          (r) =>
            !appliedKeys.has(
              `${r.file_path || activeFile}:${r.paragraph_index}`,
            ),
        ),
      }));
      toast.success(
        t("pages:terminologyCheck.applyAllSuccess", { count: pending.length }),
        {
          id: toastId,
        },
      );
    } catch (e) {
      toast.error(t("pages:terminologyCheck.applyAllFailed"), {
        id: toastId,
        description: e.message,
      });
    }
  }, [
    terminologyResults,
    customReplacements,
    buildCorrectedText,
    activeFile,
    operationId,
    t,
  ]);

  const groupedTerminologyParagraphs = useMemo(
    () => groupByFile(terminologyParagraphs),
    [terminologyParagraphs],
  );

  const renderParagraphsByFile = (paragraphs) => {
    if (activeFiles.length > 1) {
      const groups = groupedTerminologyParagraphs;
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
                      {p.plain_text ? (
                        <MarkerText text={p.plain_text} />
                      ) : (
                        t("common:blank")
                      )}
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
          <div key={p.index} className="rounded-lg border border-border p-3">
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
              P{p.index}
            </span>
            <div className="whitespace-pre-wrap text-sm text-foreground/80 mt-1 max-h-32 overflow-y-auto font-sans leading-relaxed">
              {p.plain_text ? (
                <MarkerText text={p.plain_text} />
              ) : (
                t("common:blank")
              )}
            </div>
          </div>
        ))}
      </div>
    );
  };

  const renderResultCard = (result) => {
    const hasChanges = result.changes && result.changes.length > 0;
    return (
      <Card
        key={`${result.file_path}-term-result-P${result.paragraph_index}`}
        id={`term-result-P${result.paragraph_index}`}
      >
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">
              {t("pages:terminologyCheck.paragraph", {
                index: result.paragraph_index,
              })}
            </CardTitle>
            {hasChanges && (
              <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                {t("pages:terminologyCheck.issuesCount", {
                  count: result.changes.length,
                })}
              </span>
            )}
          </div>
          {result.terms && result.terms.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {result.terms.map((term, i) => (
                <span
                  key={i}
                  className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
                >
                  {term}
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
                    const cKey = `${result.file_path || activeFile}:${result.paragraph_index}:${change.original}`;
                    return (
                      <li
                        key={i}
                        className="rounded border border-border p-2 text-sm"
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className="rounded bg-orange-500/10 text-orange-600 dark:text-orange-400 px-1 py-0.5 text-xs font-medium">
                            {t("pages:terminologyCheck.inconsistency")}
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
                        <ProofDiff
                          original={change.original}
                          modifiedLabel={t(
                            "pages:terminologyCheck.replacedWith",
                          )}
                          modified={
                            <Input
                              type="text"
                              value={
                                customReplacements[cKey] ?? change.corrected
                              }
                              onChange={(e) =>
                                setCustomReplacements((prev) => ({
                                  ...prev,
                                  [cKey]: e.target.value,
                                }))
                              }
                              className="h-7 text-sm font-medium"
                            />
                          }
                        />
                      </li>
                    );
                  })}
                </ul>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => handleApplyWithCustom(result)}
                  >
                    {t("common:applyChange")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      handleIgnoreChange(
                        result.paragraph_index,
                        result.file_path,
                      )
                    }
                  >
                    {t("common:ignore")}
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("pages:terminologyCheck.noIssues")}
              </p>
            )}
            <ParagraphContext
              currentIndex={result.paragraph_index}
              paragraphs={terminologyParagraphs}
              currentText={result.plain_text || result.original}
            />
          </div>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:terminologyCheck.title")}
          groupKey="textReview"
        />
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <EmptyState title={t("common:restoring")} className="mt-12">
            <Loader2 className="animate-spin size-6 text-muted-foreground" />
          </EmptyState>
        ) : !projectPath || texFiles.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title={t("pages:terminologyCheck.noProjectTitle")}
            description={t("pages:terminologyCheck.noProjectDesc")}
            className="mt-12"
          >
            <Button nativeButton={false} render={<Link to="/workspace" />}>
              {t("common:openWorkspace")}
            </Button>
          </EmptyState>
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
                    disabled={isLoading || terminologyParagraphs.length === 0}
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <CheckCheck />
                    )}
                    {isLoading
                      ? t("common:checking")
                      : t("pages:terminologyCheck.checkButton")}
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

            {terminologyParagraphs.length > 0 &&
              terminologyResults.length === 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:terminologyCheck.paragraphs", {
                        count: terminologyParagraphs.length,
                      })}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:terminologyCheck.paragraphsDescription")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {renderParagraphsByFile(terminologyParagraphs)}
                  </CardContent>
                </Card>
              )}

            {terminologyResults.length > 0 && (
              <div className="space-y-4">
                {terminologyResults.some(
                  (r) => r.changes && r.changes.length > 0,
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
                  typeLabels={{
                    inconsistency: t("pages:terminologyCheck.inconsistency"),
                  }}
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t("common:noResults")}
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
        title={t("common:applyAllConfirmTitle")}
        description={t("common:applyAllDescription")}
        confirmText={t("common:applyAll")}
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAllWithCustom();
        }}
      />
    </div>
  );
}
