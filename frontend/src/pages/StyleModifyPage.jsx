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
import { ArrowLeft, Wand2, Loader2, Copy } from "lucide-react";
import { api } from "@/api/index";

export default function StyleModifyPage() {
  const [latex, setLatex] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const handleModify = useCallback(async () => {
    if (!latex.trim() || !description.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.styleModify.modify(latex, description);
      setResult(data.latex);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [latex, description]);

  const handleCopy = useCallback(() => {
    if (!result) return;
    navigator.clipboard.writeText(result).catch(() => {});
  }, [result]);

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            LaTeX 样式修改
          </h1>
        </div>
      </div>

      <div className="max-w-6xl mx-auto grid grid-cols-2 gap-6">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">LaTeX 源码</CardTitle>
              <CardDescription>
                粘贴需要修改的 LaTeX 代码
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                className="min-h-80 resize-none font-mono text-sm"
                value={latex}
                onChange={(e) => setLatex(e.target.value)}
                placeholder={`例如：

\\documentclass{article}
\\begin{document}

\\begin{table}[h]
\\centering
\\begin{tabular}{|c|c|c|}
\\hline
Name & Value & Note \\\\
\\hline
A & 1 & First \\\\
B & 2 & Second \\\\
\\hline
\\end{tabular}
\\end{table}

\\end{document}`}
              />
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">修改要求</CardTitle>
              <CardDescription>
                用自然语言描述你想做的样式修改
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                className="min-h-32 resize-none"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="例如：&#10;&#10;- 表格第一列改为居中对齐&#10;- 将 Name 加粗&#10;- 将两张图片并排放置&#10;- 调整表格位置到页面顶部"
              />
              <div className="flex items-center gap-2">
                <Button
                  onClick={handleModify}
                  disabled={loading || !latex.trim() || !description.trim()}
                >
                  {loading ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Wand2 />
                  )}
                  {loading ? "修改中..." : "开始修改"}
                </Button>
                <span className="text-xs text-muted-foreground">
                  Ctrl+Enter 快速提交
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
                    修改结果
                  </CardTitle>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleCopy}
                  >
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
    </div>
  );
}
