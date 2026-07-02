import { useState, useCallback, useEffect } from "react";
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
import { ArrowLeft, FolderOpen } from "lucide-react";
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

  // 启动时读取上次保存的项目路径
  useEffect(() => {
    let cancelled = false;
    api.project.lastOpened().then((data) => {
      if (!cancelled && data.path && data.exists) {
        setLastPath(data.path);
        setOpenPath(data.path);
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

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
                  {loading ? "Opening..." : "打开项目"}
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
                  <CardTitle className="text-sm">
                    {activeFile || "Select a file"}
                  </CardTitle>
                  <CardDescription>
                    {rootPath && <span className="text-xs">{rootPath}</span>}
                  </CardDescription>
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
                              Lines {p.line_start}-{p.line_end}
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
