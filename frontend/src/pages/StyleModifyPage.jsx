import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Wand2, Loader2, Copy } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { api } from "@/api/index";
import CodeBlock from "@/components/CodeBlock";
import CodeEditorInput from "@/components/CodeEditorInput";

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
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader
          title={t("pages:styleModify.title")}
          groupKey="code_review"
        />
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
              <CodeEditorInput
                value={latex}
                onChange={(e) => setLatex(e.target.value)}
                placeholder={t("pages:styleModify.sourcePlaceholder")}
                className="min-h-80"
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
                <CodeBlock
                  code={result}
                  language="latex"
                  className="p-4 max-h-96 text-sm"
                />
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
