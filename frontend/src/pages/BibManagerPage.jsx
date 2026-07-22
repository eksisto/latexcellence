import { useState, useCallback, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
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
        toast.warning(`格式置信度较低：${data.format_name}`, {
          description: "建议检查识别结果或尝试更清晰的示例",
        });
      } else {
        toast.success(`已识别格式：${data.format_name}`);
      }
    } catch (e) {
      toast.error("格式识别失败", { description: e.message });
    } finally {
      setStep1Loading(false);
    }
  }, [example]);

  // Format loading / editing / saving
  const handleLoadSavedFormat = useCallback(async (name) => {
    if (!name) return;
    try {
      const data = await api.bibManager.formats.load(name);
      setFormatResult(data);
      setEditedFormatInfo(data);
      setSelectedFormatName(name);
      toast.success(`已加载格式：${data.format_name}`);
    } catch (e) {
      toast.error("加载格式失败", { description: e.message });
    }
  }, []);

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
      toast.success(`格式已保存：${result.name}`);
      const listData = await api.bibManager.formats.list();
      setSavedFormats(listData.formats || []);
    } catch (e) {
      toast.error("保存格式失败", { description: e.message });
    } finally {
      setSavingFormat(false);
    }
  }, [formatResult]);

  const handleDeleteFormat = useCallback(async (name) => {
    try {
      await api.bibManager.formats.delete(name);
      toast.success(`已删除格式：${name}`);
      if (selectedFormatName === name) setSelectedFormatName("");
      const listData = await api.bibManager.formats.list();
      setSavedFormats(listData.formats || []);
    } catch (e) {
      toast.error("删除格式失败", { description: e.message });
    }
  }, [selectedFormatName]);

  // Step 2: extract metadata
  const handleExtractMetadata = useCallback(async () => {
    if (!metadataText.trim() && !metadataImage) {
      toast.error("请输入文本或上传图片");
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
            ? fieldInfo.split("（")[0]
            : field;
          toast.warning(`缺少必填字段：${label}`, {
            description: "该字段将保留为空",
          });
        }
        setMissingFields(data.missing_fields);
      }

      setMetadataResult(data);
      setEditedMetadata({ ...data.fields });
      toast.success("元数据提取完成");
    } catch (e) {
      toast.error("元数据提取失败", { description: e.message });
    } finally {
      setStep2Loading(false);
    }
  }, [metadataText, metadataImage, formatResult]);

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
      toast.success("BibTeX 生成完成");
    } catch (e) {
      toast.error("BibTeX 生成失败", { description: e.message });
    } finally {
      setStep3Loading(false);
    }
  }, [editedMetadata, formatResult]);

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
      () => toast.success("已复制到剪贴板"),
      () => toast.error("复制失败"),
    );
  }, [editedBibtex]);

  // Step 5: generate LaTeX reference code
  const handleGenerateLatex = useCallback(async () => {
    if (!formatResult) {
      toast.error("请先完成格式识别（步骤 1）");
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
      toast.success("LaTeX 代码生成完成");
    } catch (e) {
      toast.error("LaTeX 代码生成失败", { description: e.message });
    } finally {
      setStep5Loading(false);
    }
  }, [formatResult, editedMetadata, editedCiteKey, bibFilePath]);

  const handleCopyLatex = useCallback(() => {
    if (!latexResult?.full_code) return;
    navigator.clipboard.writeText(latexResult.full_code).then(
      () => toast.success("已复制到剪贴板"),
      () => toast.error("复制失败"),
    );
  }, [latexResult]);

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
      const actionText = data.action === "updated" ? "已更新" : "已追加";
      toast.success(`${actionText}到 ${selectedBibFile}`);
    } catch (e) {
      toast.error("保存失败", { description: e.message });
    } finally {
      setSaving(false);
    }
  }, [editedBibtex, editedCiteKey, selectedBibFile]);

  const hasResult = formatResult || metadataResult || bibtexResult || latexResult;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">参考文献管理</h1>
        </div>
      </div>

      {!projectPath ? (
        <div className="max-w-4xl mx-auto">
          <Card>
            <CardHeader>
              <CardTitle>未打开项目</CardTitle>
              <CardDescription>
                先转到项目工作区，打开一个项目后即可使用参考文献管理
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button className="w-full" nativeButton={false} render={<Link to="/workspace" />}>
                打开工作区
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
                <CardTitle className="text-base">识别参考文献格式</CardTitle>
              </div>
              <CardDescription>
                输入一条参考文献示例，LLM 将自动识别其格式规则和所需字段
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {savedFormats.length > 0 && (
                <div className="space-y-1.5">
                  <Label className="text-xs">加载已保存的格式</Label>
                  <div className="flex items-center gap-2">
                    <Select value={selectedFormatName} onValueChange={handleLoadSavedFormat}>
                      <SelectTrigger className="min-w-48">
                        <SelectValue placeholder="选择已保存的格式...">
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
                placeholder="例如：Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Łukasz and Polosukhin, Illia.Attention Is All You Need[J].arXiv preprint arXiv:1706.03762,2017."
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
                {step1Loading ? "识别中..." : "识别格式"}
              </Button>

              {formatResult && (
                <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                  {isEditingFormat && editedFormatInfo ? (
                    <>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1">
                          <Label className="text-xs">格式名称</Label>
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
                          <Label className="text-xs">BibTeX 类型</Label>
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
                        <Label className="text-xs">必填字段（逗号分隔）</Label>
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
                        <Label className="text-xs">可选字段（逗号分隔）</Label>
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
                          确认修改
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={handleCancelEditFormat}
                        >
                          取消
                        </Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <span className="text-xs text-muted-foreground">
                          识别格式：
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
                          必填字段：
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
                            可选字段：
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
                            推荐 LaTeX 样式：
                          </span>
                          <span className="ml-1 text-xs font-mono font-semibold">
                            {formatResult.latex_bibliographystyle}
                          </span>
                          {formatResult.latex_package && (
                            <>
                              <span className="text-xs text-muted-foreground ml-2">
                                （需宏包
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
                          编辑格式
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
                          保存格式
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
                <CardTitle className="text-base">提取文献元数据</CardTitle>
              </div>
              <CardDescription>
                输入文献元数据文本，或上传包含文献信息的截图
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                className="min-h-20 resize-none"
                value={metadataText}
                onChange={(e) => setMetadataText(e.target.value)}
                placeholder="粘贴 Google Scholar、知网或其他来源的文献信息..."
              />

              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">或</span>
                <label className="cursor-pointer inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
                  <Upload className="size-4" />
                  上传截图
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
                {step2Loading ? "提取中..." : "提取元数据"}
              </Button>

              {editedMetadata && (
                <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">提取结果（可编辑）</span>
                    {missingFields.length > 0 && (
                      <span className="text-xs text-destructive">
                        {missingFields.length} 个必填字段缺失
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {editedMetadata && Object.keys(editedMetadata).map((fieldKey) => {
                      const isMissing = missingFields.includes(fieldKey);
                      const desc = formatResult?.field_descriptions?.[fieldKey];
                      const label = desc || fieldKey;
                      const value = editedMetadata[fieldKey] || "";
                      const isLong = value.length > 80 || fieldKey.includes("abstract") || (desc && (desc.includes("摘要") || desc.includes("说明")));
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
                                isMissing ? "必填字段为空" : ""
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
                <CardTitle className="text-base">生成 BibTeX</CardTitle>
              </div>
              <CardDescription>
                基于提取的元数据生成规范的 BibTeX 代码
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
                {step3Loading ? "生成中..." : "生成 BibTeX"}
              </Button>

              {editedBibtex && (
                <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">BibTeX 代码（可编辑）</span>
                    <div className="flex items-center gap-2">
                      <Button variant="outline" size="sm" onClick={handleCopyBibtex}>
                        <Copy />
                        复制
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">引用键 (cite_key)</Label>
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
                  <CardTitle className="text-base">应用到 .bib 文件</CardTitle>
                </div>
                <CardDescription>
                  选择项目中的 .bib 文件，将生成的条目写入或更新
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {bibFiles.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    当前项目中未找到 .bib 文件。你可以将 BibTeX 代码复制后手动添加。
                  </p>
                ) : (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label className="text-xs">选择 .bib 文件</Label>
                      <Select value={selectedBibFile} onValueChange={setSelectedBibFile}>
                        <SelectTrigger className="min-w-60">
                          <SelectValue placeholder="选择 .bib 文件" />
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
                      {saving ? "保存中..." : "应用到文件"}
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
                  <CardTitle className="text-base">生成 LaTeX 参考文献代码</CardTitle>
                </div>
                <CardDescription>
                  生成包含正确 \bibliographystyle 和 \bibliography 命令的 LaTeX 代码片段
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">.bib 文件路径（不含扩展名）</Label>
                    <Input
                      className="h-8 text-xs font-mono"
                      value={bibFilePath}
                      onChange={(e) => setBibFilePath(e.target.value)}
                      placeholder="references"
                    />
                  </div>
                  {formatResult.latex_bibliographystyle && (
                    <div className="space-y-1.5">
                      <Label className="text-xs">推荐样式</Label>
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
                  {step5Loading ? "生成中..." : "生成 LaTeX 代码"}
                </Button>

                {latexResult && (
                  <div className="rounded-lg border border-border p-4 space-y-3 mt-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">LaTeX 参考文献代码</span>
                      <Button variant="outline" size="sm" onClick={handleCopyLatex}>
                        <Copy />
                        复制
                      </Button>
                    </div>
                    <Textarea
                      className="min-h-32 resize-none font-mono text-xs"
                      value={latexResult.full_code}
                      readOnly
                    />
                    <p className="text-xs text-muted-foreground">
                      将上述代码添加到你的 LaTeX 文档中。导言区配置已包含在内，
                      参考文献列表命令需放在文档末尾。
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
