import { useState, useMemo, memo } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import Languages from "lucide-react/dist/esm/icons/languages.mjs";
import Eye from "lucide-react/dist/esm/icons/eye.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import ListChecks from "lucide-react/dist/esm/icons/list-checks.mjs";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import PageHeader from "@/components/PageHeader";
import ModelBadge from "@/components/ModelBadge";
import EmptyState from "@/components/EmptyState";
import UndoRedoButtons from "@/components/UndoRedoButtons";
import { api } from "@/api/index";
import useParagraphCheck, { groupByFile } from "@/hooks/useParagraphCheck";
import { buildParagraphMap } from "@/utils/paragraphKey";
import useIsOpened from "@/hooks/useIsOpened";
import { emptyStateCta } from "@/utils/projectAccess";
import { isMultiUser } from "@/api/connection";
import MarkerText from "@/components/MarkerText";
import ParagraphContext from "@/components/ParagraphContext";
import {
  UnlistedChangesBadge,
  UnlistedChangesBody,
} from "@/components/UnlistedChanges";
import ProgressBar from "@/components/ProgressBar";
import StaleCheckBanner from "@/components/editor/StaleCheckBanner";
import ConfirmDialog from "@/components/ConfirmDialog";
import ResultFilterBar from "@/components/ResultFilterBar";
import useResultFilter from "@/hooks/useResultFilter";
import { resultDomId } from "@/utils/resultDomId";

/** 结果卡 DOM id 前缀：与 useResultFilter 跳转共用同一契约（resultDomId）。 */
const RESULT_ID_PREFIX = "translate-result";

const OTHER_LANGUAGE = "__other__";

/**
 * 单条翻译结果卡片（rerender-memo，对齐 CodeReviewResultCard 模式）：
 * 模块级 memo 组件 + 稳定回调 props，检查流 progress 高频更新、
 * 搜索过滤、消息变化时已渲染的卡片不再随页面整体重渲染。
 */
