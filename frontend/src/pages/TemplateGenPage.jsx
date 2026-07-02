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
import { ArrowLeft, FileText, Loader2, Copy, Download } from "lucide-react";
import { api } from "@/api/index";

export default function TemplateGenPage() {
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [saved, setSaved] = useState(false);

  const handleGenerate = useCallback(async () => {
    if (!description.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setSaved(false);
    try {
      const data = await api.templateGen.generate({
        description: description.trim(),
      });
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [description]);

  const handleSave = useCallback(async () => {
    if (!result) return;
    try {
      const data = await api.templateGen.save({ template: result });
      setSaved(true);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [result]);

  const handleCopy = useCallback(() => {
    if (!result) return;
    const content = [
      result.preamble || "",
      "",
      "\\begin{document}",
      "",
      ...(result.body_structure || []).map(
        (s) => `\\section{${s.section}}\n% ${s.suggested_content || ""}\n`
      ),
      "\\end{document}",
    ].join("\n");
    navigator.clipboard.writeText(content).catch(() => {});
  }, [result]);

  const fullTemplate = result
    ? [
        result.preamble || "",
        "",
        "\\begin{document}",
        "",
        ...(result.body_structure || []).map(
          (s) => `\\section{${s.section}}\n% ${s.suggested_content || ""}\n`
        ),
        "\\end{document}",
      ].join("\n")
    : "";

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-6xl mx-auto mb-8">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">模板生成</h1>
        </div>
      </div>

      <div className="max-w-3xl mx-auto space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">描述你的需求</CardTitle>
            <CardDescription>
              描述你需要的 LaTeX 模板类型（如"IEEE 会议论文"、"中文简历"、"学术报告"）
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              className="min-h-24 resize-none"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="例如：一篇用于投稿 CVPR 的论文模板，包含摘要、引言、方法、实验、结论..."
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  handleGenerate();
                }
              }}
            />
            <div className="flex items-center gap-2">
              <Button
                onClick={handleGenerate}
                disabled={loading || !description.trim()}
              >
                {loading ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <FileText />
                )}
                {loading ? "生成中..." : "生成模板"}
              </Button>
              <span className="text-xs text-muted-foreground">
                Ctrl+Enter 快速生成
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
                <div>
                  <CardTitle className="text-sm">
                    {result.template_name || "生成的模板"}
                  </CardTitle>
                  <CardDescription>
                    {result.explanation}
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={handleCopy}>
                    <Copy />
                    复制
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleSave}
                    disabled={saved}
                  >
                    <Download />
                    {saved ? "已保存" : "保存 .tex 文件"}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {result.packages && result.packages.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-muted-foreground mb-1">
                    宏包
                  </h4>
                  <div className="flex flex-wrap gap-1">
                    {result.packages.map((pkg) => (
                      <span
                        key={pkg}
                        className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono"
                      >
                        {pkg}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {result.body_structure && result.body_structure.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-muted-foreground mb-1">
                    结构
                  </h4>
                  <ul className="space-y-1">
                    {result.body_structure.map((sec, i) => (
                      <li key={i} className="text-sm">
                        <span className="font-medium">
                          {i + 1}. {sec.section}
                        </span>
                        {sec.suggested_content && (
                          <span className="text-muted-foreground">
                            {" — "}
                            {sec.suggested_content}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <details>
                <summary className="cursor-pointer text-xs text-muted-foreground">
                  查看完整的 LaTeX 源代码
                </summary>
                <pre className="whitespace-pre-wrap mt-2 p-3 bg-muted rounded text-xs font-mono max-h-80 overflow-y-auto">
                  {fullTemplate}
                </pre>
              </details>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
