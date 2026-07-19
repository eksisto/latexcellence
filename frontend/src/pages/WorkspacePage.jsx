import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ArrowLeft, FolderOpen, SpellCheck, Play, Loader2, Check, CircleAlert, ChevronDown, ChevronRight, FileDiff, Undo2, Redo2, BookOpen } from "lucide-react";
import { api } from "@/api/index";
import { translateError } from "@/utils/errorMessages";
import useStore from "@/store/useStore";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import useUndoRedo from "@/hooks/useUndoRedo";
import ProjectExplorer from "@/components/ProjectExplorer";
import ConfirmDialog from "@/components/ConfirmDialog";
import FolderPickerDialog from "@/components/FolderPickerDialog";
import PDFPreview from "@/components/PDFPreview";

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
  const projectPath = useStore((s) => s.projectPath);
  const workspaceId = useStore((s) => s.workspaceId);
  const rootPath = useStore((s) => s.rootPath);
  const fileTree = useStore((s) => s.fileTree);
  const activeFile = useStore((s) => s.activeFile);
  const mainFile = useStore((s) => s.mainFile);
  const setProjectPath = useStore((s) => s.setProjectPath);
  const setRootPath = useStore((s) => s.setRootPath);
  const setFileTree = useStore((s) => s.setFileTree);
  const setActiveFile = useStore((s) => s.setActiveFile);
  const setMainFile = useStore((s) => s.setMainFile);
  const clearProject = useStore((s) => s.clearProject);
  const enabledFeatures = useEnabledFeatures();
  const compileResult = useStore((s) => s.compileResult);
  const compileLoading = useStore((s) => s.compileLoading);
  const setCompileResult = useStore((s) => s.setCompileResult);
  const setCompileLoading = useStore((s) => s.setCompileLoading);
  const pdfUrl = useStore((s) => s.pdfUrl);
  const setPdfUrl = useStore((s) => s.setPdfUrl);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

  const [openPath, setOpenPath] = useState("");
  const [paragraphs, setParagraphs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showRawOutput, setShowRawOutput] = useState(false);
  const [confirmCloseOpen, setConfirmCloseOpen] = useState(false);
  const [confirmUndoOpen, setConfirmUndoOpen] = useState(false);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const restoredRef = useRef(false);

  const handleCloseProject = useCallback(() => {
    clearProject();
    setParagraphs([]);
    setPdfUrl(null);
    setConfirmCloseOpen(false);
    toast.success("项目已关闭");
  }, [clearProject, setPdfUrl]);

  const doOpenProject = useCallback(async (path) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.project.open(path, workspaceId);
      setRootPath(data.root_path);
      setProjectPath(path);
      setFileTree(data.file_tree);
      setMainFile(data.main_file);
      setActiveFile(data.main_file);
      try {
        const p = await api.project.paragraphs(data.main_file, workspaceId);
        setParagraphs(p.paragraphs || []);
      } catch {
        setParagraphs([]);
      }
      toast.success("项目已打开", { description: data.root_path });
    } catch (e) {
      const msg = translateError(e.message);
      setError(msg);
      toast.error("打开项目失败", { description: msg });
    } finally {
      setLoading(false);
    }
  }, []);

  // 启动时：从 Zustand persist 恢复项目
  useEffect(() => {
    const restore = async () => {
      if (restoredRef.current) return;
      if (projectPath && fileTree.length === 0) {
        restoredRef.current = true;
        await doOpenProject(projectPath);
      }
    };
    restore();
  }, []);

  const handleOpenProject = useCallback(async () => {
    if (!openPath.trim()) return;
    await doOpenProject(openPath.trim());
  }, [openPath, doOpenProject]);

  const handleBrowseFolder = useCallback(async () => {
    setFolderPickerOpen(true);
  }, []);

  const handleFolderSelected = useCallback(async (path) => {
    setOpenPath(path);
    await doOpenProject(path);
  }, [doOpenProject]);

  const handleMainFileChange = useCallback(async (path) => {
    setMainFile(path);
    try {
      await api.project.setMainFile(path, workspaceId);
    } catch (_) {
      // 忽略
    }
  }, [setMainFile]);

  const handleCompile = useCallback(async () => {
    if (!mainFile) return;
    setCompileLoading(true);
    setCompileResult(null);
    setPdfUrl(null);
    try {
      const data = await api.compile.run(mainFile, workspaceId);
      setCompileResult(data);
      if (data.success) {
        setPdfUrl(`${api.compile.pdfUrl(mainFile, workspaceId)}&t=${Date.now()}`);
        toast.success("编译成功");
      } else {
        toast.error("编译失败", {
          description: data.explanation || "请查看编译结果详情",
        });
      }
    } catch (e) {
      const msg = translateError(e.message);
      setCompileResult({ success: false, explanation: msg, fixes: [], stdout: "", stderr: "" });
      toast.error("编译失败", { description: msg });
    } finally {
      setCompileLoading(false);
    }
  }, [mainFile, setCompileLoading, setCompileResult, setPdfUrl]);

  const handleSelectFile = useCallback(async (node) => {
    if (node.is_dir) return;
    setActiveFile(node.path);
    try {
      const data = await api.project.paragraphs(node.path, workspaceId);
      setParagraphs(data.paragraphs || []);
    } catch (e) {
      setParagraphs([]);
    }
  }, []);

  const reloadActiveFileParagraphs = useCallback(async () => {
    if (!activeFile) return;
    try {
      const data = await api.project.paragraphs(activeFile, workspaceId);
      setParagraphs(data.paragraphs || []);
    } catch (_) {
      setParagraphs([]);
    }
  }, [activeFile]);

  const { canUndo, canRedo, busy: undoBusy, handleUndo, handleRedo } =
    useUndoRedo(activeFile, workspaceId, reloadActiveFileParagraphs);

  // 键盘快捷键：Ctrl+Z 撤销、Ctrl+Shift+Z / Ctrl+Y 重做
  // 输入框/文本域中不拦截，保留原生 undo 行为
  useEffect(() => {
    if (!projectPath) return;
    const onKey = (e) => {
      const target = e.target;
      const tag = target?.tagName;
      const isEditable =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        target?.isContentEditable;
      if (isEditable) return;
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault();
        handleRedo();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [projectPath, handleUndo, handleRedo]);

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
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
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="project-path">项目目录路径</Label>
                  <div className="flex gap-2">
                    <Input
                      id="project-path"
                      className="flex-1"
                      value={openPath}
                      onChange={(e) => setOpenPath(e.target.value)}
                      placeholder="例如：C:\Users\YourName\paper"
                      onKeyDown={(e) => e.key === "Enter" && handleOpenProject()}
                    />
                    <Button
                      variant="outline"
                      onClick={handleBrowseFolder}
                      disabled={loading}
                      type="button"
                    >
                      浏览
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    输入项目路径，或点击「浏览」进行选择
                  </p>
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
                  {texFiles.map((f) => (
                    <SelectItem key={f.path} value={f.path}>
                      {f.path}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {enabledFeatures?.compile && (
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
              )}
              {activeFile && (
                <div className="flex items-center gap-1 ml-auto">
                  <Tooltip>
                    <TooltipTrigger render={<Button variant="outline" size="icon" onClick={() => setConfirmUndoOpen(true)} disabled={!canUndo || undoBusy} />}>
                      <Undo2 />
                    </TooltipTrigger>
                    <TooltipContent>撤销 (Ctrl+Z)</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger render={<Button variant="outline" size="icon" onClick={handleRedo} disabled={!canRedo || undoBusy} />}>
                      <Redo2 />
                    </TooltipTrigger>
                    <TooltipContent>重做 (Ctrl+Shift+Z)</TooltipContent>
                  </Tooltip>
                </div>
              )}
              {enabledFeatures?.git_diff !== false && (
                <Button
                  variant="outline"
                  size="sm"
                  nativeButton={false}
                  render={<Link to="/git-diff" />}
                >
                  <FileDiff />
                  差异高亮
                </Button>
              )}
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
                  onClick={() => setConfirmCloseOpen(true)}
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
                      <div className="flex items-center gap-1">
                        <Tooltip>
                          <TooltipTrigger render={<Button variant="outline" size="sm" nativeButton={false} render={<Link to="/typo-check" />} />}>
                            <SpellCheck />
                          </TooltipTrigger>
                          <TooltipContent>文本校对</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger render={<Button variant="outline" size="sm" nativeButton={false} render={<Link to="/terminology-check" />} />}>
                            <BookOpen />
                          </TooltipTrigger>
                          <TooltipContent>术语一致性检查</TooltipContent>
                        </Tooltip>
                      </div>
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

          {/* 编译成功后内联 PDF 预览 */}
          {compileResult?.success && pdfUrl && (
            <Card className="mt-4">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">PDF 预览</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-[70vh]">
                  <PDFPreview url={pdfUrl} />
                </div>
              </CardContent>
            </Card>
          )}
          </>
        )}
      </div>

      <ConfirmDialog
        open={confirmCloseOpen}
        onOpenChange={setConfirmCloseOpen}
        title="关闭项目？"
        description="关闭后将清空当前已加载的段落和检查结果，下次需重新打开。未保存的修改已写回源文件，不会丢失。"
        confirmText="关闭项目"
        variant="destructive"
        onConfirm={handleCloseProject}
      />
      <ConfirmDialog
        open={confirmUndoOpen}
        onOpenChange={setConfirmUndoOpen}
        title="撤销最近一次修改？"
        description="将该文件回退到上一次修改前的状态。撤销后可通过重做恢复。"
        confirmText="撤销"
        onConfirm={async () => {
          await handleUndo();
          setConfirmUndoOpen(false);
        }}
      />
      <FolderPickerDialog
        open={folderPickerOpen}
        onOpenChange={setFolderPickerOpen}
        onSelect={handleFolderSelected}
        initialPath={openPath || ""}
      />
    </div>
  );
}
