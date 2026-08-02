import { useState, useCallback, useRef, useMemo, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
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
import {
  ArrowLeft,
  FileText,
  Loader2,
  Copy,
  Download,
  Eye,
  FolderTree,
  FileCheck2,
  AlertTriangle,
  AlertOctagon,
  ScrollText,
  Wand2,
  Wrench,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";

const ROLE_ICON = {
  plan: Wand2,
  tool_call: Wrench,
  tool_result: CheckCircle2,
  compile: FileCheck2,
  error: AlertTriangle,
  warning: AlertOctagon,
  done: CheckCircle2,
};

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
  const [summary, setSummary] = useState("");
  const [traceOpen, setTraceOpen] = useState(false);
  const [trace, setTrace] = useState(null);
  const [loadingTrace, setLoadingTrace] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [steps]);

  const reset = useCallback(() => {
    setSteps([]);
    setFiles([]);
    setSessionId(null);
    setSelectedFile(null);
    setFileContent("");
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
          setSteps((prev) => [...prev, ev]);
        },
        onError: () => {},
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, [description, reset]);

  const handleLoadFile = useCallback(
    async (path) => {
      if (!sessionId) return;
      setSelectedFile(path);
      setLoadingFile(true);
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

  const compileResults = useMemo(
    () => steps.filter((s) => s.type === "compile"),
    [steps],
  );
  const lastCompile = compileResults[compileResults.length - 1];

  return (
    <div className="min-h-screen p-6 md:p-8">
      <div className="max-w-6xl mx-auto mb-6">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="icon"
            nativeButton={false}
            render={<Link to="/" />}
          >
            <ArrowLeft />
          </Button>
          <div>
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
              {t("pages:templateGen.title")}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              {t("pages:templateGen.agentHint")}
            </p>
          </div>
        </div>
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
              <AlertOctagon className="size-4 mt-0.5 shrink-0" />
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
                        <XCircle className="size-4" />
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
                              <pre className="mt-1 whitespace-pre-wrap text-muted-foreground">
                                {err.detail}
                              </pre>
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
                    <pre className="mt-2 p-3 bg-muted rounded text-xs font-mono max-h-60 overflow-auto whitespace-pre-wrap">
                      {lastCompile?.log || ""}
                    </pre>
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
                      onClick={() => api.templateGen.download(sessionId)}
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
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleCopy(fileContent)}
                  >
                    <Copy className="size-4" />
                    {t("common:copy")}
                  </Button>
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
              ) : (
                <pre className="p-3 bg-muted rounded text-xs font-mono max-h-[500px] overflow-auto whitespace-pre-wrap">
                  {fileContent}
                </pre>
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
          <pre className="bg-muted rounded p-1.5 font-mono text-[10px] max-h-32 overflow-auto whitespace-pre-wrap">
            {a.preview}
            {(a.content_length || 0) > 300 ? "\n…" : ""}
          </pre>
        )}
      </div>
    );
  }
  if (step.type === "compile") {
    return (
      <pre className="px-2.5 py-1.5 font-mono text-[10px] max-h-40 overflow-auto whitespace-pre-wrap">
        {step.log || ""}
      </pre>
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
            <pre className="px-2.5 pb-2.5 pt-1 font-mono text-[10px] overflow-x-auto whitespace-pre-wrap text-muted-foreground">
              {JSON.stringify(ev.data, null, 2)}
            </pre>
          </details>
        ))}
      </div>
    </div>
  );
}
