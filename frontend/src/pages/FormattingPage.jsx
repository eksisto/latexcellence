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

import React from "react";

const FormattingResultCard = React.memo(function FormattingResultCard({
  result,
  onApply,
  onIgnore,
  paragraphs,
  typeLabels,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const hasChanges = result.changes && result.changes.length > 0;
  return (
    <Card
      key={`${result.file_path}-fmt-result-P${result.paragraph_index}`}
      id={`fmt-result-P${result.paragraph_index}`}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">
            {t("pages:formatting.paragraph", { index: result.paragraph_index })}
          </CardTitle>
          {hasChanges && (
            <span className="rounded bg-primary/10 text-primary px-1.5 py-0.5 text-xs font-medium">
              {t("pages:formatting.suggestionsCount", { count: result.changes.length })}
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
                        {typeLabels?.[change.type] || change.type}
                      </span>
                      {change.description && (
                        <span className="text-muted-foreground text-xs">
                          {change.description}
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                        {t("common:original")}
                      </span>
                      <pre className="line-through text-foreground/60 break-all mt-0.5 font-mono text-xs whitespace-pre-wrap bg-muted/20 rounded p-1.5">
                        {change.original}
                      </pre>
                      <span className="text-muted-foreground whitespace-nowrap mt-0.5">
                        {t("pages:formatting.formatted")}
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
                    onApply(result.paragraph_index, result.corrected, result.file_path)
                  }
                >
                  {t("pages:formatting.applyFormatting")}
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
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {t("pages:formatting.noIssues")}
            </p>
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

export default function FormattingPage() {
  const { t } = useTranslation(["pages", "common"]);
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
      checkLoading: t("pages:formatting.checkLoading"),
      checkSuccess: (count) => t("pages:formatting.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:formatting.applySuccess", { idx }),
      applyAllSuccess: (count) => t("pages:formatting.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:formatting.applyAllEmpty"),
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

  const paragraphGroups = useMemo(() => groupByFile(formattingParagraphs), [formattingParagraphs]);
  const resultGroups = useMemo(() => groupByFile(filteredResults), [filteredResults]);

  const changeTypeLabels = useMemo(() => ({
    spacing: t("pages:formatting.changeTypes.spacing"),
    environment_alignment: t("pages:formatting.changeTypes.environmentAlignment"),
    code_formatting: t("pages:formatting.changeTypes.codeFormatting"),
    consistency: t("pages:formatting.changeTypes.consistency"),
  }), [t]);

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
          <h1 className="text-3xl font-bold tracking-tight">{t("pages:formatting.title")}</h1>
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
                <CardTitle>{t("pages:formatting.noProjectTitle")}</CardTitle>
                <CardDescription>
                  {t("pages:formatting.noProjectDesc")}
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
                    disabled={
                      isLoading || formattingParagraphs.length === 0
                    }
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <AlignLeft />
                    )}
                    {isLoading ? t("pages:formatting.llmRequesting") : t("pages:formatting.start")}
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

            {/* paragraph preview - raw LaTeX source */}
            {formattingParagraphs.length > 0 &&
              formattingResults.length === 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:formatting.paragraphs", { count: formattingParagraphs.length })}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:formatting.paragraphsDescription")}
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
                        {formattingParagraphs.map((p) => (
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
                  typeLabels={changeTypeLabels}
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t("pages:formatting.noResults")}
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
                            <FormattingResultCard
                              key={`${result.file_path}-fmt-result-P${result.paragraph_index}`}
                              result={result}
                              onApply={handleApplyChange}
                              onIgnore={handleIgnoreChange}
                              paragraphs={formattingParagraphs}
                              typeLabels={changeTypeLabels}
                            />
                          ))}
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>
                ) : (
                  filteredResults.map((result) => (
                    <FormattingResultCard
                      key={`${result.file_path}-fmt-result-P${result.paragraph_index}`}
                      result={result}
                      onApply={handleApplyChange}
                      onIgnore={handleIgnoreChange}
                      paragraphs={formattingParagraphs}
                      typeLabels={changeTypeLabels}
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
        description={t("pages:formatting.applyAllDesc")}
        confirmText={t("common:applyAll")}
        onConfirm={async () => {
          setConfirmApplyAllOpen(false);
          await handleApplyAll();
        }}
      />
    </div>
  );
}
