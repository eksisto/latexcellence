import { useState, useCallback, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  CardAction,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { ArrowLeft, FolderOpen, SpellCheck, Play, Loader2, Check, CircleAlert, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import ProjectExplorer from "@/components/ProjectExplorer";

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

export default function WorkspacePage() {
  const {
    projectPath,
    rootPath,
    fileTree,
    activeFile,
    mainFile,
    setProjectPath,
    setRootPath,
    setFileTree,
    setActiveFile,
    setMainFile,
    clearProject,
    compileResult,
    compileLoading,
    setCompileResult,
    setCompileLoading,
  } = useStore();

  const [openPath, setOpenPath] = useState("");
  const [paragraphs, setParagraphs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [lastPath, setLastPath] = useState("");
  const [showRawOutput, setShowRawOutput] = useState(false);
  const restoredRef = useRef(false);

  const doOpenProject = useCallback(async (path) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.project.open(path);
      setRootPath(data.root_path);
      setProjectPath(path);
      setFileTree(data.file_tree);
      setMainFile(data.main_file);
      setActiveFile(data.main_file);
      setParagraphs([]);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // 启动时：读取上次保存的项目路径 / 自动恢复已打开的项目
  useEffect(() => {
    let cancelled = false;

    const restore = async () => {
      if (restoredRef.current) return;

      // 优先从 Zustand persist 恢复（刷新前已打开的项目）
      if (projectPath && fileTree.length === 0) {
        restoredRef.current = true;
        await doOpenProject(projectPath);
        return;
      }

      // 其次从后端 lastOpened 获取路径
      try {
        const data = await api.project.lastOpened();
        if (!cancelled && data.path && data.exists) {
          setLastPath(data.path);
          setOpenPath(data.path);
          // 如果 Zustand 中没有项目状态，则自动打开上次项目
          if (!projectPath) {
            restoredRef.current = true;
            await doOpenProject(data.path);
          }
        }
      } catch (_) {
        // 忽略错误
      }
    };

    restore();
    return () => { cancelled = true; };
  }, []);

  const handleOpenProject = useCallback(async () => {
    if (!openPath.trim()) return;
    await doOpenProject(openPath.trim());
  }, [openPath, doOpenProject]);

  const handleOpenLastProject = useCallback(async () => {
    if (!lastPath) return;
    await doOpenProject(lastPath);
  }, [lastPath, doOpenProject]);

  const handleMainFileChange = useCallback(async (path) => {
    setMainFile(path);
    try {
      await api.project.setMainFile(path);
    } catch (_) {
      // 忽略
    }
  }, [setMainFile]);

  const handleCompile = useCallback(async () => {
    if (!mainFile) return;
    setCompileLoading(true);
    setCompileResult(null);
    try {
      const data = await api.compile.run(mainFile);
      setCompileResult(data);
    } catch (e) {
      setCompileResult({ success: false, explanation: e.message, fixes: [], stdout: "", stderr: "" });
    } finally {
      setCompileLoading(false);
    }
  }, [mainFile, setCompileLoading, setCompileResult]);

  const handleSelectFile = useCallback(async (node) => {
    if (node.is_dir) return;
    setActiveFile(node.path);
    try {
      const data = await api.project.paragraphs(node.path);
      setParagraphs(data.paragraphs || []);
    } catch (e) {
      setParagraphs([]);
    }
  }, []);

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            项目工作区
          </h1>
        </div>
      </div>

      <div className="max-w-6xl mx-auto">

        {!projectPath ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-lg w-full">
              <CardHeader>
                <CardTitle>打开一个 LaTeX 项目</CardTitle>
                <CardDescription>
                  输入 LaTeX 项目目录路径
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="project-path">项目目录路径</Label>
                  <Input
                    id="project-path"
                    value={openPath}
                    onChange={(e) => setOpenPath(e.target.value)}
                    placeholder="例如：C:\Users\YourName\paper"
                    onKeyDown={(e) => e.key === "Enter" && handleOpenProject()}
                  />
                </div>
                {error && (
                  <p className="text-destructive text-sm">{error}</p>
                )}
                <Button
                  className="w-full"
                  onClick={handleOpenProject}
                  disabled={loading || !openPath.trim()}
                >
                  <FolderOpen />
                  {loading ? "打开中..." : "打开项目"}
                </Button>
                {lastPath && (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={handleOpenLastProject}
                    disabled={loading}
                  >
                    <FolderOpen />
                    打开上次项目
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>
        ) : (
          <>
            {/* 编译工具栏 */}
            <div className="flex items-center gap-3 mb-4">
              <Label className="text-sm whitespace-nowrap">主文件</Label>
              <Select value={mainFile || ""} onValueChange={handleMainFileChange}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder="选择主 .tex 文件" />
                </SelectTrigger>
                <SelectContent>
                  {flattenTexFiles(fileTree).map((f) => (
                    <SelectItem key={f.path} value={f.path}>
                      {f.path}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                onClick={handleCompile}
                disabled={!mainFile || compileLoading}
                size="sm"
              >
                {compileLoading ? (
                  <><Loader2 className="animate-spin" /> 编译中...</>
                ) : (
                  <><Play /> 自动编译</>
                )}
              </Button>
            </div>

            <div className="grid grid-cols-4 gap-6">
            {/* file tree */}
            <div className="col-span-1">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">文件</CardTitle>
                </CardHeader>
                <CardContent className="p-2">
                  <ProjectExplorer
                    tree={fileTree}
                    onSelect={handleSelectFile}
                  />
                </CardContent>
              </Card>
              <div className="mt-2 space-y-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full text-xs"
                  onClick={clearProject}
                >
                  关闭项目
                </Button>
              </div>
            </div>

            {/* paragraph viewer */}
            <div className="col-span-3">
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle className="text-sm">
                        {activeFile || "选择一个文件"}
                      </CardTitle>
                      <CardDescription>
                        {rootPath && <span className="text-xs">{rootPath}</span>}
                      </CardDescription>
                    </div>
                    {activeFile && activeFile.endsWith(".tex") && (
                      <Button variant="outline" size="sm" asChild>
                        <Link to="/typo-check">
                          <SpellCheck />
                        </Link>
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  {paragraphs.length === 0 ? (
                    <p className="text-muted-foreground text-sm">
                      {activeFile
                        ? "未找到段落或文件为空"
                        : "在文件目录中点击一个 .tex 文件以查看其段落"}
                    </p>
                  ) : (
                    <div className="space-y-4">
                      {paragraphs.map((p) => (
                        <div
                          key={p.index}
                          className="rounded-lg border border-border p-3"
                        >
                          <div className="flex items-center gap-2 mb-2">
                            <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                              P{p.index}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              第 {p.line_start}-{p.line_end} 行
                            </span>
                          </div>
                          <pre className="whitespace-pre-wrap text-sm font-mono text-foreground/80">
                            {p.text}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>

          {/* 编译结果 */}
          {compileResult && (
            <Card className="mt-4">
              <CardHeader>
                <CardTitle className="text-sm">编译结果</CardTitle>
                <CardAction>
                  <Badge variant={compileResult.success ? "secondary" : "destructive"}>
                    {compileResult.success ? (
                      <><Check /> 成功</>
                    ) : (
                      <><CircleAlert /> 失败</>
                    )}
                  </Badge>
                </CardAction>
              </CardHeader>
              <CardContent className="space-y-3">
                {compileResult.explanation && (
                  <div>
                    <Label className="text-xs text-muted-foreground">错误解释</Label>
                    <p className="text-sm mt-1 whitespace-pre-wrap">{compileResult.explanation}</p>
                  </div>
                )}
                {compileResult.fixes && compileResult.fixes.length > 0 && (
                  <div>
                    <Label className="text-xs text-muted-foreground">修复建议</Label>
                    <ul className="text-sm mt-1 list-disc list-inside space-y-1">
                      {compileResult.fixes.map((fix, i) => (
                        <li key={i}>{fix}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => setShowRawOutput((v) => !v)}
                  >
                    {showRawOutput ? <ChevronDown /> : <ChevronRight />}
                    查看原始输出
                  </Button>
                  {showRawOutput && (
                    <pre className="mt-2 p-2 rounded bg-muted text-xs max-h-48 overflow-auto whitespace-pre-wrap">
                      {compileResult.stdout || "(无标准输出)"}
                      {"\n"}
                      {compileResult.stderr || "(无错误输出)"}
                    </pre>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
          </>
        )}
      </div>
    </div>
  );
}
