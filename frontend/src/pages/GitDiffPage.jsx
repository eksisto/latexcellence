import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Check,
  CircleX,
  Download,
  Expand,
  FileDiff,
  GitBranch,
  GitCommit,
  Loader2,
  Minimize,
  RefreshCw,
  Settings2,
  Tag,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import ColorField from "@/components/ColorField";
import EmptyState from "@/components/EmptyState";
import PageHeader from "@/components/PageHeader";
import CodeBlock from "@/components/CodeBlock";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import PDFPreview from "@/components/PDFPreview";
import { api } from "@/api/index";
import { cn } from "@/lib/utils";
import useStore from "@/store/useStore";
import { translateError } from "@/utils/errorMessages";

const WORKTREE_REF = "__WORKTREE__";

function flattenTexFiles(nodes) {
  const result = [];
  for (const node of nodes) {
    if (!node.is_dir && node.name.endsWith(".tex")) {
      result.push(node);
    }
    if (node.children) {
      result.push(...flattenTexFiles(node.children));
    }
  }
  return result;
}

const REVISION_KIND_TO_GROUP = {
  worktree: "worktree",
  commit: "commits",
  branch: "branches",
  tag: "tags",
};

function groupRevisions(revisions) {
  const groups = { worktree: [], commits: [], branches: [], tags: [] };
  for (const r of revisions) {
    const key = REVISION_KIND_TO_GROUP[r.kind];
    if (key) groups[key].push(r);
  }
  return groups;
}

function revisionLabel(revision, t) {
  if (!revision) return "";
  if (revision.kind === "worktree") return t("pages:gitDiff.currentWorkspace");
  return revision.label || revision.value;
}

