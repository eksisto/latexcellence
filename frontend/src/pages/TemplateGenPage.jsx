import { useState, useCallback, useRef, useMemo, useEffect } from "react";
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
import { Badge } from "@/components/ui/badge";
import FileText from "lucide-react/dist/esm/icons/file-text.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Copy from "lucide-react/dist/esm/icons/copy.mjs";
import Download from "lucide-react/dist/esm/icons/download.mjs";
import Eye from "lucide-react/dist/esm/icons/eye.mjs";
import FolderTree from "lucide-react/dist/esm/icons/folder-tree.mjs";
import FileCheck2 from "lucide-react/dist/esm/icons/file-check-corner.mjs";
import AlertTriangle from "lucide-react/dist/esm/icons/triangle-alert.mjs";
import CircleX from "lucide-react/dist/esm/icons/circle-x.mjs";
import ScrollText from "lucide-react/dist/esm/icons/scroll-text.mjs";
import Wand2 from "lucide-react/dist/esm/icons/wand-sparkles.mjs";
import Wrench from "lucide-react/dist/esm/icons/wrench.mjs";
import Brain from "lucide-react/dist/esm/icons/brain.mjs";
import CheckCircle2 from "lucide-react/dist/esm/icons/circle-check.mjs";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/api/index";
import PageHeader from "@/components/PageHeader";
import CodeBlock from "@/components/CodeBlock";
import PDFPreview from "@/components/PDFPreview";
import { languageFromFilename } from "@/utils/highlight";
import { translateError } from "@/utils/errorMessages";

const ROLE_ICON = {
  plan: Wand2,
  tool_call: Wrench,
  tool_result: CheckCircle2,
  compile: FileCheck2,
  error: CircleX,
  warning: AlertTriangle,
  done: CheckCircle2,
};

const PDF_FILE_RE = /\.pdf$/i;

