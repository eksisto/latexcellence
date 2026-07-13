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
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, FileText, Loader2, Copy } from "lucide-react";
import { api } from "@/api/index";

export default function MarkdownConvertPage() {
  const [markdown, setMarkdown] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const handleConvert = useCallback(async () => {
    if (!markdown.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.markdownConverter.convert(markdown);
      setResult(data.latex);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [markdown]);

  const handleCopy = useCallback(() => {
    if (!result) return;
    navigator.clipboard.writeText(result).catch(() => {});
  }, [result]);

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
            Markdown 转 LaTeX
          </h1>
        </div>
      </div>

      <div className="max-w-3xl mx-auto space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">粘贴 Markdown 内容</CardTitle>
            <CardDescription>
              将 Markdown 文本转换为 LaTeX 代码，基于 Pandoc 引擎
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              className="min-h-48 resize-none font-mono text-sm"
              value={markdown}
              onChange={(e) => setMarkdown(e.target.value)}
              placeholder={"# 标题\n\n这是**粗体**和*斜体*文字。\n\n- 列表项 1\n- 列表项 2\n\n$$E = mc^2$$"}
            />
            <div className="flex items-center gap-2">
              <Button
                onClick={handleConvert}
                disabled={loading || !markdown.trim()}
              >
                {loading ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <FileText />
                )}
                {loading ? "转换中..." : "转换为 LaTeX"}
              </Button>
              <span className="text-xs text-muted-foreground">
                需要安装 Pandoc
              </span>
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
          </CardContent>
        </Card>

        {result && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">
                  转换结果
                </CardTitle>
                <Button variant="outline" size="sm" onClick={handleCopy}>
                  <Copy />
                  复制
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <pre className="whitespace-pre-wrap p-4 bg-muted rounded text-sm font-mono max-h-96 overflow-y-auto">
                {result}
              </pre>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
