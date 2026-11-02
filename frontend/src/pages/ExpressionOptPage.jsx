import { useState, useCallback, useEffect, useMemo, memo } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import PencilLine from "lucide-react/dist/esm/icons/pencil-line.mjs";
import PencilSparkles from "lucide-react/dist/esm/icons/pencil-sparkles.mjs";
import Eye from "lucide-react/dist/esm/icons/eye.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Plus from "lucide-react/dist/esm/icons/plus.mjs";
import Trash2 from "lucide-react/dist/esm/icons/trash-2.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import ListChecks from "lucide-react/dist/esm/icons/list-checks.mjs";
import { api } from "@/api/index";
import PageHeader from "@/components/PageHeader";
import ModelBadge from "@/components/ModelBadge";
import EmptyState from "@/components/EmptyState";
import UndoRedoButtons from "@/components/UndoRedoButtons";
import ProofDiff from "@/components/ProofDiff";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import { buildParagraphMap } from "@/utils/paragraphKey";
import useIsOpened from "@/hooks/useIsOpened";
import { emptyStateCta } from "@/utils/projectAccess";
import { isMultiUser } from "@/api/connection";
import MarkerText from "@/components/MarkerText";
import {
  UnlistedChangesBadge,
  UnlistedChangesBody,
} from "@/components/UnlistedChanges";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import StaleCheckBanner from "@/components/editor/StaleCheckBanner";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";
import { resultDomId } from "@/utils/resultDomId";

/** 结果卡 DOM id 前缀：与 useResultFilter 跳转共用同一契约（resultDomId）。 */
const RESULT_ID_PREFIX = "expr-result";

/**
 * 单条优化建议卡片（rerender-memo，对齐 TypoResultCard 模式）：
 * 单/双文件分支共用一份实现。单文件分支此前缺 unlisted_changes 处理，
 * 该类结果落入「无建议」文案——修改后的全文不可见、无应用/忽略入口。
 */
const ExpressionResultCard = memo(function ExpressionResultCard({
  result,
  paragraphs,
  paragraphMap,
  onApply,
  onIgnore,
  canWrite,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const hasChanges = result.changes && result.changes.length > 0;
  const hasUnlistedChanges = result.unlisted_changes === true;
  return (
    <Card
      id={resultDomId(
        RESULT_ID_PREFIX,
        result.file_path,
        result.paragraph_index,
      )}
      className="[content-visibility:auto] [contain-intrinsic-size:auto_180px]"
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">
            {t("pages:expressionOpt.paragraphLabel", {
              index: result.paragraph_index,
            })}
          </CardTitle>
          {hasChanges && (
            <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
              {result.changes.length} {t("pages:expressionOpt.suggestions")}
            </span>
          )}
          {hasUnlistedChanges && (
            <UnlistedChangesBadge
              label={t("pages:expressionOpt.unlistedChangesBadge")}
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
                    className="rounded border border-border bg-background p-2 text-sm"
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
                    <ProofDiff
                      original={change.original}
                      modified={change.optimized}
                    />
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() =>
                    onApply(
                      result.paragraph_index,
                      result.optimized,
                      result.file_path,
                    )
                  }
                  disabled={!canWrite}
                >
                  {t("pages:expressionOpt.applyOptimization")}
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
          ) : hasUnlistedChanges ? (
            <UnlistedChangesBody
              text={result.optimized}
              message={t("pages:expressionOpt.unlistedChanges")}
              applyLabel={t("pages:expressionOpt.applyOptimization")}
              applyDisabled={!canWrite}
              onApply={() =>
                onApply(
                  result.paragraph_index,
                  result.optimized,
                  result.file_path,
                )
              }
              onIgnore={() =>
                onIgnore(result.paragraph_index, result.file_path)
              }
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              {t("pages:expressionOpt.noSuggestions")}
            </p>
          )}
          <ParagraphContext
            currentIndex={result.paragraph_index}
            filePath={result.file_path}
            paragraphs={paragraphs}
            paragraphMap={paragraphMap}
            currentText={result.plain_text || result.original}
          />
        </div>
      </CardContent>
    </Card>
  );
});

