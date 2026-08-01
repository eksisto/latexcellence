import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ArrowLeft,
  AlertOctagon,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Copy,
  Download,
  Eye,
  FileText,
  FolderTree,
  Loader2,
  ScrollText,
  Send,
  Upload,
  Wand2,
  Wrench,
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
import usePlotGen from "@/hooks/usePlotGen";

const STEP_ICON = {
  plan: Wand2,
  tool_call: Wrench,
  tool_result: CheckCircle2,
  execution: BarChart3,
  tikz: FileText,
  error: AlertTriangle,
  warning: AlertOctagon,
  done: CheckCircle2,
};

export default function PlotGenPage() {
  const { t } = useTranslation(["pages", "common"]);
  const [description, setDescription] = useState("");
  const [style, setStyle] = useState("nature");
  const [styles, setStyles] = useState([]);
  const [file, setFile] = useState(null);
  const [refineMessage, setRefineMessage] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [traceOpen, setTraceOpen] = useState(false);
  const scrollRef = useRef(null);

  const plot = usePlotGen();

  useEffect(() => {
    api.plotGen
      .styles()
      .then((data) => {
        if (data.styles?.length) {
          setStyles(data.styles);
          setStyle(data.styles[0].key);
        }
      })
      .catch(() => {});
  }, []);

  useLayoutEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [plot.steps]);

  const handleRun = useCallback(async () => {
    if (!file && !plot.sessionId) return;
    if (!description.trim()) return;
    try {
      if (file) {
        // 选择了新数据文件 → 创建新 session 并用返回的 session_id 立即生成
        const result = await plot.upload(file, description.trim(), style);
        await plot.generate(description.trim(), style, result.session_id);
      } else {
        // 复用已有 session 微调/重新生成
        await plot.generate(description.trim(), style);
      }
    } catch (e) {
      console.error(e);
    }
  }, [file, description, style, plot.sessionId, plot.upload, plot.generate]);

  const handleRefine = useCallback(async () => {
    if (!refineMessage.trim() || !plot.sessionId) return;
    await plot.refine(refineMessage);
    setRefineMessage("");
  }, [refineMessage, plot.sessionId, plot.refine]);

  const { lastExecution, lastTikz } = useMemo(() => {
    let exec = null;
    let tikz = null;
    for (let i = plot.steps.length - 1; i >= 0; i--) {
      const s = plot.steps[i];
      if (s.type === "execution" && !exec) exec = s;
      if (s.type === "tikz" && !tikz) tikz = s;
      if (exec && tikz) break;
    }
    return { lastExecution: exec, lastTikz: tikz };
  }, [plot.steps]);

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
              {t("pages:plotGen.title")}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              {t("pages:plotGen.subtitle")}
            </p>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {t("pages:plotGen.inputTitle")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("pages:plotGen.dataLabel")}
                </Label>
                <Input
                  type="file"
                  accept=".csv,.tsv,.xlsx,.xls,.json"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] || null);
                    e.target.value = "";
                  }}
                />
                {file && (
                  <p className="text-xs text-muted-foreground truncate">
                    {file.name}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {t("pages:plotGen.descLabel")}
                </Label>
                <Textarea
                  className="min-h-24 resize-none"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t("pages:plotGen.descPlaceholder")}
                />
              </div>
              <div className="flex items-center gap-2">
                <div className="w-44">
                  <Select value={style} onValueChange={setStyle}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {styles.map((s) => (
                        <SelectItem key={s.key} value={s.key}>
                          {s.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  onClick={handleRun}
                  disabled={
                    plot.running ||
                    !description.trim() ||
                    (!file && !plot.sessionId)
                  }
                >
                  {plot.running ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Upload />
                  )}
                  {plot.running
                    ? t("pages:plotGen.running")
                    : t("pages:plotGen.generateBtn")}
                </Button>
                {plot.sessionId && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={plot.reset}
                    disabled={plot.running}
                  >
                    {t("pages:plotGen.reset")}
                  </Button>
                )}
              </div>
              {plot.error && (
                <p className="text-sm text-destructive flex items-center gap-1">
                  <AlertTriangle className="size-4" />
                  {plot.error}
                </p>
              )}
            </CardContent>
          </Card>

          {plot.warning && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm flex items-start gap-2 text-amber-700 dark:text-amber-400">
              <AlertOctagon className="size-4 mt-0.5 shrink-0" />
              <span className="whitespace-pre-wrap">{plot.warning}</span>
            </div>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Wrench className="size-4" />
                {t("pages:plotGen.timeline")}
                {plot.steps.length > 0 && (
                  <Badge variant="secondary">{plot.steps.length}</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div
                ref={scrollRef}
                className="max-h-[420px] overflow-y-auto space-y-2 pr-1"
              >
                {plot.steps.length === 0 && !plot.running && (
                  <p className="text-xs text-muted-foreground py-8 text-center">
                    {t("pages:plotGen.timelineEmpty")}
                  </p>
                )}
                {plot.steps.map((s) => (
                  <StepItem key={s._key} step={s} />
                ))}
              </div>
            </CardContent>
          </Card>

          {plot.sessionId && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {t("pages:plotGen.refineTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Textarea
                  rows={3}
                  value={refineMessage}
                  onChange={(e) => setRefineMessage(e.target.value)}
                  placeholder={t("pages:plotGen.refinePlaceholder")}
                />
                <Button
                  onClick={handleRefine}
                  disabled={plot.running || !refineMessage.trim()}
                >
                  <Send />
                  {t("pages:plotGen.refineBtn")}
                </Button>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <StatusCard
            title={t("pages:plotGen.dataProfile")}
            profile={plot.dataProfile}
          />
          <ChartPlanCard plan={plot.chartPlan} t={t} />

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <BarChart3 className="size-4" />
                {t("pages:plotGen.artifacts")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {plot.artifacts.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("pages:plotGen.noArtifacts")}
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap gap-1.5">
                    {plot.artifacts
                      .filter((a) =>
                        [".png", ".svg", ".pdf"].some((s) =>
                          a.path.toLowerCase().endsWith(s),
                        ),
                      )
                      .map((a) => (
                        <Button
                          key={a.path}
                          variant={
                            previewPath === a.path ? "default" : "outline"
                          }
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => setPreviewPath(a.path)}
                        >
                          {a.path.split("/").pop()}
                        </Button>
                      ))}
                  </div>
                  {previewPath && (
                    <div className="rounded border bg-muted/40 p-2 min-h-40">
                      {previewPath.toLowerCase().endsWith(".svg") ||
                      previewPath.toLowerCase().endsWith(".png") ? (
                        <img
                          src={plot.previewUrl(previewPath)}
                          alt={previewPath}
                          loading="lazy"
                          className="max-h-72 w-auto mx-auto"
                        />
                      ) : (
                        <iframe
                          title={previewPath}
                          src={plot.previewUrl(previewPath)}
                          className="w-full h-72 border-0 rounded bg-white"
                        />
                      )}
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FolderTree className="size-4" />
                {t("pages:plotGen.files")}
                <div className="ml-auto flex items-center gap-2">
                  {plot.files.length > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-1.5 text-xs gap-1"
                      onClick={plot.download}
                    >
                      <Download className="size-3" />
                      {t("pages:plotGen.downloadZip")}
                    </Button>
                  )}
                  {plot.files.length > 0 && (
                    <Badge variant="secondary">{plot.files.length}</Badge>
                  )}
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {plot.files.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("pages:plotGen.noFiles")}
                </p>
              ) : (
                <ul className="space-y-0.5 text-sm font-mono max-h-52 overflow-y-auto">
                  {plot.files.map((f) => (
                    <li key={f.path}>
                      <button
                        type="button"
                        onClick={() => plot.loadFile(f.path)}
                        className={`w-full text-left flex items-center gap-1.5 px-1.5 py-1 rounded hover:bg-muted ${plot.selectedFile === f.path ? "bg-muted" : ""}`}
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

          {plot.sessionId && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <ScrollText className="size-4" />
                  {t("pages:plotGen.traceTitle")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  {t("pages:plotGen.traceDesc")}
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1"
                    disabled={plot.running}
                    onClick={async () => {
                      await plot.loadTrace();
                      setTraceOpen(true);
                    }}
                  >
                    <Eye className="size-3" />
                    {t("pages:plotGen.traceView")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1"
                    disabled={plot.running}
                    onClick={plot.downloadTrace}
                  >
                    <Download className="size-3" />
                    {t("pages:plotGen.traceDownload")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {lastExecution && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {t("pages:plotGen.executionStatus")}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm space-y-2">
                <StatusRow ok={lastExecution.success} t={t} />
                {lastExecution.traceback && (
                  <pre className="p-2 bg-muted rounded text-xs font-mono max-h-40 overflow-auto whitespace-pre-wrap">
                    {lastExecution.traceback}
                  </pre>
                )}
              </CardContent>
            </Card>
          )}

          {lastTikz && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {t("pages:plotGen.tikzStatus")}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                <StatusRow ok={lastTikz.success} t={t} />
                {lastTikz.log && (
                  <details>
                    <summary className="cursor-pointer text-xs text-muted-foreground mt-2">
                      {t("pages:plotGen.viewLog")}
                    </summary>
                    <pre className="mt-2 p-2 bg-muted rounded text-xs font-mono max-h-40 overflow-auto whitespace-pre-wrap">
                      {lastTikz.log}
                    </pre>
                  </details>
                )}
              </CardContent>
            </Card>
          )}

          {plot.summary && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {t("pages:plotGen.summary")}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm whitespace-pre-wrap">{plot.summary}</p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {plot.selectedFile && (
        <div className="max-w-6xl mx-auto mt-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-mono">
                  {plot.selectedFile}
                </CardTitle>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      navigator.clipboard
                        .writeText(plot.fileContent)
                        .catch(() => {})
                    }
                  >
                    <Copy className="size-4" />
                    {t("common:copy")}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={plot.clearFile}>
                    {t("common:cancel")}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {plot.loadingFile ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
                  <Loader2 className="animate-spin size-4" />
                  {t("common:loading")}
                </div>
              ) : (
                <pre className="p-3 bg-muted rounded text-xs font-mono max-h-[500px] overflow-auto whitespace-pre-wrap">
                  {plot.fileContent}
                </pre>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <Dialog open={traceOpen} onOpenChange={setTraceOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle>{t("pages:plotGen.traceDialogTitle")}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-y-auto pr-1">
            {plot.loadingTrace ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
                <Loader2 className="animate-spin size-4" />
                {t("common:loading")}
              </div>
            ) : !plot.trace || plot.trace.events?.length === 0 ? (
              <p className="text-xs text-muted-foreground py-8 text-center">
                {t("pages:plotGen.traceEmpty")}
              </p>
            ) : (
              <TraceViewer trace={plot.trace} t={t} />
            )}
          </div>
          <DialogFooter showCloseButton />
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatusCard({ title, profile }) {
  const { t } = useTranslation(["pages", "common"]);
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {!profile ? (
          <p className="text-xs text-muted-foreground">
            {t("pages:plotGen.noProfile")}
          </p>
        ) : (
          <div className="space-y-2">
            <div className="text-xs text-muted-foreground">
              {t("pages:plotGen.shape", {
                rows: profile.rows,
                columns: profile.columns,
              })}
            </div>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {profile.column_profiles.map((col) => (
                <div
                  key={col.name}
                  className="flex items-center justify-between text-xs rounded border bg-muted/40 px-2 py-1"
                >
                  <span className="font-mono truncate">{col.name}</span>
                  <Badge variant="outline">{col.inferred_type}</Badge>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ChartPlanCard({ plan, t }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">
          {t("pages:plotGen.chartPlan")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!plan ? (
          <p className="text-xs text-muted-foreground">
            {t("pages:plotGen.noPlan")}
          </p>
        ) : (
          <div className="space-y-2 text-xs">
            <div className="flex items-center gap-2">
              <Badge>{plan.chart_type}</Badge>
              {plan.x && (
                <span className="text-muted-foreground">
                  x: <span className="font-mono">{plan.x}</span>
                </span>
              )}
              {plan.y && (
                <span className="text-muted-foreground">
                  y: <span className="font-mono">{plan.y}</span>
                </span>
              )}
            </div>
            {plan.rationale && <p>{plan.rationale}</p>}
            {plan.risks?.length > 0 && (
              <ul className="list-disc pl-4 space-y-0.5">
                {plan.risks.map((risk) => (
                  <li key={risk} className="text-muted-foreground">
                    {risk}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function StatusRow({ ok, t }) {
  return (
    <div
      className={`flex items-center gap-1.5 font-medium ${ok ? "text-emerald-600" : "text-destructive"}`}
    >
      {ok ? (
        <CheckCircle2 className="size-4" />
      ) : (
        <XCircle className="size-4" />
      )}
      {ok ? t("pages:plotGen.success") : t("pages:plotGen.failed")}
    </div>
  );
}

const StepItem = memo(function StepItem({ step }) {
  const { t } = useTranslation(["pages", "common"]);
  const Icon = STEP_ICON[step.type] || Wand2;
  const accent =
    step.type === "execution" || step.type === "tikz"
      ? step.success
        ? "text-emerald-600"
        : "text-destructive"
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
      {step.type === "plan" && (
        <div className="px-2.5 py-1.5 whitespace-pre-wrap text-muted-foreground">
          {step.content}
        </div>
      )}
      {step.type === "execution" && step.traceback && (
        <pre className="px-2.5 py-1.5 font-mono text-[10px] max-h-40 overflow-auto whitespace-pre-wrap">
          {step.traceback}
        </pre>
      )}
      {step.type === "tikz" && step.log && (
        <pre className="px-2.5 py-1.5 font-mono text-[10px] max-h-40 overflow-auto whitespace-pre-wrap">
          {step.log}
        </pre>
      )}
      {step.type === "tool_result" && (
        <div className="px-2.5 py-1.5 text-muted-foreground">
          {step.ok ? t("pages:plotGen.success") : t("pages:plotGen.failed")}{" "}
          {step.summary}
        </div>
      )}
    </div>
  );
});

function stepLabel(step, t) {
  const key = `pages:plotGen.step_${step.type}`;
  const fallback =
    {
      plan: "Plan",
      tool_call: "Tool Call",
      tool_result: "Result",
      execution: "Execution",
      tikz: "TikZ",
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
        {trace.meta?.last_execution_success != null && (
          <Badge
            variant={
              trace.meta.last_execution_success ? "default" : "destructive"
            }
          >
            {t("pages:plotGen.traceLastExecution")}:{" "}
            {trace.meta.last_execution_success
              ? t("pages:plotGen.success")
              : t("pages:plotGen.failed")}
          </Badge>
        )}
        {trace.meta?.last_tikz_success != null && (
          <Badge
            variant={trace.meta.last_tikz_success ? "default" : "destructive"}
          >
            {t("pages:plotGen.traceLastTikz")}:{" "}
            {trace.meta.last_tikz_success
              ? t("pages:plotGen.success")
              : t("pages:plotGen.failed")}
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
              {ev.type === "run_start" && (
                <span className="text-muted-foreground truncate max-w-[40%]">
                  {ev.data?.run_type}
                </span>
              )}
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
