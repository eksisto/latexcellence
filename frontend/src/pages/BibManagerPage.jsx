import { useState, useCallback, useEffect, useRef } from "react";
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
import {
  ArrowLeft,
  BookOpen,
  Copy,
  FileText,
  ImageIcon,
  Loader2,
  Save,
  Search,
  Upload,
  X,
  Edit3,
  Check,
  Trash2,
} from "lucide-react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";

export default function BibManagerPage() {
  const { t } = useTranslation(["pages", "common"]);
  const projectPath = useStore((s) => s.projectPath);
  const workspaceId = useStore((s) => s.workspaceId);
  const rootPath = useStore((s) => s.rootPath);

  // Step 1: format analysis
  const [example, setExample] = useState("");
  const [formatResult, setFormatResult] = useState(null);
  const [step1Loading, setStep1Loading] = useState(false);
  const [savedFormats, setSavedFormats] = useState([]);
  const [selectedFormatName, setSelectedFormatName] = useState("");
  const [isEditingFormat, setIsEditingFormat] = useState(false);
  const [editedFormatInfo, setEditedFormatInfo] = useState(null);
  const [savingFormat, setSavingFormat] = useState(false);

  // Step 2: metadata extraction
  const [metadataText, setMetadataText] = useState("");
  const [metadataImage, setMetadataImage] = useState(null);
  const [metadataImagePreview, setMetadataImagePreview] = useState(null);
  const [metadataResult, setMetadataResult] = useState(null);
  const [editedMetadata, setEditedMetadata] = useState(null);
  const [missingFields, setMissingFields] = useState([]);
  const [step2Loading, setStep2Loading] = useState(false);
  const imageInputRef = useRef(null);

  // Step 3: BibTeX generation
  const [bibtexResult, setBibtexResult] = useState(null);
  const [editedBibtex, setEditedBibtex] = useState("");
  const [editedCiteKey, setEditedCiteKey] = useState("");
  const [step3Loading, setStep3Loading] = useState(false);
  const prevCiteKeyRef = useRef("");

  // Step 4: save
  const [bibFiles, setBibFiles] = useState([]);
  const [selectedBibFile, setSelectedBibFile] = useState("");
  const [saving, setSaving] = useState(false);

  // Step 5: LaTeX code generation
  const [latexResult, setLatexResult] = useState(null);
  const [step5Loading, setStep5Loading] = useState(false);
  const [bibFilePath, setBibFilePath] = useState("references");

  // Load .bib files when project is open
  useEffect(() => {
    if (projectPath) {
      api.bibManager
        .listBibFiles(workspaceId)
        .then((data) => {
          setBibFiles(data.bib_files || []);
          if (data.bib_files?.length > 0) {
            setSelectedBibFile(data.bib_files[0]);
          }
        })
        .catch(() => {});
    }
  }, [projectPath]);

  useEffect(() => {
    api.bibManager.formats
      .list()
      .then((data) => setSavedFormats(data.formats || []))
      .catch(() => {});
  }, []);

  // Step 1: analyze format
  const handleAnalyzeFormat = useCallback(async () => {
    if (!example.trim()) return;
    setStep1Loading(true);
    setFormatResult(null);
    try {
      const data = await api.bibManager.analyzeFormat(example.trim());
      setFormatResult(data);
      if (data.confidence === "low") {
        toast.warning(t("pages:bibManager.lowConfidence", { name: data.format_name }), {
          description: t("pages:bibManager.lowConfidenceHint"),
        });
      } else {
        toast.success(t("pages:bibManager.analyzeFormatSuccess", { name: data.format_name }));
      }
    } catch (e) {
      toast.error(t("pages:bibManager.analyzeFormatFailed"), { description: e.message });
    } finally {
      setStep1Loading(false);
    }
  }, [example, t]);

  // Format loading / editing / saving
  const handleLoadSavedFormat = useCallback(async (name) => {
    if (!name) return;
    try {
      const data = await api.bibManager.formats.load(name);
      setFormatResult(data);
      setEditedFormatInfo(data);
      setSelectedFormatName(name);
      toast.success(t("pages:bibManager.loadFormatSuccess", { name: data.format_name }));
    } catch (e) {
      toast.error(t("pages:bibManager.loadFormatFailed"), { description: e.message });
    }
  }, [t]);

  const handleStartEditFormat = useCallback(() => {
    if (formatResult) {
      setEditedFormatInfo({ ...formatResult });
      setIsEditingFormat(true);
    }
  }, [formatResult]);

  const handleCancelEditFormat = useCallback(() => {
    setIsEditingFormat(false);
    setEditedFormatInfo(null);
  }, []);

  const handleSaveEditFormat = useCallback(() => {
    if (!editedFormatInfo) return;
    const parsed = { ...editedFormatInfo };
    if (typeof parsed.required_fields === "string") {
      parsed.required_fields = parsed.required_fields
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    if (typeof parsed.optional_fields === "string") {
      parsed.optional_fields = parsed.optional_fields
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    if (typeof parsed.field_descriptions === "string") {
      try { parsed.field_descriptions = JSON.parse(parsed.field_descriptions); } catch {}
    }
    setFormatResult(parsed);
    setIsEditingFormat(false);
    setEditedFormatInfo(null);
  }, [editedFormatInfo]);

  const handleSaveFormatAs = useCallback(async () => {
    if (!formatResult) return;
    const rawName = (formatResult.format_name || "unnamed").trim();
    const safeName = rawName.replace(/[<>:"/\\|?*\x00-\x1f]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "") || "unnamed";
    setSavingFormat(true);
    try {
      const result = await api.bibManager.formats.save(safeName, formatResult);
      toast.success(t("pages:bibManager.saveFormatSuccess", { name: result.name }));
      const listData = await api.bibManager.formats.list();
      setSavedFormats(listData.formats || []);
    } catch (e) {
      toast.error(t("pages:bibManager.saveFormatFailed"), { description: e.message });
    } finally {
      setSavingFormat(false);
    }
  }, [formatResult, t]);

  const handleDeleteFormat = useCallback(async (name) => {
    try {
      await api.bibManager.formats.delete(name);
      toast.success(t("pages:bibManager.deleteFormatSuccess", { name }));
      if (selectedFormatName === name) setSelectedFormatName("");
      const listData = await api.bibManager.formats.list();
      setSavedFormats(listData.formats || []);
    } catch (e) {
      toast.error(t("pages:bibManager.deleteFormatFailed"), { description: e.message });
    }
  }, [selectedFormatName, t]);

  // Step 2: extract metadata
  const handleExtractMetadata = useCallback(async () => {
    if (!metadataText.trim() && !metadataImage) {
      toast.error(t("pages:bibManager.step2NoInput"));
      return;
    }
    setStep2Loading(true);
    setMetadataResult(null);
    setEditedMetadata(null);
    setMissingFields([]);
    const formData = new FormData();
    if (metadataText.trim()) {
      formData.append("text", metadataText.trim());
    }
    if (metadataImage) {
      formData.append("image", metadataImage);
    }
    if (formatResult) {
      formData.append("format_info", JSON.stringify(formatResult));
    }
    try {
      const data = await api.bibManager.extract(formData);

      // Show toast warnings for missing fields
      if (data.missing_fields?.length > 0) {
        for (const field of data.missing_fields) {
          const fieldInfo = formatResult?.field_descriptions?.[field];
          const label = fieldInfo
            ? fieldInfo.split("\uFF08")[0]
            : field;
          toast.warning(t("pages:bibManager.step2MissingFieldWarning", { field: label }), {
            description: t("pages:bibManager.step2MissingFieldDesc"),
          });
        }
        setMissingFields(data.missing_fields);
      }

      setMetadataResult(data);
      setEditedMetadata({ ...data.fields });
      toast.success(t("pages:bibManager.step2Success"));
    } catch (e) {
      toast.error(t("pages:bibManager.step2Failed"), { description: e.message });
    } finally {
      setStep2Loading(false);
    }
  }, [metadataText, metadataImage, formatResult, t]);

  const handleClearImage = useCallback(() => {
    setMetadataImage(null);
    setMetadataImagePreview(null);
    if (imageInputRef.current) {
      imageInputRef.current.value = "";
    }
  }, []);

  const handleImageChange = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMetadataImage(file);
    const reader = new FileReader();
    reader.onload = () => setMetadataImagePreview(reader.result);
    reader.readAsDataURL(file);
  }, []);

  const handleMetadataEdit = useCallback((field, value) => {
    setEditedMetadata((prev) => ({ ...prev, [field]: value }));
  }, []);

  // Step 3: generate BibTeX
  const handleGenerateBibtex = useCallback(async () => {
    if (!editedMetadata) return;
    setStep3Loading(true);
    setBibtexResult(null);
    setEditedBibtex("");
    setEditedCiteKey("");
    prevCiteKeyRef.current = "";

    try {
      const data = await api.bibManager.generate({
        metadata: { ...editedMetadata },
        format_info: formatResult,
        existing_keys: [],
      });
      setBibtexResult(data);
      setEditedBibtex(data.raw_bibtex);
      setEditedCiteKey(data.cite_key);
      prevCiteKeyRef.current = data.cite_key;
      toast.success(t("pages:bibManager.step3Success"));
    } catch (e) {
      toast.error(t("pages:bibManager.step3Failed"), { description: e.message });
    } finally {
      setStep3Loading(false);
    }
  }, [editedMetadata, formatResult, t]);

  // Sync cite_key change to BibTeX code
  useEffect(() => {
    const prevKey = prevCiteKeyRef.current;
    const newKey = editedCiteKey.trim();
    if (!editedBibtex || !newKey || !prevKey || newKey === prevKey) return;
    setEditedBibtex((prev) =>
      prev.replace(new RegExp(`@(\\w+)\\{${prevKey.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")},`), `@$1{${newKey},`),
    );
    prevCiteKeyRef.current = newKey;
  }, [editedCiteKey]);

  const handleCopyBibtex = useCallback(() => {
    if (!editedBibtex) return;
    navigator.clipboard.writeText(editedBibtex).then(
      () => toast.success(t("pages:bibManager.copySuccess")),
      () => toast.error(t("pages:bibManager.copyFailed")),
    );
  }, [editedBibtex, t]);

  // Step 5: generate LaTeX reference code
  const handleGenerateLatex = useCallback(async () => {
    if (!formatResult) {
      toast.error(t("pages:bibManager.step5NeedFormat"));
      return;
    }
    setStep5Loading(true);
    setLatexResult(null);

    try {
      const data = await api.bibManager.generateLatex({
        metadata: { ...editedMetadata, cite_key: editedCiteKey },
        format_info: formatResult,
        bib_file_path: bibFilePath || "references",
      });
      setLatexResult(data);
      toast.success(t("pages:bibManager.step5Success"));
    } catch (e) {
      toast.error(t("pages:bibManager.step5Failed"), { description: e.message });
    } finally {
      setStep5Loading(false);
    }
  }, [formatResult, editedMetadata, editedCiteKey, bibFilePath, t]);

  const handleCopyLatex = useCallback(() => {
    if (!latexResult?.full_code) return;
    navigator.clipboard.writeText(latexResult.full_code).then(
      () => toast.success(t("pages:bibManager.copySuccess")),
      () => toast.error(t("pages:bibManager.copyFailed")),
    );
  }, [latexResult, t]);

  // Step 4: save to .bib file
  const handleSaveBib = useCallback(async () => {
    if (!editedBibtex.trim() || !selectedBibFile) return;
    setSaving(true);
    try {
      const data = await api.bibManager.save({
        file_path: selectedBibFile,
        bibtex_code: editedBibtex.trim(),
        cite_key: editedCiteKey,
      }, workspaceId);
      const actionKey = data.action === "updated" ? "step4Updated" : "step4Appended";
      toast.success(t("pages:bibManager.step4SaveSuccess", { action: t(`pages:bibManager.${actionKey}`), file: selectedBibFile }));
    } catch (e) {
      toast.error(t("pages:bibManager.step4SaveFailed"), { description: e.message });
    } finally {
      setSaving(false);
    }
  }, [editedBibtex, editedCiteKey, selectedBibFile, t]);

  const hasResult = formatResult || metadataResult || bibtexResult || latexResult;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">{t("pages:bibManager.title")}</h1>
        </div>
      </div>

      {!projectPath ? (
        <div className="max-w-4xl mx-auto">
          <Card>
            <CardHeader>
              <CardTitle>{t("pages:bibManager.noProjectTitle")}</CardTitle>
              <CardDescription>
                {t("pages:bibManager.noProjectDesc")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button className="w-full" nativeButton={false} render={<Link to="/workspace" />}>
                {t("common:openWorkspace")}
              </Button>
            </CardContent>
          </Card>
        </div>
      ) : (
        <div className="max-w-4xl mx-auto space-y-6">
          {/* Step 1: Format Analysis */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <span className="text-primary text-sm font-bold mr-1">
                  1
                </span>
                <CardTitle className="text-base">{t("pages:bibManager.step1Title")}</CardTitle>
              </div>
              <CardDescription>
                {t("pages:bibManager.step1Desc")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {savedFormats.length > 0 && (
                <div className="space-y-1.5">
                  <Label className="text-xs">{t("pages:bibManager.loadSavedFormat")}</Label>
                  <div className="flex items-center gap-2">
                    <Select value={selectedFormatName} onValueChange={handleLoadSavedFormat}>
                      <SelectTrigger className="min-w-48">
                        <SelectValue placeholder={t("pages:bibManager.selectSavedFormat")}>
                          {selectedFormatName
                            ? (savedFormats.find((f) => f.name === selectedFormatName)
                                ?.format_name || selectedFormatName)
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
                        onClick={() => handleDeleteFormat(selectedFormatName)}
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
                {step1Loading ? t("pages:bibManager.step1BtnLoading") : t("pages:bibManager.step1Btn")}
              </Button>

              {formatResult && (
                <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                  {isEditingFormat && editedFormatInfo ? (
                    <>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1">
                          <Label className="text-xs">{t("pages:bibManager.formatName")}</Label>
                          <Input
                            className="h-8 text-xs"
                            value={editedFormatInfo.format_name || ""}
                            onChange={(e) =>
                              setEditedFormatInfo({
                                ...editedFormatInfo,
                                format_name: e.target.value,
                              })
                            }
                          />
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs">{t("pages:bibManager.bibtexType")}</Label>
                          <Input
                            className="h-8 text-xs"
                            value={editedFormatInfo.entry_type || ""}
                            onChange={(e) =>
                              setEditedFormatInfo({
                                ...editedFormatInfo,
                                entry_type: e.target.value,
                              })
                            }
                          />
                        </div>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">{t("pages:bibManager.requiredFields")}</Label>
                        <Input
                          className="h-8 text-xs"
                          value={
                            Array.isArray(editedFormatInfo.required_fields)
                              ? editedFormatInfo.required_fields.join(", ")
                              : editedFormatInfo.required_fields || ""
                          }
                          onChange={(e) =>
                            setEditedFormatInfo({
                              ...editedFormatInfo,
                              required_fields: e.target.value,
                            })
                          }
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">{t("pages:bibManager.optionalFields")}</Label>
                        <Input
                          className="h-8 text-xs"
                          value={
                            Array.isArray(editedFormatInfo.optional_fields)
                              ? editedFormatInfo.optional_fields.join(", ")
                              : editedFormatInfo.optional_fields || ""
                          }
                          onChange={(e) =>
                            setEditedFormatInfo({
                              ...editedFormatInfo,
                              optional_fields: e.target.value,
                            })
                          }
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <Button size="sm" onClick={handleSaveEditFormat}>
                          <Check />
                          {t("pages:bibManager.confirmEdit")}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={handleCancelEditFormat}
                        >
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
                                {" "}{formatResult.latex_package}
                              </span>
                              <span className="text-xs text-muted-foreground">
                                ）
                              </span>
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
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={handleStartEditFormat}
                        >
                          <Edit3 />
                          {t("pages:bibManager.editFormat")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={handleSaveFormatAs}
                          disabled={savingFormat}
                        >
                          {savingFormat ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Save />
                          )}
                          {t("pages:bibManager.saveFormat")}
                        </Button>
                      </div>
                    </>
                  )}
                </div>
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
                <CardTitle className="text-base">{t("pages:bibManager.step2Title")}</CardTitle>
              </div>
              <CardDescription>
                {t("pages:bibManager.step2Desc")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                className="min-h-20 resize-none"
                value={metadataText}
                onChange={(e) => setMetadataText(e.target.value)}
                placeholder={t("pages:bibManager.step2Placeholder")}
              />

              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">{t("pages:bibManager.step2Or")}</span>
                <label className="cursor-pointer inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
                  <Upload className="size-4" />
                  {t("pages:bibManager.step2Upload")}
                  <input
                    ref={imageInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    onChange={handleImageChange}
                    className="hidden"
                  />
                </label>
                {metadataImagePreview && (
                  <div className="flex items-center gap-2">
                    <img
                      src={metadataImagePreview}
                      alt="preview"
                      className="h-8 w-auto rounded border border-border object-contain"
                    />
                    <span className="text-xs text-muted-foreground truncate max-w-40">
                      {metadataImage?.name}
                    </span>
                    <Button variant="ghost" size="icon" className="size-6" onClick={handleClearImage}>
                      <X className="size-3" />
                    </Button>
                  </div>
                )}
              </div>

              <Button
                onClick={handleExtractMetadata}
                disabled={step2Loading || (!metadataText.trim() && !metadataImage)}
              >
                {step2Loading ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <FileText />
                )}
                {step2Loading ? t("pages:bibManager.step2BtnLoading") : t("pages:bibManager.step2Btn")}
              </Button>

              {editedMetadata && (
                <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{t("pages:bibManager.step2ResultTitle")}</span>
                    {missingFields.length > 0 && (
                      <span className="text-xs text-destructive">
                        {t("pages:bibManager.step2MissingFields", { count: missingFields.length })}
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {editedMetadata && Object.keys(editedMetadata).map((fieldKey) => {
                      const isMissing = missingFields.includes(fieldKey);
                      const desc = formatResult?.field_descriptions?.[fieldKey];
                      const label = desc || fieldKey;
                      const value = editedMetadata[fieldKey] || "";
                      const isLong = value.length > 80 || fieldKey.includes("abstract") || (desc && (desc.includes("\u6458\u8981") || desc.includes("\u8BF4\u660E")));
                      return (
                        <div key={fieldKey} className="space-y-1">
                          <Label className={`text-xs ${isMissing ? "text-destructive" : ""}`}>
                            {label}
                            {isMissing && (
                              <span className="ml-1 text-destructive">*</span>
                            )}
                          </Label>
                          {isLong ? (
                            <Textarea
                              className="min-h-16 resize-none text-xs"
                              value={value}
                              onChange={(e) =>
                                handleMetadataEdit(fieldKey, e.target.value)
                              }
                            />
                          ) : (
                            <Input
                              className={`h-8 text-xs ${isMissing ? "border-destructive" : ""}`}
                              value={value}
                              onChange={(e) =>
                                handleMetadataEdit(fieldKey, e.target.value)
                              }
                              placeholder={
                                isMissing ? t("pages:bibManager.step2FieldMissing") : ""
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
                <CardTitle className="text-base">{t("pages:bibManager.step3Title")}</CardTitle>
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
                  <BookOpen />
                )}
                {step3Loading ? t("pages:bibManager.step3BtnLoading") : t("pages:bibManager.step3Btn")}
              </Button>

              {editedBibtex && (
                <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{t("pages:bibManager.step3ResultTitle")}</span>
                    <div className="flex items-center gap-2">
                      <Button variant="outline" size="sm" onClick={handleCopyBibtex}>
                        <Copy />
                        {t("common:copy")}
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">{t("pages:bibManager.step3CiteKeyLabel")}</Label>
                    <Input
                      className="h-8 text-xs font-mono"
                      value={editedCiteKey}
                      onChange={(e) => setEditedCiteKey(e.target.value)}
                    />
                  </div>
                  <Textarea
                    className="min-h-48 resize-none font-mono text-xs"
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
                  <CardTitle className="text-base">{t("pages:bibManager.step4Title")}</CardTitle>
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
                      <Label className="text-xs">{t("pages:bibManager.step4SelectBibFile")}</Label>
                      <Select value={selectedBibFile} onValueChange={setSelectedBibFile}>
                        <SelectTrigger className="min-w-60">
                          <SelectValue placeholder={t("pages:bibManager.step4SelectBibFile")} />
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
                      disabled={saving || !selectedBibFile || !editedBibtex.trim()}
                    >
                      {saving ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <Save />
                      )}
                      {saving ? t("pages:bibManager.step4BtnLoading") : t("pages:bibManager.step4Btn")}
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
                  <CardTitle className="text-base">{t("pages:bibManager.step5Title")}</CardTitle>
                </div>
                <CardDescription>
                  {t("pages:bibManager.step5Desc")}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">{t("pages:bibManager.step5BibFilePath")}</Label>
                    <Input
                      className="h-8 text-xs font-mono"
                      value={bibFilePath}
                      onChange={(e) => setBibFilePath(e.target.value)}
                      placeholder="references"
                    />
                  </div>
                  {formatResult.latex_bibliographystyle && (
                    <div className="space-y-1.5">
                      <Label className="text-xs">{t("pages:bibManager.step5RecommendedStyle")}</Label>
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
                  {step5Loading ? t("pages:bibManager.step5BtnLoading") : t("pages:bibManager.step5Btn")}
                </Button>

                {latexResult && (
                  <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{t("pages:bibManager.step5ResultTitle")}</span>
                      <Button variant="outline" size="sm" onClick={handleCopyLatex}>
                        <Copy />
                        {t("common:copy")}
                      </Button>
                    </div>
                    <Textarea
                      className="min-h-32 resize-none font-mono text-xs"
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
      )}
    </div>
  );
}