export default function ExpressionOptPage() {
  const { t } = useTranslation(["pages", "common"]);
  const isOpened = useIsOpened();
  const cta = emptyStateCta(isMultiUser());
  const paragraphCheckMessages = useMemo(
    () => ({
      checkLoading: t("pages:expressionOpt.checkLoading"),
      checkSuccess: (count) => t("pages:expressionOpt.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:expressionOpt.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:expressionOpt.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:expressionOpt.applyAllEmpty"),
    }),
    [t],
  );
  const {
    workspaceId,
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
    staleFiles,
    canWrite,
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
  } = useResultFilter(expressionResults, { idPrefix: RESULT_ID_PREFIX });

  const paragraphMap = useMemo(
    () => buildParagraphMap(expressionParagraphs),
    [expressionParagraphs],
  );

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
    api.expressionOpt.prompts
      .list()
      .then((data) => {
        setPrompts(data.prompts || []);
        const active = (data.prompts || []).find((p) => p.active);
        if (active) setActivePrompt(active.name);
      })
      .catch(() => {});
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
      toast.success(t("pages:expressionOpt.promptSaved"), {
        description: name,
      });
    } catch (e) {
      setPromptError(e.message);
      toast.error(t("pages:expressionOpt.promptSaveFailed"), {
        description: e.message,
      });
    }
  }, [newPromptName, promptContent, t]);

  const handleDeletePrompt = useCallback(
    async (name) => {
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
        toast.success(t("pages:expressionOpt.promptDeleted"), {
          description: name,
        });
      } catch (e) {
        setPromptError(e.message);
        toast.error(t("pages:expressionOpt.promptDeleteFailed"), {
          description: e.message,
        });
      }
    },
    [editingPrompt, activePrompt, t],
  );

  const handleSetActivePrompt = useCallback(
    async (name) => {
      try {
        await api.expressionOpt.prompts.setActive(name);
        setActivePrompt(name);
        setPromptMessage(
          name
            ? t("pages:expressionOpt.activePromptEnabled", { name })
            : t("pages:expressionOpt.usingDefaultPrompt"),
        );
        setPromptError(null);
        toast.success(
          name
            ? t("pages:expressionOpt.promptEnabled", { name })
            : t("pages:expressionOpt.switchedToDefault"),
        );
      } catch (e) {
        setPromptError(e.message);
        toast.error(t("pages:expressionOpt.promptSwitchFailed"), {
          description: e.message,
        });
      }
    },
    [t],
  );

  const handleEditPrompt = useCallback(
    async (name) => {
      try {
        const data = await api.expressionOpt.prompts.load(name);
        setEditingPrompt(name);
        setPromptContent(data.content || "");
        setPromptError(null);
      } catch (e) {
        setPromptError(e.message);
        toast.error(t("pages:expressionOpt.promptLoadFailed"), {
          description: e.message,
        });
      }
    },
    [t],
  );

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

  const paragraphsByFile = useMemo(
    () => (activeFiles.length > 1 ? groupByFile(expressionParagraphs) : []),
    [activeFiles.length, expressionParagraphs],
  );
  const resultsByFile = useMemo(
    () => (activeFiles.length > 1 ? groupByFile(expressionResults) : []),
    [activeFiles.length, expressionResults],
  );

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:expressionOpt.title")}

          showWorkspace
        >
          <ModelBadge role="expression_opt" />
        </PageHeader>
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <EmptyState title={t("common:restoring")} className="mt-12">
            <Loader2 className="animate-spin size-6 text-muted-foreground" />
          </EmptyState>
        ) : !isOpened || texFiles.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title={t("pages:expressionOpt.noProjectTitle")}
            description={t("pages:expressionOpt.noProjectDesc")}
            className="mt-12"
          >
            <Button nativeButton={false} render={<Link to={cta.to} />}>
              {t(`common:${cta.labelKey}`)}
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
                    onClick={handleOptimize}
                    disabled={isLoading || expressionParagraphs.length === 0}
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <PencilSparkles />
                    )}
                    {isLoading
                      ? t("pages:expressionOpt.optimizing")
                      : t("pages:expressionOpt.startOptimize")}
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

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {t("pages:expressionOpt.promptSectionTitle")}
                </CardTitle>
                <CardDescription>
                  {t("pages:expressionOpt.promptSectionDesc")}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  {prompts.map((p) => (
                    <div key={p.name} className="flex items-center gap-1">
                      <Button
                        variant={
                          activePrompt === p.name ? "default" : "outline"
                        }
                        size="sm"
                        onClick={() =>
                          handleSetActivePrompt(
                            activePrompt === p.name ? "" : p.name,
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
                    {t("pages:expressionOpt.noPrompts")}
                  </p>
                )}

                {activePrompt && previewContent && (
                  <div className="rounded-md border border-border bg-muted/30 p-3">
                    <span className="text-xs font-medium text-muted-foreground">
                      {t("pages:expressionOpt.activePromptLabel")}
                      {activePrompt}
                    </span>
                    <pre className="whitespace-pre-wrap text-sm text-foreground/80 font-sans leading-relaxed max-h-48 overflow-y-auto mt-2">
                      {previewContent}
                    </pre>
                  </div>
                )}

                {editingPrompt && (
                  <p className="text-xs text-muted-foreground">
                    {t("pages:expressionOpt.editingLabel")}
                    {editingPrompt}
                  </p>
                )}

                <div className="flex items-end gap-2">
                  <div className="flex-1 space-y-1">
                    <Label htmlFor="prompt-name" className="text-xs">
                      {t("pages:expressionOpt.nameLabel")}
                    </Label>
                    <Input
                      id="prompt-name"
                      className="h-7 text-sm"
                      value={newPromptName}
                      onChange={(e) => setNewPromptName(e.target.value)}
                      placeholder={t(
                        "pages:expressionOpt.promptNamePlaceholder",
                      )}
                    />
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleCreatePrompt}
                    disabled={!newPromptName.trim() || !promptContent.trim()}
                  >
                    <Plus />
                    {t("pages:expressionOpt.save")}
                  </Button>
                </div>
                <Textarea
                  className="min-h-24 resize-none text-sm"
                  value={promptContent}
                  onChange={(e) => setPromptContent(e.target.value)}
                  placeholder={t("pages:expressionOpt.promptPlaceholder")}
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

            {expressionParagraphs.length > 0 &&
              expressionResults.length === 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:expressionOpt.paragraphPreviewTitle", {
                        count: expressionParagraphs.length,
                      })}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:expressionOpt.paragraphPreviewDesc")}
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
                              {(paragraphsByFile[f] || []).map((p) => (
                                <div
                                  key={p.index}
                                  className="rounded-lg border border-border bg-background p-3"
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
                    ) : (
                      <div className="space-y-3">
                        {expressionParagraphs.map((p) => (
                          <div
                            key={p.index}
                            className="rounded-lg border border-border bg-background p-3"
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

            {expressionResults.length > 0 && (
              <div className="space-y-4">
                {/* 可见性与 hook 的 hasResultIssue 同口径：unlisted 结果
                    也是待应用项，unlisted-only 结果集下按钮不得隐藏 */}
                {expressionResults.some(
                  (r) =>
                    (r.changes && r.changes.length > 0) ||
                    r.unlisted_changes === true,
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
                />
                {filteredResults.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    {t("pages:expressionOpt.noMatchResult")}
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
                          {(resultsByFile[f] || []).length === 0 ? (
                            <p className="text-sm text-muted-foreground text-center py-8">
                              {t("pages:expressionOpt.noFileResult")}
                            </p>
                          ) : (
                            (resultsByFile[f] || []).map((result) => (
                              <ExpressionResultCard
                                key={`${result.file_path}:${result.paragraph_index}`}
                                result={result}
                                paragraphs={expressionParagraphs}
                                paragraphMap={paragraphMap}
                                onApply={handleApplyChange}
                                onIgnore={handleIgnoreChange}
                                canWrite={canWrite}
                              />
                            ))
                          )}
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>
                ) : (
                  filteredResults.map((result) => (
                    <ExpressionResultCard
                      key={`${result.file_path}:${result.paragraph_index}`}
                      result={result}
                      paragraphs={expressionParagraphs}
                      paragraphMap={paragraphMap}
                      onApply={handleApplyChange}
                      onIgnore={handleIgnoreChange}
                      canWrite={canWrite}
                    />
                  ))
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
        title={t("pages:expressionOpt.deletePromptTitle")}
        description={t("pages:expressionOpt.deletePromptDesc", {
          name: confirmDeletePrompt,
        })}
        confirmText={t("pages:expressionOpt.delete")}
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