export default function TemplateGenPage() {
  const { t } = useTranslation(["pages", "common"]);
  const [description, setDescription] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [steps, setSteps] = useState([]);
  const [warning, setWarning] = useState(null);
  const [files, setFiles] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState("");
  const [loadingFile, setLoadingFile] = useState(false);
  const [pdfUrl, setPdfUrl] = useState(null);
  // PDF 预览失败原因（PDF 分支不渲染 CodeBlock，错误必须独立展示）
  const [pdfError, setPdfError] = useState(null);
  // blob URL 在替换/清空/组件卸载时由 cleanup 统一释放，避免 PDF 预览内存泄漏
  useEffect(() => {
    if (!pdfUrl || !pdfUrl.startsWith("blob:")) return undefined;
    return () => URL.revokeObjectURL(pdfUrl);
  }, [pdfUrl]);
  const [summary, setSummary] = useState("");
  // 思维链流式文本（只读展示，不参与任何后续流程）
  const [reasoningText, setReasoningText] = useState("");
  const [traceOpen, setTraceOpen] = useState(false);
  const [trace, setTrace] = useState(null);
  const [loadingTrace, setLoadingTrace] = useState(false);
  const scrollRef = useRef(null);
  const reasoningScrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps]);

  useEffect(() => {
    if (reasoningScrollRef.current) {
      reasoningScrollRef.current.scrollTop =
        reasoningScrollRef.current.scrollHeight;
    }
  }, [reasoningText]);

  const reset = useCallback(() => {
    setSteps([]);
    setReasoningText("");
    setFiles([]);
    setSessionId(null);
    setSelectedFile(null);
    setFileContent("");
    setPdfUrl(null);
    setPdfError(null);
    setSummary("");
    setTrace(null);
    setError(null);
    setWarning(null);
  }, []);

  const handleGenerate = useCallback(async () => {
    if (!description.trim()) return;
    reset();
    setRunning(true);
    try {
      await api.templateGen.generate({
        description: description.trim(),
        onEvent: (ev) => {
          if (ev.type === "error") {
            setError(translateError(ev.message || ""));
            return;
          }
          if (ev.type === "reasoning") {
            // 增量思维链：追加到思考面板，不进入步骤流
            setReasoningText((prev) => prev + (ev.text || ""));
            return;
          }
          if (ev.type === "warning") {
            setWarning(translateError(ev.message || ""));
            return;
          }
          if (ev.type === "done") {
            setSessionId(ev.session_id);
            setFiles(ev.files || []);
            setSummary(ev.summary || "");
            return;
          }
          const step =
            ev.ok === false && ev.summary
              ? { ...ev, summary: translateError(ev.summary) }
              : ev;
          setSteps((prev) => [...prev, step]);
        },
        onError: () => {},
      });
    } catch (e) {
      setError(translateError(e.message));
    } finally {
      setRunning(false);
    }
  }, [description, reset]);

  const handleLoadFile = useCallback(
    async (path) => {
      if (!sessionId) return;
      setSelectedFile(path);
      setLoadingFile(true);
      // PDF 为二进制产物，走独立 preview 端点获取 blob URL 渲染
      if (PDF_FILE_RE.test(path)) {
        setFileContent("");
        try {
          const url = await api.templateGen.preview(sessionId, path);
          setPdfUrl(url);
          setPdfError(null);
        } catch (e) {
          setPdfUrl(null);
          // fetchBlobUrl 内部已翻译错误，直接展示
          setPdfError(e.message);
        } finally {
          setLoadingFile(false);
        }
        return;
      }
      setPdfUrl(null);
      setPdfError(null);
      try {
        const data = await api.templateGen.file(sessionId, path);
        setFileContent(data.content || "");
      } catch (e) {
        setFileContent(`// ${e.message}`);
      } finally {
        setLoadingFile(false);
      }
    },
    [sessionId],
  );

  const handleCopy = useCallback((text) => {
    if (!text) return;
    navigator.clipboard.writeText(text).catch(() => {});
  }, []);

  const handleLoadTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoadingTrace(true);
    try {
      const data = await api.templateGen.trace(sessionId);
      setTrace(data);
      setTraceOpen(true);
    } catch (e) {
      setTrace(null);
      setError(e.message);
    } finally {
      setLoadingTrace(false);
    }
  }, [sessionId]);

  const handleDownloadTrace = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.templateGen.downloadTrace(sessionId);
    } catch (e) {
      setError(e.message);
    }
  }, [sessionId]);

  const handleDownloadZip = useCallback(async () => {
    if (!sessionId) return;
    try {
      await api.templateGen.download(sessionId);
    } catch (e) {
      setError(e.message);
    }
  }, [sessionId]);

  const compileResults = useMemo(
    () => steps.filter((s) => s.type === "compile"),
    [steps],
  );
  const lastCompile = compileResults[compileResults.length - 1];

  return (
    <div className="min-h-screen p-6 md:p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto mb-6">
        <PageHeader
          title={t("pages:templateGen.title")}
          description={t("pages:templateGen.agentHint")}
          groupKey="contentGeneration"
        />
      </div>

      <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 左：输入 + 步骤流 */}
        <div className="lg:col-span-2 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {t("pages:templateGen.descLabel")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                className="min-h-24 resize-none"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("pages:templateGen.descDetailPlaceholder")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    handleGenerate();
                  }
                }}
              />
              <div className="flex items-center gap-2">
                <Button
                  onClick={handleGenerate}
                  disabled={running || !description.trim()}
                >
                  {running ? <Loader2 className="animate-spin" /> : <Wand2 />}
                  {running
                    ? t("pages:templateGen.agentRunning")
                    : t("pages:templateGen.generateBtn")}
                </Button>
                <span className="text-xs text-muted-foreground">
                  {t("pages:templateGen.submitHint")}
                </span>
              </div>
              {error && (
                <p className="text-sm text-destructive flex items-center gap-1">
                  <AlertTriangle className="size-4" />
                  {error}
                </p>
              )}
            </CardContent>
          </Card>

          {warning && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm flex items-start gap-2 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="size-4 mt-0.5 shrink-0" />
              <span className="whitespace-pre-wrap">{warning}</span>
            </div>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Wrench className="size-4" />
                {t("pages:templateGen.timeline")}
                {steps.length > 0 && (
                  <Badge variant="secondary">{steps.length}</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {(running || reasoningText) && (
                <details className="mb-2 rounded border bg-muted/40" open>
                  <summary className="cursor-pointer flex items-center gap-2 px-2.5 py-1.5 text-xs text-muted-foreground">
                    {running ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Brain className="size-3.5" />
                    )}
                    {running
                      ? t("pages:templateGen.thinking")
                      : t("pages:templateGen.thoughtProcess")}
                    {reasoningText && (
                      <span className="ml-auto font-mono text-[10px]">
                        {reasoningText.length}
                      </span>
                    )}
                  </summary>
                  {reasoningText && (
                    <div
                      ref={reasoningScrollRef}
                      className="max-h-40 overflow-y-auto px-2.5 pb-2 font-mono text-[10px] whitespace-pre-wrap text-muted-foreground"
                    >
                      {reasoningText}
                    </div>
                  )}
                </details>
              )}
              <div
                ref={scrollRef}
                className="max-h-[420px] overflow-y-auto space-y-2 pr-1"
              >
                {steps.length === 0 && !running && (
                  <p className="text-xs text-muted-foreground py-8 text-center">
                    {t("pages:templateGen.timelineEmpty")}
                  </p>
                )}
                {steps.map((s, i) => (
                  <StepItem key={i} step={s} t={t} />
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* 右：编译状态 + 文件树 */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileCheck2 className="size-4" />
                {t("pages:templateGen.compileStatus")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {compileResults.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("pages:templateGen.noCompileYet")}
                </p>
              ) : (
                <>
                  {lastCompile && (
                    <div
                      className={`text-sm font-medium flex items-center gap-1.5 ${lastCompile.success ? "text-emerald-600" : "text-destructive"}`}
                    >
                      {lastCompile.success ? (
                        <CheckCircle2 className="size-4" />
                      ) : (
                        <CircleX className="size-4" />
                      )}
                      {lastCompile.success
                        ? t("pages:templateGen.compileSuccess")
                        : t("pages:templateGen.compileFailed")}
                      <Badge variant="secondary" className="ml-auto">
                        {compileResults.length}
                      </Badge>
                    </div>
                  )}
                  {lastCompile?.errors && lastCompile.errors.length > 0 && (
                    <div className="space-y-1">
                      <h4 className="text-xs font-medium text-muted-foreground">
                        {t("pages:templateGen.detectedErrors")}
                      </h4>
                      <ul className="space-y-1.5 text-xs">
                        {lastCompile.errors.map((err, i) => (
                          <li
                            key={i}
                            className="rounded border bg-muted/50 px-2 py-1.5 font-mono"
                          >
                            <div className="flex items-center gap-1.5 font-medium text-destructive">
                              <span className="size-1.5 rounded-full bg-destructive" />
                              {err.type || "Error"}
                              {err.file && (
                                <span className="text-muted-foreground">
                                  {" · "}
                                  {err.file}
                                  {err.line != null ? `:${err.line}` : ""}
                                </span>
                              )}
                            </div>
                            {err.detail && (
                              <CodeBlock
                                code={err.detail}
                                language={languageFromFilename(err.file)}
                                variant="bare"
                                className="mt-1 text-muted-foreground"
                              />
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <details>
                    <summary className="cursor-pointer text-xs text-muted-foreground">
                      {t("pages:templateGen.viewCompileLog")}
                    </summary>
                    <CodeBlock
                      code={lastCompile?.log || ""}
                      language="plaintext"
                      className="mt-2 p-3 max-h-60"
                    />
                  </details>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FolderTree className="size-4" />
                {t("pages:templateGen.fileTree")}
                <div className="ml-auto flex items-center gap-2">
                  {files.length > 0 && sessionId && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-1.5 text-xs gap-1"
                      onClick={handleDownloadZip}
                    >
                      <Download className="size-3" />
                      {t("pages:templateGen.downloadZip")}
                    </Button>
                  )}
                  {files.length > 0 && (
                    <Badge variant="secondary">{files.length}</Badge>
                  )}
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {files.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("pages:templateGen.noFilesYet")}
                </p>
              ) : (
                <ul className="space-y-0.5 text-sm font-mono">
                  {files.map((f) => (
                    <li key={f.path}>
                      <button
                        type="button"
                        onClick={() => handleLoadFile(f.path)}
                        className={`w-full text-left flex items-center gap-1.5 px-1.5 py-1 rounded hover:bg-muted ${selectedFile === f.path ? "bg-muted" : ""}`}
                      >
                        <FileText className="size-3.5 text-muted-foreground shrink-0" />
                        <span className="truncate">{f.path}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {sessionId && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <ScrollText className="size-4" />
                  {t("pages:templateGen.traceTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  {t("pages:templateGen.traceDesc")}
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1"
                    disabled={running}
                    onClick={handleLoadTrace}
                  >
                    <Eye className="size-3" />
                    {t("pages:templateGen.traceView")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1"
                    disabled={running}
                    onClick={handleDownloadTrace}
                  >
                    <Download className="size-3" />
                    {t("pages:templateGen.traceDownload")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {summary && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {t("pages:templateGen.summary")}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm whitespace-pre-wrap">{summary}</p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* 文件预览面板 */}
      {selectedFile && (
        <div className="max-w-6xl mx-auto mt-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-mono">
                  {selectedFile}
                </CardTitle>
                <div className="flex gap-2">
                  {!PDF_FILE_RE.test(selectedFile) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleCopy(fileContent)}
                    >
                      <Copy className="size-4" />
                      {t("common:copy")}
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedFile(null)}
                  >
                    {t("common:cancel")}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {loadingFile ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
                  <Loader2 className="animate-spin size-4" />
                  {t("common:loading")}
                </div>
              ) : PDF_FILE_RE.test(selectedFile) ? (
                pdfError ? (
                  <p className="text-sm text-destructive flex items-center gap-1 py-6">
                    <AlertTriangle className="size-4" />
                    {pdfError}
                  </p>
                ) : (
                  <div className="h-[600px]">
                    <PDFPreview url={pdfUrl} />
                  </div>
                )
              ) : (
                <CodeBlock
                  code={fileContent}
                  language={languageFromFilename(selectedFile)}
                  className="p-3 max-h-[500px]"
                />
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Agent 运行日志弹窗 */}
      <Dialog open={traceOpen} onOpenChange={setTraceOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle>{t("pages:templateGen.traceDialogTitle")}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-y-auto pr-1">
            {loadingTrace ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
                <Loader2 className="animate-spin size-4" />
                {t("common:loading")}
              </div>
            ) : !trace || trace.events?.length === 0 ? (
              <p className="text-xs text-muted-foreground py-8 text-center">
                {t("pages:templateGen.traceEmpty")}
              </p>
            ) : (
              <TraceViewer trace={trace} t={t} />
            )}
          </div>
          <DialogFooter showCloseButton />
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StepItem({ step, t }) {
  const Icon = ROLE_ICON[step.type] || Wand2;
  const accent =
    step.type === "compile"
      ? step.success
        ? "text-emerald-600"
        : "text-destructive"
      : step.type === "error"
        ? "text-destructive"
        : step.type === "tool_result"
          ? step.ok
            ? "text-emerald-600"
            : "text-destructive"
          : step.type === "done"
            ? "text-emerald-600"
            : "text-primary";

  return (
    <div className="rounded border bg-card text-xs">
      <div className="flex items-center gap-2 px-2.5 py-1.5 border-b">
        <Icon className={`size-3.5 ${accent}`} />
        <span className="font-medium">{stepLabel(step, t)}</span>
        {step.tool && (
          <Badge variant="outline" className="font-mono text-[10px] py-0">
            {step.tool}
          </Badge>
        )}
        {step.summary && (
          <span className="ml-auto text-muted-foreground truncate max-w-[60%]">
            {step.summary}
          </span>
        )}
      </div>
      <StepBody step={step} />
    </div>
  );
}

function StepBody({ step }) {
  if (step.type === "plan") {
    return (
      <div className="px-2.5 py-1.5 whitespace-pre-wrap text-muted-foreground">
        {step.content}
      </div>
    );
  }
  if (step.type === "tool_call" && step.tool === "write_file") {
    const a = step.args || {};
    return (
      <div className="px-2.5 py-1.5 space-y-1">
        <div className="font-mono text-[11px]">
          {a.file_path}{" "}
          <span className="text-muted-foreground">
            ({a.content_length} chars)
          </span>
        </div>
        {a.preview && (
          <CodeBlock
            code={`${a.preview}${(a.content_length || 0) > 300 ? "\n…" : ""}`}
            language={languageFromFilename(a.file_path)}
            className="p-1.5 max-h-32 text-[10px]"
          />
        )}
      </div>
    );
  }
  if (step.type === "compile") {
    return (
      <CodeBlock
        code={step.log || ""}
        language="plaintext"
        className="px-2.5 py-1.5 max-h-40 text-[10px]"
      />
    );
  }
  if (step.type === "tool_result") {
    return (
      <div className="px-2.5 py-1.5 text-muted-foreground">
        {step.ok ? "✓" : "✗"} {step.summary}
      </div>
    );
  }
  return null;
}

function stepLabel(step, t) {
  const key = `pages:templateGen.step_${step.type}`;
  const fallback =
    {
      plan: "Plan",
      tool_call: "Tool Call",
      tool_result: "Result",
      compile: "Compile",
      done: "Done",
      error: "Error",
      warning: "Warning",
    }[step.type] || step.type;
  const translated = t(key);
  return translated === key ? fallback : translated;
}

function TraceViewer({ trace, t }) {
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="font-mono">
          {trace.session_id}
        </Badge>
        <Badge variant="secondary">{trace.events.length}</Badge>
        {trace.meta?.language && (
          <Badge variant="secondary">
            {t("pages:templateGen.traceLanguage")}: {trace.meta.language}
          </Badge>
        )}
        {trace.meta?.max_rounds != null && (
          <Badge variant="secondary">
            {t("pages:templateGen.traceMaxRounds")}: {trace.meta.max_rounds}
          </Badge>
        )}
      </div>
      <div className="space-y-1.5">
        {trace.events.map((ev, i) => (
          <details
            key={ev.ts || i}
            className="rounded border bg-card overflow-hidden"
            open={ev.type === "run_start"}
          >
            <summary className="cursor-pointer flex items-center gap-2 px-2.5 py-1.5">
              <Badge variant="outline" className="font-mono text-[10px]">
                {ev.type}
              </Badge>
              <span className="text-muted-foreground font-mono">{ev.ts}</span>
              {ev.type === "tool_result" && (
                <span className="text-muted-foreground truncate max-w-[40%]">
                  {ev.data?.tool}
                </span>
              )}
            </summary>
            <CodeBlock
              code={JSON.stringify(ev.data, null, 2)}
              language="json"
              className="px-2.5 pb-2.5 pt-1 text-[10px] text-muted-foreground"
            />
          </details>
        ))}
      </div>
    </div>
  );
}
