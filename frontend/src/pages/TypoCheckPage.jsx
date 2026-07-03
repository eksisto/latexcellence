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
import { ArrowLeft, CheckCheck, Eye, Loader2 } from "lucide-react";
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

export default function TypoCheckPage() {
  const {
    fileTree,
    projectPath,
    activeFile,
    setActiveFile,
    setRootPath,
    setProjectPath,
    setFileTree,
    setMainFile,
    typoParagraphs,
    setTypoParagraphs,
    typoResults,
    setTypoResults,
    loadingStates,
    setLoading,
  } = useStore();

  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [restoring, setRestoring] = useState(false);
  const restoredRef = useRef(false);

  const texFiles = useMemo(() => flattenTexFiles(fileTree), [fileTree]);

  // 挂载时自动恢复项目状态（直接从 TypoCheckPage 刷新）
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
        // 恢复失败，忽略
      } finally {
        setRestoring(false);
      }
    };

    restore();
  }, []);

  const handlePreview = useCallback(async () => {
    if (!activeFile) {
      setError("请先选择一个文件");
      return;
    }
    setLoading("typoCheck", true);
    setError(null);
    setMessage(null);
    setTypoResults([]);
    setTypoParagraphs([]);
    try {
      const data = await api.typoCheck.preview(activeFile);
      setTypoParagraphs(data.paragraphs || []);
      setMessage(`已加载 ${data.total} 个段落`);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading("typoCheck", false);
    }
  }, [activeFile]);

  const handleCheck = useCallback(async () => {
    if (!activeFile) return;
    setLoading("typoCheck", true);
    setError(null);
    setMessage("正在检查错字...");
    try {
      const data = await api.typoCheck.check({
        file_path: activeFile,
      });
      setTypoResults(
        (data.results || []).slice().sort((a, b) => {
          const aHas = a.changes && a.changes.length > 0;
          const bHas = b.changes && b.changes.length > 0;
          if (aHas !== bHas) return aHas ? -1 : 1;
          return a.paragraph_index - b.paragraph_index;
        })
      );
      const withIssues = (data.results || []).filter(
        (r) => r.changes && r.changes.length > 0
      );
      setMessage(
        `发现 ${withIssues.length} 个段落可能有错字`
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading("typoCheck", false);
    }
  }, [activeFile]);

  const handleApplyChange = useCallback(
    async (idx, corrected) => {
      try {
        await api.typoCheck.apply({
          file_path: activeFile,
          changes: [{ paragraph_index: idx, new_text: corrected }],
        });
        setMessage(`已应用段落 ${idx} 的修改`);
        // 从结果中移除已应用的段落，保留其余结果
        setTypoResults((prev) =>
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
      setTypoParagraphs([]);
      setTypoResults([]);
      setError(null);
      setMessage(null);
    },
    [setActiveFile, setTypoParagraphs, setTypoResults]
  );

  const isLoading = loadingStates.typoCheck;

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">拼写检查</h1>
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
                  先转到项目工作区，打开一个项目，然后选择要检查的 .tex 文件
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
                    onClick={handleCheck}
                    disabled={
                      isLoading || typoParagraphs.length === 0
                    }
                  >
                    {isLoading ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <CheckCheck />
                    )}
                    {isLoading ? "检查中..." : "检查错字"}
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

            {/* paragraph preview */}
            {typoParagraphs.length > 0 && typoResults.length === 0 && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">
                    段落 ({typoParagraphs.length})
                  </CardTitle>
                  <CardDescription>
                    剥离 LaTeX 后的纯文本（将交由 LLM 检查）
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {typoParagraphs.map((p) => (
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

            {/* typo check results */}
            {typoResults.length > 0 && (
              <div className="space-y-4">
                {typoResults.map((result) => {
                  const hasChanges =
                    result.changes && result.changes.length > 0;
                  return (
                    <Card key={result.paragraph_index}>
                      <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                          <CardTitle className="text-sm">
                            段落 {result.paragraph_index}
                          </CardTitle>
                          {hasChanges && (
                            <span className="rounded bg-destructive/10 text-destructive px-1.5 py-0.5 text-xs font-medium">
                              {result.changes.length} 处问题
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
                                      <span className="rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 px-1 py-0.5 text-xs font-medium">
                                        {change.type}
                                      </span>
                                      {change.explanation && (
                                        <span className="text-muted-foreground text-xs">
                                          {change.explanation}
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
                                        修改：
                                      </span>
                                      <span className="font-medium">
                                        {change.corrected}
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
                                    result.corrected
                                  )
                                }
                              >
                                应用修改
                              </Button>
                            </>
                          ) : (
                            <p className="text-sm text-muted-foreground">
                                此段落未发现错字。
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