export default function GitDiffPage() {
  const { t } = useTranslation(["pages", "common"]);
  const projectPath = useStore((s) => s.projectPath);
  const workspaceId = useStore((s) => s.workspaceId);
  const rootPath = useStore((s) => s.rootPath);
  const fileTree = useStore((s) => s.fileTree);
  const mainFile = useStore((s) => s.mainFile);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setRootPath = useStore((s) => s.setRootPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setMainFile = useStore((s) => s.setMainFile);
  const setActiveFile = useStore((s) => s.setActiveFile);

  const [refs, setRefs] = useState([]);
  const [gitRoot, setGitRoot] = useState("");
  const [isDirty, setIsDirty] = useState(false);
  const [selectedMainFile, setSelectedMainFile] = useState(mainFile || "");
  const [baseRef, setBaseRef] = useState("");
  const [targetRef, setTargetRef] = useState(WORKTREE_REF);
  const [loadingRefs, setLoadingRefs] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [pdfUrl, setPdfUrl] = useState("");
  const [pdfFullscreen, setPdfFullscreen] = useState(false);
  const [showRawOutput, setShowRawOutput] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [diffEngine, setDiffEngine] = useState("difflib");
  const [markupType, setMarkupType] = useState("UNDERLINE");
  const [mathMarkup, setMathMarkup] = useState("fine");
  const [colorAdd, setColorAdd] = useState("");
  const [colorDel, setColorDel] = useState("");
  const [excludeSafecmd, setExcludeSafecmd] = useState("");
  const [excludeTextcmd, setExcludeTextcmd] = useState("");
  const [diffOptions, setDiffOptions] = useState({
    markupTypes: [],
    mathMarkups: [],
  });
  // 记录已经初始化过默认 ref 的项目，避免切换 base/target 时反复重拉 refs
  const defaultsInitializedFor = useRef("");

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);
  const grouped = useMemo(() => groupRevisions(refs), [refs]);
  const labelMap = useMemo(() => {
    const map = {};
    for (const r of refs) {
      map[r.value] = revisionLabel(r, t);
    }
    return map;
  }, [refs, t]);

  const formatRefValue = useCallback((val) => labelMap[val] || val, [labelMap]);

  useEffect(() => {
    if (!projectPath || fileTree.length > 0) return;

    let cancelled = false;
    api.project
      .open(projectPath, workspaceId)
      .then((data) => {
        if (cancelled) return;
        setRootPath(data.root_path);
        setProjectPath(projectPath);
        setFileTree(data.file_tree);
        setMainFile(data.main_file);
        setActiveFile(data.main_file);
        setSelectedMainFile(data.main_file || "");
      })
      .catch((err) => setError(err.message));

    return () => {
      cancelled = true;
    };
  }, [fileTree.length, projectPath]);

  useEffect(() => {
    if (!selectedMainFile && mainFile) {
      setSelectedMainFile(mainFile);
    }
  }, [mainFile, selectedMainFile]);

  useEffect(() => {
    if (!pdfFullscreen) return;

    const onKeyDown = (e) => {
      if (e.key === "Escape") setPdfFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pdfFullscreen]);

  const loadRefs = useCallback(async () => {
    if (!projectPath) return;
    setLoadingRefs(true);
    setError("");
    try {
      const data = await api.gitDiff.refs(workspaceId);
      const revisions = data.revisions || [];
      setRefs(revisions);
      setGitRoot(data.git_root || "");
      setIsDirty(Boolean(data.is_dirty));
      // 只在项目首次加载（或切换项目）时设置默认 base/target
      if (defaultsInitializedFor.current !== projectPath) {
        const head = revisions.find((item) => item.kind === "commit");
        setBaseRef(head?.value || "");
        setTargetRef(WORKTREE_REF);
        defaultsInitializedFor.current = projectPath;
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingRefs(false);
    }
  }, [projectPath, workspaceId]);

  useEffect(() => {
    loadRefs();
    api.gitDiff
      .getOptions()
      .then((data) => {
        setDiffOptions({
          markupTypes: data.markup_types || [],
          mathMarkups: data.math_markups || [],
        });
        // 从配置初始化默认引擎 / 标记 / 颜色，设置页的修改才能生效
        if (data.default_engine) setDiffEngine(data.default_engine);
        if (data.default_markup_type) setMarkupType(data.default_markup_type);
        if (data.default_math_markup) setMathMarkup(data.default_math_markup);
        if (data.default_color_add) setColorAdd(data.default_color_add);
        if (data.default_color_del) setColorDel(data.default_color_del);
      })
      .catch(() => {});
  }, [loadRefs]);

  const handleRender = useCallback(async () => {
    if (!selectedMainFile || !baseRef || !targetRef) return;
    setRendering(true);
    setError("");
    setResult(null);
    setPdfUrl("");
    try {
      const data = await api.gitDiff.renderPdf(
        {
          mainFile: selectedMainFile,
          baseRef,
          targetRef,
          diffEngine,
          markupType,
          mathMarkup,
          colorAdd,
          colorDel,
          excludeSafecmd,
          excludeTextcmd,
        },
        workspaceId,
      );
      setResult(data);
      if (data.success && data.pdf_url) {
        setPdfUrl(`${data.pdf_url}?t=${Date.now()}`);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setRendering(false);
    }
  }, [
    baseRef,
    selectedMainFile,
    targetRef,
    diffEngine,
    markupType,
    mathMarkup,
    colorAdd,
    colorDel,
    excludeSafecmd,
    excludeTextcmd,
  ]);

  if (!projectPath) {
    return (
      <div className="min-h-screen p-8 animate-tab-enter">
        <div className="max-w-3xl mx-auto">
          <PageHeader
            title={t("pages:gitDiff.title")}
            groupKey="versionControl"
          />
          <EmptyState
            icon={GitBranch}
            title={t("pages:gitDiff.noProjectTitle")}
            description={t("pages:gitDiff.noProjectDesc")}
          >
            <Button nativeButton={false} render={<Link to="/workspace" />}>
              {t("pages:gitDiff.openProjectWorkspace")}
            </Button>
          </EmptyState>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:gitDiff.title")}
          description={t("pages:gitDiff.description")}
          groupKey="versionControl"
        />
      </div>

      <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <FileDiff className="size-4" />
                {t("pages:gitDiff.diffSettings")}
              </CardTitle>
              <CardDescription>
                {t("pages:gitDiff.multiFileDescription")}
              </CardDescription>
              <CardAction>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={loadRefs}
                  disabled={loadingRefs}
                >
                  <RefreshCw className={loadingRefs ? "animate-spin" : ""} />
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>{t("pages:gitDiff.mainFile")}</Label>
                <Select
                  value={selectedMainFile}
                  onValueChange={setSelectedMainFile}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue
                      className="min-w-0 truncate"
                      placeholder={t("pages:gitDiff.selectMainFile")}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {texFiles.map((file) => (
                      <SelectItem key={file.path} value={file.path}>
                        {file.path}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>{t("pages:gitDiff.baseState")}</Label>
                <Select value={baseRef} onValueChange={setBaseRef}>
                  <SelectTrigger className="w-full">
                    <SelectValue
                      className="min-w-0 truncate"
                      placeholder={t("pages:gitDiff.selectBaseState")}
                    >
                      {formatRefValue}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>{t("pages:gitDiff.workspace")}</SelectLabel>
                      {grouped.worktree.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                    {grouped.commits.length > 0 && (
                      <SelectGroup>
                        <SelectLabel className="flex items-center gap-1">
                          <GitCommit className="size-3" />
                          {t("pages:gitDiff.commits", {
                            count: grouped.commits.length,
                          })}
                        </SelectLabel>
                        {grouped.commits.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {grouped.branches.length > 0 && (
                      <SelectGroup>
                        <SelectLabel className="flex items-center gap-1">
                          <GitBranch className="size-3" />
                          {t("pages:gitDiff.branches")}
                        </SelectLabel>
                        {grouped.branches.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {grouped.tags.length > 0 && (
                      <SelectGroup>
                        <SelectLabel className="flex items-center gap-1">
                          <Tag className="size-3" />
                          {t("pages:gitDiff.tags")}
                        </SelectLabel>
                        {grouped.tags.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>{t("pages:gitDiff.targetState")}</Label>
                <Select value={targetRef} onValueChange={setTargetRef}>
                  <SelectTrigger className="w-full">
                    <SelectValue
                      className="min-w-0 truncate"
                      placeholder={t("pages:gitDiff.selectTargetState")}
                    >
                      {formatRefValue}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>{t("pages:gitDiff.workspace")}</SelectLabel>
                      {grouped.worktree.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                    {grouped.commits.length > 0 && (
                      <SelectGroup>
                        <SelectLabel className="flex items-center gap-1">
                          <GitCommit className="size-3" />
                          {t("pages:gitDiff.commits", {
                            count: grouped.commits.length,
                          })}
                        </SelectLabel>
                        {grouped.commits.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {grouped.branches.length > 0 && (
                      <SelectGroup>
                        <SelectLabel className="flex items-center gap-1">
                          <GitBranch className="size-3" />
                          {t("pages:gitDiff.branches")}
                        </SelectLabel>
                        {grouped.branches.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {grouped.tags.length > 0 && (
                      <SelectGroup>
                        <SelectLabel className="flex items-center gap-1">
                          <Tag className="size-3" />
                          {t("pages:gitDiff.tags")}
                        </SelectLabel>
                        {grouped.tags.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                  </SelectContent>
                </Select>
              </div>

              <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground space-y-1">
                <div>
                  {t("pages:gitDiff.gitRoot")}
                  {gitRoot || t("pages:gitDiff.notLoaded")}
                </div>
                <div>
                  {t("pages:gitDiff.workspaceStatus")}
                  {isDirty
                    ? t("pages:gitDiff.hasUncommittedChanges")
                    : t("pages:gitDiff.cleanOrNoChanges")}
                </div>
                <div>
                  {t("pages:gitDiff.projectDirectory")}
                  {rootPath}
                </div>
              </div>

              <div className="rounded-lg border">
                <button
                  type="button"
                  className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                  onClick={() => setShowAdvanced((v) => !v)}
                >
                  <span className="flex items-center gap-2">
                    <Settings2 className="size-4" />
                    {t("pages:gitDiff.advancedOptions")}
                  </span>
                  <svg
                    className={`size-4 shrink-0 transition-transform duration-300 ${showAdvanced ? "rotate-90" : ""}`}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M9 18l6-6-6-6" />
                  </svg>
                </button>
                <div
                  className={cn(
                    "grid transition-all duration-300 ease-in-out",
                    showAdvanced ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
                  )}
                >
                  <div className="overflow-hidden">
                    <div className="border-t px-3 py-3 space-y-3">
                      <div className="space-y-2">
                        <Label>{t("pages:gitDiff.diffEngine")}</Label>
                        <Select
                          value={diffEngine}
                          onValueChange={setDiffEngine}
                        >
                          <SelectTrigger className="w-full">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="difflib">
                              {t("pages:gitDiff.diffEngineDifflib")}
                            </SelectItem>
                            <SelectItem value="latexdiff">
                              {t("pages:gitDiff.diffEngineLatexdiff")}
                            </SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      {diffEngine === "latexdiff" && (
                        <>
                          <div className="space-y-2">
                            <Label>{t("pages:gitDiff.markupType")}</Label>
                            <Select
                              value={markupType}
                              onValueChange={setMarkupType}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {diffOptions.markupTypes.map((type) => (
                                  <SelectItem key={type} value={type}>
                                    {type}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          <div className="space-y-2">
                            <Label>{t("pages:gitDiff.mathMarkup")}</Label>
                            <Select
                              value={mathMarkup}
                              onValueChange={setMathMarkup}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {diffOptions.mathMarkups.map((opt) => (
                                  <SelectItem key={opt} value={opt}>
                                    {t(
                                      `pages:gitDiff.mathMarkup${opt.charAt(0).toUpperCase() + opt.slice(1)}`,
                                    )}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          <div className="grid grid-cols-2 gap-2">
                            <div className="space-y-2">
                              <Label>{t("pages:gitDiff.excludeSafecmd")}</Label>
                              <Input
                                value={excludeSafecmd}
                                onChange={(e) =>
                                  setExcludeSafecmd(e.target.value)
                                }
                                placeholder={t(
                                  "pages:gitDiff.excludeSafecmdPlaceholder",
                                )}
                              />
                            </div>
                            <div className="space-y-2">
                              <Label>{t("pages:gitDiff.excludeTextcmd")}</Label>
                              <Input
                                value={excludeTextcmd}
                                onChange={(e) =>
                                  setExcludeTextcmd(e.target.value)
                                }
                                placeholder={t(
                                  "pages:gitDiff.excludeTextcmdPlaceholder",
                                )}
                              />
                            </div>
                          </div>
                        </>
                      )}

                      {diffEngine === "difflib" && (
                        <div className="grid grid-cols-2 gap-2">
                          <ColorField
                            label={t("pages:gitDiff.colorAdd")}
                            value={colorAdd}
                            onChange={setColorAdd}
                            fallback="#D7E8B1"
                          />
                          <ColorField
                            label={t("pages:gitDiff.colorDel")}
                            value={colorDel}
                            onChange={setColorDel}
                            fallback="#FFA3A3"
                          />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <Button
                className="w-full"
                onClick={handleRender}
                disabled={
                  rendering || !selectedMainFile || !baseRef || !targetRef
                }
              >
                {rendering ? (
                  <>
                    <Loader2 className="animate-spin" />
                    {t("pages:gitDiff.generating")}
                  </>
                ) : (
                  <>
                    <FileDiff />
                    {t("pages:gitDiff.generateDiffPdf")}
                  </>
                )}
              </Button>
            </CardContent>
          </Card>

          {result && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {t("pages:gitDiff.generationResult")}
                </CardTitle>
                <CardAction>
                  <Badge variant={result.success ? "secondary" : "destructive"}>
                    {result.success ? (
                      <>
                        <Check />
                        {t("pages:gitDiff.success")}
                      </>
                    ) : (
                      <>
                        <CircleX />
                        {t("pages:gitDiff.failed")}
                      </>
                    )}
                  </Badge>
                </CardAction>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline" className="font-mono">
                    {result.diff_engine === "latexdiff"
                      ? "latexdiff"
                      : "difflib"}
                  </Badge>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded bg-green-500/10 px-3 py-2 text-green-700 dark:text-green-300">
                    {t("pages:gitDiff.addedLines", {
                      count: result.added_lines || 0,
                    })}
                  </div>
                  <div className="rounded bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300">
                    {t("pages:gitDiff.deletedLines", {
                      count: result.deleted_lines || 0,
                    })}
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  {t("pages:gitDiff.expandedFiles", {
                    count: result.included_files?.length || 0,
                  })}
                </div>
                {result.explanation && (
                  <div>
                    <Label className="text-xs text-muted-foreground">
                      {t("pages:gitDiff.errorExplanation")}
                    </Label>
                    <p className="text-sm mt-1 whitespace-pre-wrap text-destructive">
                      {translateError(result.explanation)}
                    </p>
                  </div>
                )}
                {result.fixes && result.fixes.length > 0 && (
                  <div>
                    <Label className="text-xs text-muted-foreground">
                      {t("pages:gitDiff.fixSuggestions")}
                    </Label>
                    <ul className="text-sm mt-1 list-disc list-inside space-y-1">
                      {result.fixes.map((fix, i) => (
                        <li key={i}>{fix}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {result.success && result.pdf_url && (
                  <Button
                    variant="outline"
                    size="sm"
                    nativeButton={false}
                    render={
                      <a
                        href={result.pdf_url}
                        download="diff-highlight.pdf"
                        rel="noreferrer"
                      />
                    }
                  >
                    <Download />
                    {t("pages:gitDiff.downloadPdf")}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowRawOutput((value) => !value)}
                >
                  {showRawOutput
                    ? t("pages:gitDiff.hideRawOutput")
                    : t("pages:gitDiff.viewRawOutput")}
                </Button>
                {showRawOutput && (
                  <CodeBlock
                    code={`${result.stdout || t("pages:gitDiff.noStdout")}\n${result.stderr || t("pages:gitDiff.noStderr")}`}
                    language="plaintext"
                    className="max-h-48 p-2"
                  />
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="relative h-[720px]">
          {!pdfFullscreen && <PDFPreview url={pdfUrl} />}
          {pdfUrl && !pdfFullscreen && (
            <Button
              variant="outline"
              size="sm"
              className="absolute bottom-2 left-2 z-10 bg-background/80 backdrop-blur"
              onClick={() => setPdfFullscreen(true)}
            >
              <Expand />
              {t("pages:gitDiff.enterFullscreen")}
            </Button>
          )}
        </div>

        {pdfFullscreen && pdfUrl && (
          <div className="fixed inset-0 z-50 flex flex-col bg-background">
            <div className="flex items-center justify-between border-b px-4 py-2">
              <h2 className="text-sm font-semibold">
                {t("pages:gitDiff.fullscreenTitle")}
              </h2>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPdfFullscreen(false)}
              >
                <Minimize />
                {t("pages:gitDiff.quitFullscreen")}
              </Button>
            </div>
            <div className="flex-1 overflow-hidden p-4">
              <div className="h-full">
                <PDFPreview url={pdfUrl} />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
