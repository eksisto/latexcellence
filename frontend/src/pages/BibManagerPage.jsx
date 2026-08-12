import React, { useState, useMemo } from "react";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  BookOpen,
  BookOpenText,
  Copy,
  FileText,
  Loader2,
  Save,
  Search,
  Upload,
  X,
  Edit3,
  Check,
  Trash2,
  Library,
  Plus,
} from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import BibManagerLibrary from "@/components/BibManagerLibrary";
import PageHeader from "@/components/PageHeader";
import { latexEditorClass } from "@/components/CodeBlock";
import { cn } from "@/lib/utils";
import EmptyState from "@/components/EmptyState";
import useStore from "@/store/useStore";
import useFormatAnalysis from "@/hooks/bib-manager/useFormatAnalysis";
import useMetadataExtraction from "@/hooks/bib-manager/useMetadataExtraction";
import useBibtexGeneration from "@/hooks/bib-manager/useBibtexGeneration";
import useLatexGeneration from "@/hooks/bib-manager/useLatexGeneration";
import useBibFileSave from "@/hooks/bib-manager/useBibFileSave";

const FormatResultCard = React.memo(function FormatResultCard({
  formatResult,
  isEditingFormat,
  editedFormatInfo,
  savingFormat,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onSaveAs,
  onEditedFormatInfoChange,
}) {
  const { t } = useTranslation(["pages", "common"]);

  if (!formatResult) return null;

  return (
    <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
      {isEditingFormat && editedFormatInfo ? (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">
                {t("pages:bibManager.formatName")}
              </Label>
              <Input
                className="h-8 text-xs"
                value={editedFormatInfo.format_name || ""}
                onChange={(e) =>
                  onEditedFormatInfoChange((prev) => ({
                    ...prev,
                    format_name: e.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">
                {t("pages:bibManager.bibtexType")}
              </Label>
              <Input
                className="h-8 text-xs"
                value={editedFormatInfo.entry_type || ""}
                onChange={(e) =>
                  onEditedFormatInfoChange((prev) => ({
                    ...prev,
                    entry_type: e.target.value,
                  }))
                }
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              {t("pages:bibManager.requiredFields")}
            </Label>
            <Input
              className="h-8 text-xs"
              value={
                Array.isArray(editedFormatInfo.required_fields)
                  ? editedFormatInfo.required_fields.join(", ")
                  : editedFormatInfo.required_fields || ""
              }
              onChange={(e) =>
                onEditedFormatInfoChange((prev) => ({
                  ...prev,
                  required_fields: e.target.value,
                }))
              }
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              {t("pages:bibManager.optionalFields")}
            </Label>
            <Input
              className="h-8 text-xs"
              value={
                Array.isArray(editedFormatInfo.optional_fields)
                  ? editedFormatInfo.optional_fields.join(", ")
                  : editedFormatInfo.optional_fields || ""
              }
              onChange={(e) =>
                onEditedFormatInfoChange((prev) => ({
                  ...prev,
                  optional_fields: e.target.value,
                }))
              }
            />
          </div>
          <div className="col-span-2 pt-1 border-t border-border space-y-1">
            <Label className="text-xs font-semibold">
              {t("pages:bibManager.latexStyleFields")}
            </Label>
            <p className="text-xs text-muted-foreground">
              {t("pages:bibManager.latexStyleFieldsHint")}
            </p>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              {t("pages:bibManager.latexBackend")}
            </Label>
            <Select
              value={editedFormatInfo.latex_backend || "none"}
              onValueChange={(value) =>
                onEditedFormatInfoChange((prev) => ({
                  ...prev,
                  latex_backend: value === "none" ? null : value,
                }))
              }
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">
                  {t("pages:bibManager.noStyle")}
                </SelectItem>
                <SelectItem value="bibtex">bibtex</SelectItem>
                <SelectItem value="biblatex">biblatex</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              {t("pages:bibManager.latexPackage")}
            </Label>
            <Input
              className="h-8 text-xs font-mono"
              value={editedFormatInfo.latex_package || ""}
              onChange={(e) =>
                onEditedFormatInfoChange((prev) => ({
                  ...prev,
                  latex_package: e.target.value,
                }))
              }
              placeholder="natbib / biblatex-chicago / ..."
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              {t("pages:bibManager.latexBibliographystyle")}
            </Label>
            <Input
              className="h-8 text-xs font-mono"
              value={editedFormatInfo.latex_bibliographystyle || ""}
              onChange={(e) =>
                onEditedFormatInfoChange((prev) => ({
                  ...prev,
                  latex_bibliographystyle: e.target.value,
                }))
              }
              placeholder="plainnat / ACM-Reference-Format / ..."
            />
          </div>
          <div className="col-span-2 space-y-1">
            <Label className="text-xs">
              {t("pages:bibManager.latexPreamble")}
            </Label>
            <Textarea
              className="min-h-20 text-xs font-mono"
              value={editedFormatInfo.latex_preamble || ""}
              onChange={(e) =>
                onEditedFormatInfoChange((prev) => ({
                  ...prev,
                  latex_preamble: e.target.value,
                }))
              }
              placeholder={"\\usepackage{natbib}"}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={onSaveEdit}>
              <Check />
              {t("pages:bibManager.confirmEdit")}
            </Button>
            <Button size="sm" variant="outline" onClick={onCancelEdit}>
              {t("pages:bibManager.cancelEdit")}
            </Button>
          </div>
        </>
      ) : (
        <>
          <div>
            <span className="text-xs text-muted-foreground">
              {t("pages:bibManager.identifiedFormat")}
            </span>
            <span className="ml-1 text-sm font-semibold">
              {formatResult.format_name}
            </span>
            <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {formatResult.entry_type}
            </span>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">
              {t("pages:bibManager.requiredFieldsLabel")}
            </span>
            <div className="flex flex-wrap gap-1 mt-1">
              {formatResult.required_fields?.map((f) => (
                <span
                  key={f}
                  className="rounded bg-red-500/10 text-red-600 dark:text-red-400 px-1.5 py-0.5 text-xs"
                >
                  {f}
                </span>
              ))}
            </div>
          </div>
          {formatResult.optional_fields?.length > 0 && (
            <div>
              <span className="text-xs text-muted-foreground">
                {t("pages:bibManager.optionalFieldsLabel")}
              </span>
              <div className="flex flex-wrap gap-1 mt-1">
                {formatResult.optional_fields.map((f) => (
                  <span
                    key={f}
                    className="rounded bg-muted px-1.5 py-0.5 text-xs"
                  >
                    {f}
                  </span>
                ))}
              </div>
            </div>
          )}
          {formatResult.format_description && (
            <p className="text-xs text-muted-foreground">
              {formatResult.format_description}
            </p>
          )}
          {!formatResult.latex_backend &&
            !formatResult.latex_package &&
            !formatResult.latex_bibliographystyle &&
            !formatResult.latex_preamble && (
              <p className="text-xs text-muted-foreground">
                {t("pages:bibManager.customStyleHint")}
              </p>
            )}
          {formatResult.latex_bibliographystyle && (
            <div className="rounded bg-muted/50 p-2 mt-1">
              <span className="text-xs text-muted-foreground">
                {t("pages:bibManager.recommendedStyle")}
              </span>
              <span className="ml-1 text-xs font-mono font-semibold">
                {formatResult.latex_bibliographystyle}
              </span>
              {formatResult.latex_package && (
                <>
                  <span className="text-xs text-muted-foreground ml-2">
                    {t("pages:bibManager.withPackage")}
                  </span>
                  <span className="text-xs font-mono">
                    {" "}
                    {formatResult.latex_package}
                  </span>
                  <span className="text-xs text-muted-foreground">）</span>
                </>
              )}
              {formatResult.latex_backend && (
                <span className="text-xs text-muted-foreground ml-2">
                  [{formatResult.latex_backend}]
                </span>
              )}
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button variant="outline" size="sm" onClick={onStartEdit}>
              <Edit3 />
              {t("pages:bibManager.editFormat")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onSaveAs}
              disabled={savingFormat}
            >
              {savingFormat ? <Loader2 className="animate-spin" /> : <Save />}
              {t("pages:bibManager.saveFormat")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
});

export default function BibManagerPage() {
  const { t } = useTranslation(["pages", "common"]);
  const projectPath = useStore((s) => s.projectPath);
  const workspaceId = useStore((s) => s.workspaceId);

  const [activeTab, setActiveTab] = useState("add");

  const {
    example,
    setExample,
    formatResult,
    setFormatResult,
    step1Loading,
    savedFormats,
    selectedFormatName,
    setSelectedFormatName,
    isEditingFormat,
    editedFormatInfo,
    setEditedFormatInfo,
    savingFormat,
    handleAnalyzeFormat,
    handleLoadSavedFormat,
    handleStartEditFormat,
    handleCancelEditFormat,
    handleSaveEditFormat,
    handleSaveFormatAs,
    handleDeleteFormat,
  } = useFormatAnalysis();

  const {
    metadataText,
    setMetadataText,
    metadataImages,
    metadataImagePreviews,
    metadataResult,
    editedMetadata,
    setEditedMetadata,
    missingFields,
    step2Loading,
    imageInputRef,
    doiInput,
    setDoiInput,
    doiLoading,
    doiResult,
    handleExtractMetadata,
    handleClearImage,
    handleClearAllImages,
    handleImageChange,
    handleDoiLookup,
    handleMetadataEdit,
  } = useMetadataExtraction({ formatResult });

  const {
    bibtexResult,
    editedBibtex,
    setEditedBibtex,
    editedCiteKey,
    setEditedCiteKey,
    step3Loading,
    handleGenerateBibtex,
    handleCopyBibtex,
  } = useBibtexGeneration({ editedMetadata, formatResult });

  const {
    latexResult,
    step5Loading,
    bibFilePath,
    setBibFilePath,
    handleGenerateLatex,
    handleCopyLatex,
  } = useLatexGeneration({ formatResult, editedMetadata, editedCiteKey });

  const {
    bibFiles,
    selectedBibFile,
    setSelectedBibFile,
    saving,
    handleSaveBib,
  } = useBibFileSave({
    projectPath,
    workspaceId,
    editedBibtex,
    editedCiteKey,
  });

  const missingFieldSet = useMemo(
    () => new Set(missingFields),
    [missingFields],
  );

  const hasResult =
    formatResult || metadataResult || bibtexResult || latexResult;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:bibManager.title")}
          groupKey="contentGeneration"
        />
      </div>

      {!projectPath ? (
        <div className="max-w-4xl mx-auto">
          <EmptyState
            icon={Library}
            title={t("pages:bibManager.noProjectTitle")}
            description={t("pages:bibManager.noProjectDesc")}
          >
            <Button nativeButton={false} render={<Link to="/workspace" />}>
              {t("common:openWorkspace")}
            </Button>
          </EmptyState>
        </div>
      ) : (
        <div className="max-w-6xl mx-auto">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="mx-auto mb-6">
              <TabsTrigger value="add">
                <Plus className="size-4" />
                {t("pages:bibManager.addTab")}
              </TabsTrigger>
              <TabsTrigger value="library">
                <Library className="size-4" />
                {t("pages:bibManager.library.shortTitle")}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="add">
              <div
                key={`add-${workspaceId}`}
                className="max-w-4xl mx-auto space-y-6 animate-tab-enter"
              >
                {/* Step 1: Format Analysis */}
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-primary text-sm font-bold mr-1">
                        1
                      </span>
                      <CardTitle className="text-base">
                        {t("pages:bibManager.step1Title")}
                      </CardTitle>
                    </div>
                    <CardDescription>
                      {t("pages:bibManager.step1Desc")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {savedFormats.length > 0 && (
                      <div className="space-y-1.5">
                        <Label className="text-xs">
                          {t("pages:bibManager.loadSavedFormat")}
                        </Label>
                        <div className="flex items-center gap-2">
                          <Select
                            value={selectedFormatName}
                            onValueChange={handleLoadSavedFormat}
                          >
                            <SelectTrigger className="min-w-48">
                              <SelectValue
                                placeholder={t(
                                  "pages:bibManager.selectSavedFormat",
                                )}
                              >
                                {selectedFormatName
                                  ? savedFormats.find(
                                      (f) => f.name === selectedFormatName,
                                    )?.format_name || selectedFormatName
                                  : null}
                              </SelectValue>
                            </SelectTrigger>
                            <SelectContent>
                              {savedFormats.map((f) => (
                                <SelectItem key={f.name} value={f.name}>
                                  <span className="flex items-center gap-2">
                                    <span>{f.format_name}</span>
                                    <span className="text-xs text-muted-foreground">
                                      ({f.entry_type})
                                    </span>
                                  </span>
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {selectedFormatName && (
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
                              onClick={() =>
                                handleDeleteFormat(selectedFormatName)
                              }
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                          )}
                        </div>
                      </div>
                    )}

                    <Textarea
                      className="min-h-24 resize-none"
                      value={example}
                      onChange={(e) => setExample(e.target.value)}
                      placeholder={t("pages:bibManager.step1Placeholder")}
                    />
                    <Button
                      onClick={handleAnalyzeFormat}
                      disabled={step1Loading || !example.trim()}
                    >
                      {step1Loading ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <Search />
                      )}
                      {step1Loading
                        ? t("pages:bibManager.step1BtnLoading")
                        : t("pages:bibManager.step1Btn")}
                    </Button>

                    {formatResult && (
                      <FormatResultCard
                        formatResult={formatResult}
                        isEditingFormat={isEditingFormat}
                        editedFormatInfo={editedFormatInfo}
                        savingFormat={savingFormat}
                        onStartEdit={handleStartEditFormat}
                        onCancelEdit={handleCancelEditFormat}
                        onSaveEdit={handleSaveEditFormat}
                        onSaveAs={handleSaveFormatAs}
                        onEditedFormatInfoChange={setEditedFormatInfo}
                      />
                    )}
                  </CardContent>
                </Card>

                {/* Step 2: Metadata Extraction */}
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-primary text-sm font-bold mr-1">
                        2
                      </span>
                      <CardTitle className="text-base">
                        {t("pages:bibManager.step2Title")}
                      </CardTitle>
                    </div>
                    <CardDescription>
                      {t("pages:bibManager.step2Desc")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {/* DOI Lookup Section */}
                    <div className="rounded-lg border border-dashed border-primary/30 bg-primary/5 p-4 space-y-3">
                      <div className="flex items-center gap-2">
                        <BookOpen className="size-4 text-primary" />
                        <span className="text-sm font-medium">
                          {t("pages:bibManager.doiLookupTitle")}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {t("pages:bibManager.doiLookupDesc")}
                      </p>
                      <div className="flex items-center gap-2">
                        <Input
                          className="flex-1 h-8 text-xs font-mono"
                          value={doiInput}
                          onChange={(e) => setDoiInput(e.target.value)}
                          placeholder={t(
                            "pages:bibManager.doiLookupPlaceholder",
                          )}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleDoiLookup();
                          }}
                        />
                        <Button
                          size="sm"
                          onClick={handleDoiLookup}
                          disabled={doiLoading || !doiInput.trim()}
                        >
                          {doiLoading ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Search />
                          )}
                          {doiLoading
                            ? t("pages:bibManager.doiLookupBtnLoading")
                            : t("pages:bibManager.doiLookupBtn")}
                        </Button>
                      </div>
                      {doiResult && (
                        <div className="rounded bg-muted/50 p-2 text-xs">
                          <span className="text-muted-foreground">
                            {t("pages:bibManager.doiLookupFound")}:
                          </span>
                          <span className="ml-1 font-medium">
                            {doiResult.title}
                          </span>
                        </div>
                      )}
                    </div>

                    <div className="flex items-center gap-3">
                      <span className="text-xs text-muted-foreground">
                        {t("pages:bibManager.step2OrManual")}
                      </span>
                    </div>

                    <Textarea
                      className="min-h-20 resize-none"
                      value={metadataText}
                      onChange={(e) => setMetadataText(e.target.value)}
                      placeholder={t("pages:bibManager.step2Placeholder")}
                    />

                    <div className="flex items-center gap-3">
                      <span className="text-xs text-muted-foreground">
                        {t("pages:bibManager.step2Or")}
                      </span>
                      <label className="cursor-pointer inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
                        <Upload className="size-4" />
                        {t("pages:bibManager.step2Upload")}
                        <input
                          ref={imageInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          multiple
                          onChange={handleImageChange}
                          className="hidden"
                        />
                      </label>
                      {metadataImagePreviews.length > 0 && (
                        <div className="flex items-center gap-2 flex-wrap">
                          {metadataImagePreviews.map((preview, idx) => (
                            <div key={idx} className="flex items-center gap-2">
                              <img
                                src={preview}
                                alt={`preview ${idx + 1}`}
                                className="h-8 w-auto rounded border border-border object-contain"
                              />
                              <span className="text-xs text-muted-foreground truncate max-w-40">
                                {metadataImages[idx]?.name}
                              </span>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-6"
                                onClick={() => handleClearImage(idx)}
                              >
                                <X className="size-3" />
                              </Button>
                            </div>
                          ))}
                          {metadataImagePreviews.length > 1 && (
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-6 text-muted-foreground hover:text-destructive"
                              onClick={handleClearAllImages}
                            >
                              <X className="size-3" />
                            </Button>
                          )}
                        </div>
                      )}
                    </div>

                    <Button
                      onClick={handleExtractMetadata}
                      disabled={
                        step2Loading ||
                        (!metadataText.trim() && metadataImages.length === 0)
                      }
                    >
                      {step2Loading ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <FileText />
                      )}
                      {step2Loading
                        ? t("pages:bibManager.step2BtnLoading")
                        : t("pages:bibManager.step2Btn")}
                    </Button>

                    {editedMetadata && (
                      <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium">
                            {t("pages:bibManager.step2ResultTitle")}
                          </span>
                          {missingFields.length > 0 && (
                            <span className="text-xs text-destructive">
                              {t("pages:bibManager.step2MissingFields", {
                                count: missingFields.length,
                              })}
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                          {editedMetadata &&
                            Object.keys(editedMetadata).map((fieldKey) => {
                              const isMissing = missingFieldSet.has(fieldKey);
                              const desc =
                                formatResult?.field_descriptions?.[fieldKey];
                              const label = fieldKey;
                              const value = editedMetadata[fieldKey] || "";
                              const isLong =
                                value.length > 80 ||
                                fieldKey.includes("abstract") ||
                                (desc &&
                                  (desc.includes("\u6458\u8981") ||
                                    desc.includes("\u8BF4\u660E")));
                              return (
                                <div key={fieldKey} className="space-y-1">
                                  <Label
                                    className={`text-xs ${isMissing ? "text-destructive" : ""}`}
                                  >
                                    {label}
                                    {isMissing && (
                                      <span className="ml-1 text-destructive">
                                        *
                                      </span>
                                    )}
                                  </Label>
                                  {isLong ? (
                                    <Textarea
                                      className="min-h-16 resize-none text-xs"
                                      value={value}
                                      onChange={(e) =>
                                        handleMetadataEdit(
                                          fieldKey,
                                          e.target.value,
                                        )
                                      }
                                    />
                                  ) : (
                                    <Input
                                      className={`h-8 text-xs ${isMissing ? "border-destructive" : ""}`}
                                      value={value}
                                      onChange={(e) =>
                                        handleMetadataEdit(
                                          fieldKey,
                                          e.target.value,
                                        )
                                      }
                                      placeholder={
                                        isMissing
                                          ? t(
                                              "pages:bibManager.step2FieldMissing",
                                            )
                                          : ""
                                      }
                                    />
                                  )}
                                </div>
                              );
                            })}
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Step 3: BibTeX Generation */}
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-primary text-sm font-bold mr-1">
                        3
                      </span>
                      <CardTitle className="text-base">
                        {t("pages:bibManager.step3Title")}
                      </CardTitle>
                    </div>
                    <CardDescription>
                      {t("pages:bibManager.step3Desc")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Button
                      onClick={handleGenerateBibtex}
                      disabled={step3Loading || !editedMetadata}
                    >
                      {step3Loading ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <BookOpenText />
                      )}
                      {step3Loading
                        ? t("pages:bibManager.step3BtnLoading")
                        : t("pages:bibManager.step3Btn")}
                    </Button>

                    {editedBibtex && (
                      <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium">
                            {t("pages:bibManager.step3ResultTitle")}
                          </span>
                          <div className="flex items-center gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={handleCopyBibtex}
                            >
                              <Copy />
                              {t("common:copy")}
                            </Button>
                          </div>
                        </div>
                        <div className="space-y-1.5">
                          <Label className="text-xs">
                            {t("pages:bibManager.step3CiteKeyLabel")}
                          </Label>
                          <Input
                            className="h-8 text-xs font-mono"
                            value={editedCiteKey}
                            onChange={(e) => setEditedCiteKey(e.target.value)}
                          />
                        </div>
                        <Textarea
                          className={cn(
                            latexEditorClass,
                            "min-h-48 resize-none text-xs",
                          )}
                          value={editedBibtex}
                          onChange={(e) => setEditedBibtex(e.target.value)}
                        />
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* Step 4: Apply to .bib file */}
                {editedBibtex && (
                  <Card>
                    <CardHeader className="pb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-primary text-sm font-bold mr-1">
                          4
                        </span>
                        <CardTitle className="text-base">
                          {t("pages:bibManager.step4Title")}
                        </CardTitle>
                      </div>
                      <CardDescription>
                        {t("pages:bibManager.step4Desc")}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {bibFiles.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          {t("pages:bibManager.step4NoBibFiles")}
                        </p>
                      ) : (
                        <div className="space-y-3">
                          <div className="space-y-1.5">
                            <Label className="text-xs">
                              {t("pages:bibManager.step4SelectBibFile")}
                            </Label>
                            <Select
                              value={selectedBibFile}
                              onValueChange={setSelectedBibFile}
                            >
                              <SelectTrigger className="min-w-60">
                                <SelectValue
                                  placeholder={t(
                                    "pages:bibManager.step4SelectBibFile",
                                  )}
                                />
                              </SelectTrigger>
                              <SelectContent>
                                {bibFiles.map((f) => (
                                  <SelectItem key={f} value={f}>
                                    {f}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <Button
                            onClick={handleSaveBib}
                            disabled={
                              saving || !selectedBibFile || !editedBibtex.trim()
                            }
                          >
                            {saving ? (
                              <Loader2 className="animate-spin" />
                            ) : (
                              <Save />
                            )}
                            {saving
                              ? t("pages:bibManager.step4BtnLoading")
                              : t("pages:bibManager.step4Btn")}
                          </Button>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                {/* Step 5: Generate LaTeX reference code */}
                {formatResult && (
                  <Card>
                    <CardHeader className="pb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-primary text-sm font-bold mr-1">
                          5
                        </span>
                        <CardTitle className="text-base">
                          {t("pages:bibManager.step5Title")}
                        </CardTitle>
                      </div>
                      <CardDescription>
                        {t("pages:bibManager.step5Desc")}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="space-y-1.5">
                          <Label className="text-xs">
                            {t("pages:bibManager.step5BibFilePath")}
                          </Label>
                          <Input
                            className="h-8 text-xs font-mono"
                            value={bibFilePath}
                            onChange={(e) => setBibFilePath(e.target.value)}
                            placeholder="references"
                          />
                        </div>
                        {formatResult.latex_bibliographystyle && (
                          <div className="space-y-1.5">
                            <Label className="text-xs">
                              {t("pages:bibManager.step5RecommendedStyle")}
                            </Label>
                            <div className="h-8 flex items-center text-xs font-mono bg-muted/50 rounded px-2">
                              {formatResult.latex_bibliographystyle}
                            </div>
                          </div>
                        )}
                      </div>
                      <Button
                        onClick={handleGenerateLatex}
                        disabled={step5Loading}
                      >
                        {step5Loading ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <FileText />
                        )}
                        {step5Loading
                          ? t("pages:bibManager.step5BtnLoading")
                          : t("pages:bibManager.step5Btn")}
                      </Button>

                      {latexResult && (
                        <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">
                              {t("pages:bibManager.step5ResultTitle")}
                            </span>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={handleCopyLatex}
                            >
                              <Copy />
                              {t("common:copy")}
                            </Button>
                          </div>
                          <Textarea
                            className={cn(
                              latexEditorClass,
                              "min-h-32 resize-none text-xs",
                            )}
                            value={latexResult.full_code}
                            readOnly
                          />
                          <p className="text-xs text-muted-foreground">
                            {t("pages:bibManager.step5Hint")}
                          </p>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}
              </div>
            </TabsContent>
            <TabsContent value="library">
              <div key={`library-${workspaceId}`} className="animate-tab-enter">
                <BibManagerLibrary />
              </div>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
