import { useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  ArrowRightLeft,
  FileText,
  Loader2,
  Copy,
  Download,
  Upload,
} from "lucide-react";
import { api } from "@/api/index";
import PageHeader from "@/components/PageHeader";
import CodeBlock, { latexEditorClass } from "@/components/CodeBlock";
import { cn } from "@/lib/utils";

const FORMATS = [
  { value: "markdown", label: "Markdown" },
  { value: "html", label: "HTML" },
  { value: "docx", label: "DOCX" },
  { value: "latex", label: "LaTeX" },
];

const TEXT_FORMATS = ["markdown", "html", "latex"];

const ACCEPT_MAP = {
  markdown: ".md,.markdown",
  html: ".html,.htm",
  docx: ".docx",
  latex: ".tex",
};

function getOutputMimeType(targetFormat) {
  const map = {
    markdown: "text/markdown",
    html: "text/html",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    latex: "application/x-latex",
  };
  return map[targetFormat] || "text/plain";
}

export default function FormatConvertPage() {
  const { t } = useTranslation(["pages", "common"]);
  const [sourceFormat, setSourceFormat] = useState("markdown");
  const [targetFormat, setTargetFormat] = useState("latex");
  const [inputMode, setInputMode] = useState("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [outputFilename, setOutputFilename] = useState(null);
  const fileInputRef = useRef(null);

  const isTextMode = inputMode === "text";
  const isTextFormat = TEXT_FORMATS.includes(sourceFormat);
  const isBinaryResult = result instanceof Blob;

  const handleSwapFormats = useCallback(() => {
    setSourceFormat(targetFormat);
    setTargetFormat(sourceFormat);
  }, [sourceFormat, targetFormat]);

  const handleSourceFormatChange = useCallback((val) => {
    setSourceFormat(val);
    if (val === "docx") {
      setInputMode("file");
    }
    setFile(null);
    setResult(null);
    setError(null);
  }, []);

  const handleTargetFormatChange = useCallback((val) => {
    setTargetFormat(val);
    setResult(null);
  }, []);

  const handleConvert = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    setOutputFilename(null);
    try {
      if (isTextMode) {
        if (!text.trim()) {
          setError(t("errors:inputEmpty"));
          setLoading(false);
          return;
        }
        const data = await api.formatConverter.convertText(
          text,
          sourceFormat,
          targetFormat,
        );
        setResult(data.result);
      } else {
        if (!file) {
          setLoading(false);
          return;
        }
        const data = await api.formatConverter.convertFile(
          file,
          sourceFormat,
          targetFormat,
        );
        setResult(data.result);
        setOutputFilename(data.output_filename);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [isTextMode, text, file, sourceFormat, targetFormat, t]);

  const handleCopy = useCallback(() => {
    if (!result || isBinaryResult) return;
    navigator.clipboard.writeText(result).catch(() => {});
  }, [result, isBinaryResult]);

  const handleDownload = useCallback(() => {
    if (!result) return;
    const filename =
      outputFilename ||
      `output.${targetFormat === "latex" ? "tex" : targetFormat}`;
    const blob =
      result instanceof Blob
        ? result
        : new Blob([result], {
            type: `${getOutputMimeType(targetFormat)};charset=utf-8`,
          });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [result, outputFilename, targetFormat]);

  const handleFileDrop = useCallback((e) => {
    e.preventDefault();
    const dropped = e.dataTransfer?.files?.[0];
    if (dropped) {
      setFile(dropped);
      setResult(null);
      setError(null);
    }
  }, []);

  const handleFileSelect = useCallback((e) => {
    const selected = e.target.files?.[0];
    if (selected) {
      setFile(selected);
      setResult(null);
      setError(null);
    }
  }, []);

  const canConvert = isTextMode ? !!text.trim() : !!file;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:formatConvert.title")}
          description={t("pages:formatConvert.subtitle")}
          groupKey="contentImport"
        />
      </div>

      <div className="max-w-3xl mx-auto space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-3">
              <div className="flex-1 space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">
                  {t("pages:formatConvert.sourceFormat")}
                </span>
                <Select
                  value={sourceFormat}
                  onValueChange={handleSourceFormatChange}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FORMATS.map((f) => (
                      <SelectItem key={f.value} value={f.value}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="mt-4 size-8"
                onClick={handleSwapFormats}
              >
                <ArrowRightLeft className="size-4" />
              </Button>
              <div className="flex-1 space-y-1.5">
                <span className="text-xs font-medium text-muted-foreground">
                  {t("pages:formatConvert.targetFormat")}
                </span>
                <Select
                  value={targetFormat}
                  onValueChange={handleTargetFormatChange}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FORMATS.map((f) => (
                      <SelectItem key={f.value} value={f.value}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <Tabs
              value={isTextMode ? "text" : "file"}
              onValueChange={(val) => {
                setInputMode(val);
                if (
                  val === "file" &&
                  !isTextFormat &&
                  sourceFormat !== "docx"
                ) {
                  setSourceFormat("markdown");
                }
              }}
            >
              <TabsList>
                <TabsTrigger value="text">
                  {t("pages:formatConvert.textTab")}
                </TabsTrigger>
                <TabsTrigger
                  value="file"
                  disabled={!isTextFormat && sourceFormat !== "docx"}
                >
                  {t("pages:formatConvert.fileTab")}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="text" className="pt-2">
                <Textarea
                  className={cn(latexEditorClass, "min-h-48 resize-none")}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={t("pages:formatConvert.inputPlaceholder")}
                />
              </TabsContent>
              <TabsContent value="file" className="pt-2">
                <div
                  className="flex flex-col items-center justify-center min-h-48 border-2 border-dashed rounded-lg cursor-pointer transition-colors hover:border-primary/50 hover:bg-muted/50"
                  onDrop={handleFileDrop}
                  onDragOver={(e) => e.preventDefault()}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPT_MAP[sourceFormat] || "*"}
                    onChange={handleFileSelect}
                    className="hidden"
                  />
                  {file ? (
                    <div className="flex flex-col items-center gap-2">
                      <FileText className="size-8 text-muted-foreground" />
                      <span className="text-sm font-medium">{file.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {(file.size / 1024).toFixed(1)} KB
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFile(null);
                        }}
                      >
                        {t("common:cancel")}
                      </Button>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2">
                      <Upload className="size-8 text-muted-foreground" />
                      <span className="text-sm text-muted-foreground">
                        {t("pages:formatConvert.fileUploadDesc")}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {t("pages:formatConvert.fileUploadHint")}
                      </span>
                    </div>
                  )}
                </div>
              </TabsContent>
            </Tabs>

            <div className="flex items-center gap-2">
              <Button onClick={handleConvert} disabled={loading || !canConvert}>
                {loading ? <Loader2 className="animate-spin" /> : <FileText />}
                {loading
                  ? t("pages:formatConvert.convertBtnLoading")
                  : t("pages:formatConvert.convertBtn")}
              </Button>
              <span className="text-xs text-muted-foreground">
                {t("pages:formatConvert.needPandoc")}
                <a
                  href="https://pandoc.org/installing.html"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="relative inline-block no-underline after:content-[''] after:absolute after:left-0 after:bottom-0 after:h-[0.5px] after:w-full after:bg-current after:origin-left after:transition-transform after:duration-300 hover:after:scale-x-0"
                >
                  Pandoc
                </a>
              </span>
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
          </CardContent>
        </Card>

        {result && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">
                  {t("pages:formatConvert.resultTitle")}
                </CardTitle>
                <div className="flex items-center gap-1">
                  {!isBinaryResult && (
                    <Button variant="outline" size="sm" onClick={handleCopy}>
                      <Copy />
                      {t("pages:formatConvert.copyBtn")}
                    </Button>
                  )}
                  <Button variant="outline" size="sm" onClick={handleDownload}>
                    <Download />
                    {t("pages:formatConvert.downloadBtn")}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {isBinaryResult ? (
                <div className="flex flex-col items-center justify-center gap-3 py-8 text-muted-foreground">
                  <FileText className="size-10" />
                  <p className="text-sm">
                    {t("pages:formatConvert.binaryResultHint")}
                  </p>
                </div>
              ) : (
                <CodeBlock
                  code={result}
                  language={targetFormat}
                  className="p-4 max-h-96 text-sm"
                />
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
