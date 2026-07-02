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
} from "@/components/ui/card";
import { ArrowLeft, FolderOpen, SpellCheck } from "lucide-react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";
import ProjectExplorer from "@/components/ProjectExplorer";

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
  } = useStore();

  const [openPath, setOpenPath] = useState("");
  const [paragraphs, setParagraphs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [lastPath, setLastPath] = useState("");
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
          <div className="grid grid-cols-4 gap-6">
            {/* file tree */}
            <div className="col-span-1">
              <Card className="h-full">
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
        )}
      </div>
    </div>
  );
}
