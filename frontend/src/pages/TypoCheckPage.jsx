import { useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, CheckCheck, Eye, Loader2 } from "lucide-react";
import { api } from "@/api/index";
import useStore from "@/store/useStore";

export default function TypoCheckPage() {
  const {
    fileTree,
    activeFile,
    setActiveFile,
    typoParagraphs,
    setTypoParagraphs,
    typoResults,
    setTypoResults,
    loadingStates,
    setLoading,
  } = useStore();

  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const handlePreview = useCallback(async () => {
    if (!activeFile) {
      setError("请先在项目工作区中选择一个文件");
      return;
    }
    setLoading("typoCheck", true);
    setError(null);
    setMessage(null);
    setTypoResults([]);
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
      setTypoResults(data.results || []);
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
        // refresh preview
        handlePreview();
      } catch (e) {
        setError(e.message);
      }
    },
    [activeFile, handlePreview]
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

        {!activeFile ? (
          <div className="flex justify-center mt-12">
            <Card className="max-w-md w-full">
              <CardHeader>
                <CardTitle>未选择文件</CardTitle>
                <CardDescription>
                  先转到项目工作区，打开一个项目，然后先选择一个 .tex 文件
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
            {/* controls */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">{activeFile}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-3">
                  <Button
                    onClick={handlePreview}
                    disabled={isLoading}
                    variant="outline"
                  >
                    <Eye />
                    预览
                  </Button>
                  <Button
                    onClick={handleCheck}
                    disabled={isLoading || typoParagraphs.length === 0}
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
