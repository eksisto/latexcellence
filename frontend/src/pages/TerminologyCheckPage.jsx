import { useState, useCallback, useMemo, memo } from "react";
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
import CheckCheck from "lucide-react/dist/esm/icons/check-check.mjs";
import Eye from "lucide-react/dist/esm/icons/eye.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import ListChecks from "lucide-react/dist/esm/icons/list-checks.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import PageHeader from "@/components/PageHeader";
import ModelBadge from "@/components/ModelBadge";
import EmptyState from "@/components/EmptyState";
import UndoRedoButtons from "@/components/UndoRedoButtons";
import ProofDiff from "@/components/ProofDiff";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import { buildParagraphMap } from "@/utils/paragraphKey";
import useIsOpened from "@/hooks/useIsOpened";
import { emptyStateCta } from "@/utils/projectAccess";
import { isMultiUser } from "@/api/connection";
import MarkerText from "@/components/MarkerText";
import ParagraphContext from "@/components/ParagraphContext";
import ProgressBar from "@/components/ProgressBar";
import StaleCheckBanner from "@/components/editor/StaleCheckBanner";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";
import { resultDomId } from "@/utils/resultDomId";

/** 结果卡 DOM id 前缀：与 useResultFilter 跳转共用同一契约（resultDomId）。 */
const RESULT_ID_PREFIX = "term-result";
import useStore from "@/store/useStore";
import { toast } from "sonner";

/**
 * 单条变更行（rerender-memo-with-default-value）：ProofDiff 的 modified
 * 若直接传内联 <Input> 元素，引用每次渲染必变，ProofDiff 的 memo 形同虚设。
 * 这里把受控 Input 收进行组件内部、对外只暴露原始值 props，键入替换词时
 * 仅值变化的行重渲染，未编辑行的 ProofDiff 随之被 memo 跳过。
 */
const ChangeDiffRow = memo(function ChangeDiffRow({
  original,
  modifiedLabel,
  value,
  replaceKey,
  onReplace,
}) {
  return (
    <ProofDiff
      original={original}
      modifiedLabel={modifiedLabel}
      modified={
        <Input
          type="text"
          value={value}
          onChange={(e) => onReplace(replaceKey, e.target.value)}
          className="h-7 text-sm font-medium"
        />
      }
    />
  );
});

/**
 * 单条术语检查结果卡片（rerender-memo，对齐 CodeReviewResultCard 模式）：
 * 模块级 memo 组件 + 稳定回调 props，检查流 progress 高频更新、搜索过滤、
 * 消息变化时已渲染的卡片不再随页面整体重渲染。customReplacements 作为
 * prop 传入：编辑替换词的每次键入仍会刷新列表（受控输入所需），
 * 但流式检查期间卡片保持稳定；行级替换输入下沉到 ChangeDiffRow（memo），
 * 键入时未编辑行的 ProofDiff 不再随卡片重渲染。
 */
const TerminologyResultCard = memo(function TerminologyResultCard({
  result,
  paragraphs,
  paragraphMap,
  onApply,
  onIgnore,
  canWrite,
  customReplacements,
  onCustomReplacementChange,
  activeFile,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const hasChanges = result.changes && result.changes.length > 0;
  // onCustomReplacementChange 是 useState setter（引用稳定），此回调随之稳定，
  // 是 ChangeDiffRow memo 生效的前提
  const handleReplace = useCallback(
    (key, value) =>
      onCustomReplacementChange((prev) => ({ ...prev, [key]: value })),
    [onCustomReplacementChange],
  );
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
                      className="rounded border border-border bg-background p-2 text-sm"
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
                      <ChangeDiffRow
                        original={change.original}
                        modifiedLabel={t("pages:terminologyCheck.replacedWith")}
                        value={customReplacements[cKey] ?? change.corrected}
                        replaceKey={cKey}
                        onReplace={handleReplace}
                      />
                    </li>
                  );
                })}
              </ul>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => onApply(result)}
                  disabled={!canWrite}
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
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {t("pages:terminologyCheck.noIssues")}
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

export default function TerminologyCheckPage() {
  const { t } = useTranslation(["pages", "common"]);
  const isOpened = useIsOpened();
  const cta = emptyStateCta(isMultiUser());
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const [customReplacements, setCustomReplacements] = useState({});
  const paragraphCheckMessages = useMemo(
    () => ({
      checkLoading: t("pages:terminologyCheck.checkLoading"),
      checkSuccess: (count) =>
        t("pages:terminologyCheck.checkSuccess", { count }),
      applySuccess: (idx) => t("pages:terminologyCheck.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:terminologyCheck.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:terminologyCheck.applyAllEmpty"),
    }),
    [t],
  );
  const {
    workspaceId,
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
    staleFiles,
    operationId,
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
  } = useResultFilter(terminologyResults, { idPrefix: RESULT_ID_PREFIX });

  const paragraphMap = useMemo(
    () => buildParagraphMap(terminologyParagraphs),
    [terminologyParagraphs],
  );

  // 单趟按文件分组（js-index-maps）：过滤后的结果在多文件 Tabs 下逐 tab
  // 全量 filter 是 O(文件数×结果数)，检查流 rAF 批量追加时反复触发
  const filteredResultsByFile = useMemo(
    () => groupByFile(filteredResults),
    [filteredResults],
  );

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
      );
    }
    return (
      <div className="space-y-3">
        {paragraphs.map((p) => (
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
    );
  };

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:terminologyCheck.title")}
          groupKey="textReview"
        >
          <ModelBadge role="terminology_check" />
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
            title={t("pages:terminologyCheck.noProjectTitle")}
            description={t("pages:terminologyCheck.noProjectDesc")}
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
                          {(filteredResultsByFile[f] || []).map((result) => (
                            <TerminologyResultCard
                              key={`${result.file_path}-term-result-P${result.paragraph_index}`}
                              result={result}
                              paragraphs={terminologyParagraphs}
                              paragraphMap={paragraphMap}
                              onApply={handleApplyWithCustom}
                              onIgnore={handleIgnoreChange}
                              canWrite={canWrite}
                              customReplacements={customReplacements}
                              onCustomReplacementChange={setCustomReplacements}
                              activeFile={activeFile}
                            />
                          ))}
                        </div>
                      </TabsContent>
                    ))}
                  </Tabs>
                ) : (
                  <div className="space-y-4">
                    {filteredResults.map((result) => (
                      <TerminologyResultCard
                        key={`${result.file_path}-term-result-P${result.paragraph_index}`}
                        result={result}
                        paragraphs={terminologyParagraphs}
                        paragraphMap={paragraphMap}
                        onApply={handleApplyWithCustom}
                        onIgnore={handleIgnoreChange}
                        canWrite={canWrite}
                        customReplacements={customReplacements}
                        onCustomReplacementChange={setCustomReplacements}
                        activeFile={activeFile}
                      />
                    ))}
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
        title={t("common:applyAllConfirm")}
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
