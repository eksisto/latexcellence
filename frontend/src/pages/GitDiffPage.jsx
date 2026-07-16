import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  CircleAlert,
  Download,
  FileDiff,
  GitBranch,
  GitCommit,
  Loader2,
  RefreshCw,
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
import useStore from "@/store/useStore";

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

function groupRevisions(revisions) {
  return {
    worktree: revisions.filter((r) => r.kind === "worktree"),
    commits: revisions.filter((r) => r.kind === "commit"),
    branches: revisions.filter((r) => r.kind === "branch"),
    tags: revisions.filter((r) => r.kind === "tag"),
  };
}

function revisionLabel(revision) {
  if (!revision) return "";
  if (revision.kind === "worktree") return "当前工作区";
  return revision.label || revision.value;
}

export default function GitDiffPage() {
  const projectPath = useStore((s) => s.projectPath);
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
  const [showRawOutput, setShowRawOutput] = useState(false);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);
  const grouped = useMemo(() => groupRevisions(refs), [refs]);
  const labelMap = useMemo(() => {
    const map = {};
    for (const r of refs) {
      map[r.value] = revisionLabel(r);
    }
    return map;
  }, [refs]);

  const formatRefValue = useCallback(
    (val) => labelMap[val] || val,
    [labelMap]
  );

  useEffect(() => {
    if (!projectPath || fileTree.length > 0) return;

    let cancelled = false;
    api.project
      .open(projectPath)
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

  const loadRefs = useCallback(async () => {
    if (!projectPath) return;
    setLoadingRefs(true);
    setError("");
    try {
      const data = await api.gitDiff.refs();
      const revisions = data.revisions || [];
      setRefs(revisions);
      setGitRoot(data.git_root || "");
      setIsDirty(Boolean(data.is_dirty));
      if (!baseRef) {
        const head = revisions.find((item) => item.kind === "commit");
        setBaseRef(head?.value || "");
      }
      if (!targetRef) {
        setTargetRef(WORKTREE_REF);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingRefs(false);
    }
  }, [baseRef, projectPath, targetRef]);

  useEffect(() => {
    loadRefs();
  }, [loadRefs]);

  const handleRender = useCallback(async () => {
    if (!selectedMainFile || !baseRef || !targetRef) return;
    setRendering(true);
    setError("");
    setResult(null);
    setPdfUrl("");
    try {
      const data = await api.gitDiff.renderPdf({
        mainFile: selectedMainFile,
        baseRef,
        targetRef,
      });
      setResult(data);
      if (data.success && data.pdf_url) {
        setPdfUrl(`${data.pdf_url}?t=${Date.now()}`);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setRendering(false);
    }
  }, [baseRef, selectedMainFile, targetRef]);

  if (!projectPath) {
    return (
      <div className="min-h-screen p-8">
        <div className="max-w-3xl mx-auto">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <Card className="mt-8">
            <CardHeader>
              <CardTitle>请先打开项目</CardTitle>
              <CardDescription>
                差异高亮需要一个已打开且受 Git 管理的 LaTeX 项目。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button nativeButton={false} render={<Link to="/workspace" />}>
                打开项目工作区
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Git 差异高亮</h1>
            <p className="text-sm text-muted-foreground mt-1">
              选择主文件和两个 Git 状态，生成红删绿增的差异 PDF。源项目文件不会被修改。
            </p>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <FileDiff className="size-4" />
                差异设置
              </CardTitle>
              <CardDescription>
                多文件项目只需选择入口主文件，系统会展开常见的 input/include 依赖。
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
                <Label>主文件</Label>
                <Select
                  value={selectedMainFile}
                  onValueChange={setSelectedMainFile}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue className="min-w-0 truncate" placeholder="选择主 .tex 文件" />
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
                <Label>初始状态（旧版本）</Label>
                <Select value={baseRef} onValueChange={setBaseRef}>
                  <SelectTrigger className="w-full">
                    <SelectValue className="min-w-0 truncate" placeholder="选择初始 Git 状态">
                      {formatRefValue}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>工作区</SelectLabel>
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
                          提交（最近 {grouped.commits.length} 条）
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
                          分支
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
                          标签
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
                <Label>结束状态（新版本）</Label>
                <Select value={targetRef} onValueChange={setTargetRef}>
                  <SelectTrigger className="w-full">
                    <SelectValue className="min-w-0 truncate" placeholder="选择结束 Git 状态">
                      {formatRefValue}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>工作区</SelectLabel>
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
                          提交（最近 {grouped.commits.length} 条）
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
                          分支
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
                          标签
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
                <div>Git 根目录：{gitRoot || "未加载"}</div>
                <div>
                  工作区状态：{isDirty ? "有未提交修改" : "干净或未检测到修改"}
                </div>
                <div>项目目录：{rootPath}</div>
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <Button
                className="w-full"
                onClick={handleRender}
                disabled={rendering || !selectedMainFile || !baseRef || !targetRef}
              >
                {rendering ? (
                  <>
                    <Loader2 className="animate-spin" />
                    生成中...
                  </>
                ) : (
                  <>
                    <FileDiff />
                    生成差异 PDF
                  </>
                )}
              </Button>
            </CardContent>
          </Card>

          {result && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">生成结果</CardTitle>
                <CardAction>
                  <Badge variant={result.success ? "secondary" : "destructive"}>
                    {result.success ? (
                      <>
                        <Check />
                        成功
                      </>
                    ) : (
                      <>
                        <CircleAlert />
                        失败
                      </>
                    )}
                  </Badge>
                </CardAction>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded bg-green-500/10 px-3 py-2 text-green-700 dark:text-green-300">
                    增加 {result.added_lines || 0} 行
                  </div>
                  <div className="rounded bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300">
                    删除 {result.deleted_lines || 0} 行
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  已展开 {result.included_files?.length || 0} 个 LaTeX 文件
                </div>
                {result.explanation && (
                  <p className="text-sm text-destructive whitespace-pre-wrap">
                    {result.explanation}
                  </p>
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
                    下载 PDF
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowRawOutput((value) => !value)}
                >
                  {showRawOutput ? "隐藏原始输出" : "查看原始输出"}
                </Button>
                {showRawOutput && (
                  <pre className="max-h-48 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
                    {result.stdout || "(无标准输出)"}
                    {"\n"}
                    {result.stderr || "(无错误输出)"}
                  </pre>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="h-[720px]">
          <PDFPreview url={pdfUrl} />
        </div>
      </div>
    </div>
  );
}
