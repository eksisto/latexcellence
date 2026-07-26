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
import { ArrowLeft, Wand2, Loader2, Copy } from "lucide-react";
import { api } from "@/api/index";

export default function StyleModifyPage() {
  const { t } = useTranslation(["pages", "common"]);
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
          <Button
            variant="ghost"
            size="icon"
            nativeButton={false}
            render={<Link to="/" />}
          >
            <ArrowLeft />
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            {t("pages:styleModify.title")}
          </h1>
        </div>
      </div>

      <div className="max-w-6xl mx-auto grid grid-cols-2 gap-6">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {t("pages:styleModify.sourceCode")}
              </CardTitle>
              <CardDescription>
                {t("pages:styleModify.sourcePlaceholder")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                className="min-h-80 resize-none font-mono text-sm"
                value={latex}
                onChange={(e) => setLatex(e.target.value)}
                placeholder={t("pages:styleModify.sourcePlaceholder")}
              />
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                {t("pages:styleModify.requirement")}
              </CardTitle>
              <CardDescription>
                {t("pages:styleModify.requirementPlaceholder")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                className="min-h-32 resize-none"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("pages:styleModify.requirementExamples")}
              />
              <div className="flex items-center gap-2">
                <Button
                  onClick={handleModify}
                  disabled={loading || !latex.trim() || !description.trim()}
                >
                  {loading ? <Loader2 className="animate-spin" /> : <Wand2 />}
                  {loading
                    ? t("pages:styleModify.modifyBtnLoading")
                    : t("pages:styleModify.modifyBtn")}
                </Button>
                <span className="text-xs text-muted-foreground">
                  {t("pages:styleModify.submitHint")}
                </span>
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
            </CardContent>
          </Card>

          {result && (
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm">
                    {t("pages:styleModify.resultTitle")}
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
    </div>
  );
}
