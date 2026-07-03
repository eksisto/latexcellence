import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, PencilLine, Eye, Loader2, Plus, Trash2 } from "lucide-react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";

function flattenTexFiles(tree, prefix = "") {
  const result = [];
  for (const node of tree) {
    if (node.is_dir && node.children) {
      result.push(...flattenTexFiles(node.children, prefix + node.name + "/"));
    } else if (node.name.endsWith(".tex")) {
      result.push({ name: prefix + node.name, path: node.path });
    }
  }
  return result;
}

export default function ExpressionOptPage() {
  const {
    fileTree,
    projectPath,
    activeFile,
    setActiveFile,
    setRootPath,
    setProjectPath,
    setFileTree,
    setMainFile,
    expressionParagraphs,
    setExpressionParagraphs,
    expressionResults,
    setExpressionResults,
    loadingStates,
    setLoading,
  } = useStore();

  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [restoring, setRestoring] = useState(false);
  const restoredRef = useRef(false);

  // prompt management
  const [prompts, setPrompts] = useState([]);
  const [activePrompt, setActivePrompt] = useState("");
  const [newPromptName, setNewPromptName] = useState("");
  const [editingPrompt, setEditingPrompt] = useState(null);
  const [promptContent, setPromptContent] = useState("");

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

  // ── auto-restore project on mount ──────────────────────────────────────

  useEffect(() => {
    if (restoredRef.current) return;
    if (!projectPath || fileTree.length > 0) return;

    const restore = async () => {
      restoredRef.current = true;
      setRestoring(true);
      try {
        const data = await api.project.open(projectPath);
        setRootPath(data.root_path);
        setProjectPath(projectPath);
        setFileTree(data.file_tree);
        setMainFile(data.main_file);
        if (!activeFile) {
          setActiveFile(data.main_file);
        }
      } catch (_) {
        // ignore
      } finally {
        setRestoring(false);
      }
    };

    restore();
  }, []);

  // ── load prompts on mount ──────────────────────────────────────────────

  useEffect(() => {
    api.expressionOpt.prompts.list().then((data) => {
      setPrompts(data.prompts || []);
      const active = (data.prompts || []).find((p) => p.active);
      if (active) setActivePrompt(active.name);
    }).catch(() => {});
  }, []);

  // ── actions ────────────────────────────────────────────────────────────

  const handlePreview = useCallback(async () => {
    if (!activeFile) return;
    setLoading("expressionOpt", true);
    setError(null);
    setMessage(null);
    setExpressionResults([]);
    try {
      const data = await api.expressionOpt.preview(activeFile);
      setExpressionParagraphs(data.paragraphs || []);
      setMessage(`已加载 ${data.total} 个段落`);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading("expressionOpt", false);
    }
  }, [activeFile]);

  const handleOptimize = useCallback(async () => {
    if (!activeFile) return;
    setLoading("expressionOpt", true);
    setError(null);
    setMessage("正在优化...");
    try {
      const data = await api.expressionOpt.optimize({
        file_path: activeFile,
      });
      setExpressionResults(
        (data.results || []).slice().sort((a, b) => {
          const aHas = a.changes && a.changes.length > 0;
          const bHas = b.changes && b.changes.length > 0;
          if (aHas !== bHas) return aHas ? -1 : 1;
          return a.paragraph_index - b.paragraph_index;
        })
      );
      const withChanges = (data.results || []).filter(
        (r) => r.changes && r.changes.length > 0
      );
      setMessage(`找到 ${withChanges.length} 个有优化建议的段落`);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading("expressionOpt", false);
    }
  }, [activeFile]);

  const handleApplyChange = useCallback(
    async (idx, optimized) => {
      try {
        await api.expressionOpt.apply({
          file_path: activeFile,
          changes: [{ paragraph_index: idx, new_text: optimized }],
        });
        setMessage(`已应用第 ${idx} 段的优化建议`);
        setExpressionResults((prev) =>
          prev.filter((r) => r.paragraph_index !== idx)
        );
      } catch (e) {
        setError(e.message);
      }
    },
    [activeFile]
  );

  const handleFileChange = useCallback(
    (path) => {
      setActiveFile(path);
      setExpressionParagraphs([]);
      setExpressionResults([]);
      setError(null);
      setMessage(null);
    },
    [setActiveFile, setExpressionParagraphs, setExpressionResults]
  );

  // ── prompt actions ─────────────────────────────────────────────────────

  const handleCreatePrompt = useCallback(async () => {
    const name = newPromptName.trim();
    if (!name || !promptContent.trim()) return;
    try {
      await api.expressionOpt.prompts.save(name, promptContent);
      const data = await api.expressionOpt.prompts.list();
      setPrompts(data.prompts || []);
      setNewPromptName("");
      setPromptContent("");
      setEditingPrompt(name);
    } catch (e) {
      setError(e.message);
    }
  }, [newPromptName, promptContent]);

  const handleDeletePrompt = useCallback(async (name) => {
    try {
      await api.expressionOpt.prompts.delete(name);
      const data = await api.expressionOpt.prompts.list();
      setPrompts(data.prompts || []);
      if (editingPrompt === name) {
        setEditingPrompt(null);
        setPromptContent("");
      }
      if (activePrompt === name) setActivePrompt("");
    } catch (e) {
      setError(e.message);
    }
  }, [editingPrompt, activePrompt]);

  const handleSetActivePrompt = useCallback(async (name) => {
    try {
      await api.expressionOpt.prompts.setActive(name);
      setActivePrompt(name);
      setMessage(name ? `当前启用提示词：${name}` : "使用默认提示词");
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const handleEditPrompt = useCallback(async (name) => {
    try {
      const data = await api.expressionOpt.prompts.load(name);
      setEditingPrompt(name);
      setPromptContent(data.content || "");
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const isLoading = loadingStates.expressionOpt;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">表达优化</h1>
        </div>
      </div>

      <div className="max-w-4xl mx-auto">
        {restoring ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-md w-full">
              <CardHeader>
                <CardTitle>正在恢复项目...</CardTitle>
              </CardHeader>
              <CardContent className="flex justify-center">
                <Loader2 className="animate-spin size-6" />
              </CardContent>
            </Card>
          </div>
        ) : !projectPath || texFiles.length === 0 ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-md w-full">
              <CardHeader>
                <CardTitle>未选择文件</CardTitle>
                <CardDescription>
                  先转到项目工作区，打开一个项目，然后选择要优化的 .tex 文件
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button className="w-full" asChild>
                  <Link to="/workspace">打开工作区</Link>
                </Button>
              </CardContent>
            </Card>
          </div>
        ) : (
          <div className="space-y-4">
            {/* file selector + controls */}
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center gap-3">
                  <Select
                    value={activeFile || ""}
                    onValueChange={handleFileChange}
                  >
                    <SelectTrigger className="min-w-60">
                      <SelectValue placeholder="选择 .tex 文件" />
                    </SelectTrigger>
                    <SelectContent>
                      {texFiles.map((f) => (
                        <SelectItem key={f.path} value={f.path}>
                          {f.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-3">
                  <Button
                    onClick={handlePreview}
                    disabled={isLoading || !activeFile}
                    variant="outline"
                  >
                    <Eye />
                    预览
                  </Button>
                  <Button
                    onClick={handleOptimize}
                    disabled={isLoading || expressionParagraphs.length === 0}
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <PencilLine />
                    )}
                    {isLoading ? "正在优化..." : "开始优化"}
                  </Button>
                </div>
                {message && (
                  <p className="text-sm text-muted-foreground mt-2">
                    {message}
                  </p>
                )}
                {error && (
                  <p className="text-sm text-destructive mt-2">{error}</p>
                )}
              </CardContent>
            </Card>

            {/* custom prompts */}

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">优化提示词</CardTitle>
                <CardDescription>
                  选择或自定义 LLM 的优化策略
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  {prompts.map((p) => (
                    <div key={p.name} className="flex items-center gap-1">
                      <Button
                        variant={activePrompt === p.name ? "default" : "outline"}
                        size="sm"
                        onClick={() =>
                          activePrompt === p.name
                            ? handleSetActivePrompt("")
                            : handleSetActivePrompt(p.name)
                        }
                      >
                        {p.name}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6"
                        onClick={() => handleEditPrompt(p.name)}
                      >
                        <PencilLine className="size-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6"
                        onClick={() => handleDeletePrompt(p.name)}
                      >
                        <Trash2 className="size-3" />
                      </Button>
                    </div>
                  ))}
                </div>
                {prompts.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    暂无自定义提示词，请在下方创建一个
                  </p>
                )}

                {editingPrompt && (
                  <p className="text-xs text-muted-foreground">
                    正在编辑：{editingPrompt}
                  </p>
                )}

                <div className="flex items-end gap-2">
                  <div className="flex-1 space-y-1">
                    <Label htmlFor="prompt-name" className="text-xs">
                      名称
                    </Label>
                    <Input
                      id="prompt-name"
                      className="h-7 text-sm"
                      value={newPromptName}
                      onChange={(e) => setNewPromptName(e.target.value)}
                      placeholder="例如：academic-formal"
                    />
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleCreatePrompt}
                    disabled={!newPromptName.trim() || !promptContent.trim()}
                  >
                    <Plus />
                    保存
                  </Button>
                </div>
                <Textarea
                  className="min-h-24 resize-none text-sm"
                  value={promptContent}
                  onChange={(e) => setPromptContent(e.target.value)}
                  placeholder="输入给 LLM 的自定义提示词说明..."
                />
              </CardContent>
            </Card>

            {/* paragraph preview */}
            {expressionParagraphs.length > 0 && expressionResults.length === 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    段落（{expressionParagraphs.length}）
                  </CardTitle>
                  <CardDescription>
                    去除 LaTeX 标记后的纯文本
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {expressionParagraphs.map((p) => (
                      <div
                        key={p.index}
                        className="rounded-lg border border-border p-3"
                      >
                        <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono mb-1 inline-block">
                          P{p.index}
                        </span>
                        <pre className="whitespace-pre-wrap text-sm text-foreground/80 mt-1 max-h-32 overflow-y-auto">
                          {p.plain_text || "（空）"}
                        </pre>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* optimization results */}
            {expressionResults.length > 0 && (
              <div className="space-y-4">
                {expressionResults.map((result) => {
                  const hasChanges =
                    result.changes && result.changes.length > 0;
                  return (
                    <Card key={result.paragraph_index}>
                      <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                          <CardTitle className="text-sm">
                            第 {result.paragraph_index} 段
                          </CardTitle>
                          {hasChanges && (
                            <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                              {result.changes.length} 条建议
                            </span>
                          )}
                        </div>
                      </CardHeader>
                      <CardContent>
                        <div className="space-y-3">
                          {hasChanges ? (
                            <>
                              <ul className="space-y-2">
                                {result.changes.map((change, i) => (
                                  <li
                                    key={i}
                                    className="rounded border border-border p-2 text-sm"
                                  >
                                    <div className="flex items-center gap-2 mb-1">
                                      <span className="rounded bg-green-500/10 text-green-600 dark:text-green-400 px-1 py-0.5 text-xs font-medium">
                                        {change.type}
                                      </span>
                                      {change.reason && (
                                        <span className="text-muted-foreground text-xs">
                                          {change.reason}
                                        </span>
                                      )}
                                    </div>
                                    <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm">
                                      <span className="text-muted-foreground">
                                        原文：
                                      </span>
                                      <span className="line-through text-foreground/60">
                                        {change.original}
                                      </span>
                                      <span className="text-muted-foreground">
                                        修改后：
                                      </span>
                                      <span className="font-medium">
                                        {change.optimized}
                                      </span>
                                    </div>
                                  </li>
                                ))}
                              </ul>
                              <Button
                                size="sm"
                                onClick={() =>
                                  handleApplyChange(
                                    result.paragraph_index,
                                    result.optimized
                                  )
                                }
                              >
                                应用优化
                              </Button>
                            </>
                          ) : (
                            <p className="text-sm text-muted-foreground">
                              当前段落没有优化建议
                            </p>
                          )}
                          <details className="text-xs text-muted-foreground">
                            <summary className="cursor-pointer">
                              查看原文
                            </summary>
                            <pre className="whitespace-pre-wrap mt-1 p-2 bg-muted rounded text-xs">
                              {result.plain_text || result.original}
                            </pre>
                          </details>
                        </div>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
