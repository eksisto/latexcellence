import { useState, useMemo } from "react";
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
import { CheckCheck, Eye, Loader2, ListChecks, FolderOpen } from "lucide-react";
import { api } from "@/api/index";
import PageHeader from "@/components/PageHeader";
import EmptyState from "@/components/EmptyState";
import ProofDiff from "@/components/ProofDiff";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import MarkerText from "@/components/MarkerText";
import {
  UnlistedChangesBadge,
  UnlistedChangesBody,
} from "@/components/UnlistedChanges";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";

export default function TypoCheckPage() {
  const { t } = useTranslation(["pages", "common"]);
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
      checkLoading: t("pages:typoCheck.checkLoading"),
      checkSuccess: (count) => t("pages:typoCheck.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:typoCheck.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:typoCheck.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:typoCheck.applyAllEmpty"),
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

  const groupedParagraphs = useMemo(
    () => groupByFile(typoParagraphs),
    [typoParagraphs],
  );
  const groupedResults = useMemo(
    () => groupByFile(filteredResults),
    [filteredResults],
  );

  const renderResultCard = (result) => {
    const hasChanges = result.changes && result.changes.length > 0;
    const hasUnlistedChanges = result.unlisted_changes === true;
    return (
      <Card
        key={`${result.file_path}-${result.paragraph_index}`}
        id={`${result.file_path}-P${result.paragraph_index}`}
      >
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">
              {t("common:paragraph")} {result.paragraph_index}
            </CardTitle>
            {hasChanges && (
              <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                {result.changes.length} {t("common:issues")}
              </span>
            )}
            {hasUnlistedChanges && (
              <UnlistedChangesBadge
                label={t("pages:typoCheck.unlistedChangesBadge")}
              />
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
                      <ProofDiff
                        original={change.original}
                        modified={change.corrected}
                      />
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
            ) : hasUnlistedChanges ? (
              <UnlistedChangesBody
                text={result.corrected}
                message={t("pages:typoCheck.unlistedChanges")}
                applyLabel={t("common:applyChange")}
                onApply={() =>
                  handleApplyChange(
                    result.paragraph_index,
                    result.corrected,
                    result.file_path,
                  )
                }
                onIgnore={() =>
                  handleIgnoreChange(result.paragraph_index, result.file_path)
                }
              />
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("pages:typoCheck.noIssues")}
              </p>
            )}
            <ParagraphContext
              currentIndex={result.paragraph_index}
              paragraphs={typoParagraphs}
              currentText={result.plain_text || result.original}
            />
          </div>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader title={t("pages:typoCheck.title")} groupKey="textReview" />
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <EmptyState title={t("common:restoring")} className="mt-12">
            <Loader2 className="animate-spin size-6 text-muted-foreground" />
          </EmptyState>
        ) : !projectPath || texFiles.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title={t("pages:typoCheck.noProjectTitle")}
            description={t("pages:typoCheck.noProjectDesc")}
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
                    disabled={isLoading || typoParagraphs.length === 0}
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <CheckCheck />
                    )}
                    {isLoading
                      ? t("common:checking")
                      : t("pages:typoCheck.checkButton")}
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

            {typoParagraphs.length > 0 && typoResults.length === 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    {t("pages:typoCheck.paragraphPreviewTitle", {
                      count: typoParagraphs.length,
                    })}
                  </CardTitle>
                  <CardDescription>
                    {t("pages:typoCheck.paragraphPreviewDesc")}
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
                      {activeFiles.map((f) => {
                        const fileParagraphs = groupedParagraphs[f] || [];
                        return (
                          <TabsContent key={f} value={f}>
                            <div className="space-y-3">
                              {fileParagraphs.map((p) => (
                                <div
                                  key={`${f}-${p.index}`}
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
                        );
                      })}
                    </Tabs>
                  ) : (
                    <div className="space-y-3">
                      {typoParagraphs.map((p) => (
                        <div
                          key={`${activeFile}-${p.index}`}
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
                  )}
                </CardContent>
              </Card>
            )}

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
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t("common:noMatchResult")}
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
                    {activeFiles.map((f) => {
                      const fileResults = groupedResults[f] || [];
                      return (
                        <TabsContent key={f} value={f}>
                          <div className="space-y-4">
                            {fileResults.length === 0 ? (
                              <p className="text-sm text-muted-foreground text-center py-8">
                                {t("common:noFileResult")}
                              </p>
                            ) : (
                              fileResults.map((result) =>
                                renderResultCard(result),
                              )
                            )}
                          </div>
                        </TabsContent>
                      );
                    })}
                  </Tabs>
                ) : (
                  filteredResults.map((result) => renderResultCard(result))
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