const TranslateResultCard = memo(function TranslateResultCard({
  result,
  paragraphs,
  paragraphMap,
  onApply,
  onIgnore,
  canWrite,
}) {
  const { t } = useTranslation(["pages", "common"]);
  // 比较 translated 与 original 而非 plain_text：
  // translated 和 original 均已还原 marker（由后端 _build_success_result 处理），
  // 而 plain_text 仍含 marker。若用 plain_text 比较，"__CMD_0001__" ≠ "\\textbf"，
  // 任何含 LaTeX 命令的段落都会被误判为 hasTranslation=true
  const hasTranslation = result.translated !== result.original;
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
            {t("common:paragraph")} {result.paragraph_index}
          </CardTitle>
          {hasTranslation && (
            <span className="rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 text-xs font-medium">
              {t("pages:paragraphTranslate.translated")}
            </span>
          )}
          {hasUnlistedChanges && (
            <UnlistedChangesBadge
              label={t("pages:paragraphTranslate.unlistedChangesBadge")}
            />
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {hasUnlistedChanges ? (
            <UnlistedChangesBody
              text={result.translated}
              message={t("pages:paragraphTranslate.unlistedChanges")}
              applyLabel={t("common:applyChange")}
              applyDisabled={!canWrite}
              onApply={() =>
                onApply(
                  result.paragraph_index,
                  result.translated,
                  result.file_path,
                )
              }
              onIgnore={() =>
                onIgnore(result.paragraph_index, result.file_path)
              }
            />
          ) : hasTranslation ? (
            <>
              <div className="rounded border border-border bg-background p-2 text-sm space-y-2">
                <div>
                  <span className="text-muted-foreground text-xs">
                    {t("common:original")}
                  </span>
                  <div className="mt-0.5 whitespace-pre-wrap break-all text-foreground/60">
                    <MarkerText text={result.original} />
                  </div>
                </div>
                <div>
                  <span className="text-muted-foreground text-xs">
                    {t("pages:paragraphTranslate.translation")}
                  </span>
                  <div className="mt-0.5 whitespace-pre-wrap break-all font-medium">
                    {result.translated}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() =>
                    onApply(
                      result.paragraph_index,
                      result.translated,
                      result.file_path,
                    )
                  }
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
              {t("pages:paragraphTranslate.noTranslation")}
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

const TARGET_LANGUAGES = [
  { value: "English", label: "English" },
  {
    value: "Simplified Chinese",
    label: "\u7b80\u4f53\u4e2d\u6587",
  },
  {
    value: "Traditional Chinese",
    label: "\u7e41\u4f53\u4e2d\u6587",
  },
  { value: "Japanese", label: "\u65e5\u672c\u8a9e" },
  { value: "Korean", label: "\ud55c\uad6d\uc5b4" },
  { value: "French", label: "Fran\u00e7ais" },
  { value: "German", label: "Deutsch" },
  { value: "Spanish", label: "Espa\u00f1ol" },
  { value: "Russian", label: "\u0420\u0443\u0441\u0441\u043a\u0438\u0439" },
];

export default function ParagraphTranslatePage() {
  const { t } = useTranslation(["pages", "common"]);
  const isOpened = useIsOpened();
  const cta = emptyStateCta(isMultiUser());
  const [confirmApplyAllOpen, setConfirmApplyAllOpen] = useState(false);
  const [targetLanguage, setTargetLanguage] = useState("English");
  const [customLanguage, setCustomLanguage] = useState("");
  const paragraphCheckMessages = useMemo(
    () => ({
      checkLoading: t("pages:paragraphTranslate.checkLoading"),
      checkSuccess: (count) =>
        t("pages:paragraphTranslate.checkSuccess", { count }),
      applySuccess: (idx) =>
        t("pages:paragraphTranslate.applySuccess", { idx }),
      applyAllSuccess: (count) =>
        t("pages:paragraphTranslate.applyAllSuccess", { count }),
      applyAllEmpty: t("pages:paragraphTranslate.applyAllEmpty"),
    }),
    [t],
  );
  const isOther = targetLanguage === OTHER_LANGUAGE;
  const effectiveTargetLanguage = isOther ? customLanguage : targetLanguage;

  // 稳定 apiModule 引用：目标语言变化才重建，避免 useParagraphCheck 回调
  // 随页面重渲染反复换身份
  const apiModule = useMemo(
    () => ({
      preview: api.paragraphTranslate.preview,
      apply: api.paragraphTranslate.apply,
      stream: (wsId, filePath, ri) =>
        api.paragraphTranslate.stream(
          wsId,
          filePath,
          ri,
          effectiveTargetLanguage,
        ),
    }),
    [effectiveTargetLanguage],
  );

  const {
    workspaceId,
    activeFile,
    texFiles,
    paragraphs: translateParagraphs,
    results: translateResults,
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
    apiModule,
    checkMethodName: "translate",
    correctedTextField: "translated",
    storeSlice: {
      paragraphsKey: "paragraphTranslateParagraphs",
      setParagraphsKey: "setParagraphTranslateParagraphs",
      resultsKey: "paragraphTranslateResults",
      setResultsKey: "setParagraphTranslateResults",
      loadingKey: "paragraphTranslate",
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
  } = useResultFilter(translateResults, { idPrefix: RESULT_ID_PREFIX });

  const paragraphMap = useMemo(
    () => buildParagraphMap(translateParagraphs),
    [translateParagraphs],
  );

  const groupedParagraphs = useMemo(
    () => groupByFile(translateParagraphs),
    [translateParagraphs],
  );
  const groupedResults = useMemo(
    () => groupByFile(filteredResults),
    [filteredResults],
  );

  const handleTranslate = () => {
    handleCheck();
  };

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:paragraphTranslate.title")}
          groupKey="textReview"
        >
          <ModelBadge role="paragraph_translate" />
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
            title={t("pages:paragraphTranslate.noProjectTitle")}
            description={t("pages:paragraphTranslate.noProjectDesc")}
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
                  {canWrite && (
                    <UndoRedoButtons
                      activeFile={activeFile}
                      workspaceId={workspaceId}
                      onAfterUndoRedo={handlePreview}
                    />
                  )}
                  <div className="flex-1" />
                  <div className="flex items-center gap-2">
                    <Label className="text-sm whitespace-nowrap">
                      {t("pages:paragraphTranslate.targetLanguage")}
                    </Label>
                    <Select
                      value={targetLanguage}
                      onValueChange={setTargetLanguage}
                    >
                      <SelectTrigger className="min-w-40">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TARGET_LANGUAGES.map((lang) => (
                          <SelectItem key={lang.value} value={lang.value}>
                            {lang.label}
                          </SelectItem>
                        ))}
                        <SelectItem value={OTHER_LANGUAGE}>
                          {t("pages:paragraphTranslate.otherLanguage")}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                    {isOther && (
                      <Input
                        className="min-w-40"
                        placeholder={t(
                          "pages:paragraphTranslate.customLanguagePlaceholder",
                        )}
                        value={customLanguage}
                        onChange={(e) => setCustomLanguage(e.target.value)}
                      />
                    )}
                  </div>
                  <Button
                    onClick={handleTranslate}
                    disabled={
                      isLoading ||
                      translateParagraphs.length === 0 ||
                      (isOther && !customLanguage.trim())
                    }
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <Languages />
                    )}
                    {isLoading
                      ? t("pages:paragraphTranslate.translating")
                      : t("pages:paragraphTranslate.translateButton")}
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

            <StaleCheckBanner files={staleFiles} />

            {translateParagraphs.length > 0 &&
              translateResults.length === 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">
                      {t("pages:paragraphTranslate.paragraphPreviewTitle", {
                        count: translateParagraphs.length,
                      })}
                    </CardTitle>
                    <CardDescription>
                      {t("pages:paragraphTranslate.paragraphPreviewDesc")}
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
                          );
                        })}
                      </Tabs>
                    ) : (
                      <div className="space-y-3">
                        {translateParagraphs.map((p) => (
                          <div
                            key={`${activeFile}-${p.index}`}
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

            {translateResults.length > 0 && (
              <div className="space-y-4">
                {translateResults.some((r) => r.translated !== r.original) && (
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
                              fileResults.map((result) => (
                                <TranslateResultCard
                                  key={`${result.file_path}-${result.paragraph_index}`}
                                  result={result}
                                  paragraphs={translateParagraphs}
                                  paragraphMap={paragraphMap}
                                  onApply={handleApplyChange}
                                  onIgnore={handleIgnoreChange}
                                  canWrite={canWrite}
                                />
                              ))
                            )}
                          </div>
                        </TabsContent>
                      );
                    })}
                  </Tabs>
                ) : (
                  filteredResults.map((result) => (
                    <TranslateResultCard
                      key={`${result.file_path}-${result.paragraph_index}`}
                      result={result}
                      paragraphs={translateParagraphs}
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
