import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation(["pages", "common"]);
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
          <Button variant="ghost" size="icon" nativeButton={false} render={<Link to="/" />}>
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            {t("pages:markdownConvert.title")}
          </h1>
        </div>
      </div>

      <div className="max-w-3xl mx-auto space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t("pages:markdownConvert.inputLabel")}</CardTitle>
            <CardDescription>
              {t("pages:markdownConvert.inputDesc")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              className="min-h-48 resize-none font-mono text-sm"
              value={markdown}
              onChange={(e) => setMarkdown(e.target.value)}
              placeholder={t("pages:markdownConvert.inputPlaceholder")}
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
                {loading ? t("pages:markdownConvert.convertBtnLoading") : t("pages:markdownConvert.convertBtn")}
              </Button>
              <span className="text-xs text-muted-foreground">
                {t("pages:markdownConvert.needPandoc")}<a href="https://pandoc.org/installing.html" target="_blank" rel="noopener noreferrer" className="relative inline-block no-underline after:content-[''] after:absolute after:left-0 after:bottom-0 after:h-[0.5px] after:w-full after:bg-current after:origin-left after:transition-transform after:duration-300 hover:after:scale-x-0">
                  Pandoc
                </a>
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
                  {t("pages:markdownConvert.resultTitle")}
                </CardTitle>
                <Button variant="outline" size="sm" onClick={handleCopy}>
                  <Copy />
                  {t("common:copy")}
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
