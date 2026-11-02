import { useState, useCallback, useRef, useEffect } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import PageHeader from "@/components/PageHeader";
import ModelBadge from "@/components/ModelBadge";
import CodeBlock from "@/components/CodeBlock";
import Upload from "lucide-react/dist/esm/icons/upload.mjs";
import Loader2 from "lucide-react/dist/esm/icons/loader-circle.mjs";
import Copy from "lucide-react/dist/esm/icons/copy.mjs";

const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];

/**
 * 通用图片 OCR 页面组件。
 *
 * 将 FormulaOCR 与 TableOCR 的共享逻辑（拖拽上传、粘贴、识别、复制）
 * 抽取为单一组件，通过 props 配置差异化的标题、图标、API 和结果展示。
 *
 * @param {object} props
 * @param {string} props.title              页面标题
 * @param {React.ComponentType} props.icon  识别按钮图标
 * @param {(formData: FormData) => Promise<object>} props.recognize  识别 API 调用
 * @param {string} props.recognizeButtonText  识别按钮文案
 * @param {(result: object, setResult: (updater) => void) => React.ReactNode} [props.renderResultMeta]  结果头部额外信息（可选第二参数用于更新 result）
 * @param {string} [props.resultEmptyText="(empty)"]  结果为空时的占位文本
 */
export default function ImageOCRPage({
  title,
  icon: Icon,
  recognize,
  recognizeButtonText,
  renderResultMeta,
  resultEmptyText = "(empty)",
}) {
  const { t } = useTranslation(["components", "common"]);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  const handleFile = useCallback(
    (f) => {
      if (!f) return;
      if (!ACCEPTED_TYPES.includes(f.type)) {
        toast.error(t("imageOcr.unsupportedFormat"), {
          description: t("imageOcr.unsupportedFormatDesc"),
        });
        return;
      }
      setFile(f);
      setResult(null);

      const reader = new FileReader();
      reader.onload = (e) => setPreview(e.target.result);
      reader.readAsDataURL(f);
    },
    [t],
  );

  const handleInputChange = useCallback(
    (e) => {
      handleFile(e.target.files?.[0]);
    },
    [handleFile],
  );

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragging(false);
      handleFile(e.dataTransfer.files?.[0]);
    },
    [handleFile],
  );

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  // Ctrl+V 粘贴剪切板图片
  useEffect(() => {
    const onPaste = (e) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          e.preventDefault();
          const blob = item.getAsFile();
          if (blob) {
            const f = new File([blob], "clipboard.png", {
              type: blob.type || "image/png",
            });
            handleFile(f);
          }
          break;
        }
      }
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [handleFile]);

  const handleRecognize = useCallback(async () => {
    if (!file) return;
    setLoading(true);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const data = await recognize(formData);
      setResult(data);
      toast.success(t("imageOcr.recognizeComplete"));
    } catch (e) {
      toast.error(t("imageOcr.recognizeFailed"), { description: e.message });
    } finally {
      setLoading(false);
    }
  }, [file, recognize]);

  const handleCopy = useCallback(() => {
    if (!result?.latex) return;
    navigator.clipboard
      .writeText(result.latex)
      .then(() => toast.success(t("imageOcr.copySuccess")))
      .catch(() => toast.error(t("imageOcr.copyFailed")));
  }, [result]);

  const handleClear = useCallback(() => {
    setFile(null);
    setPreview(null);
    setResult(null);
  }, []);

  return (
    <div className="min-h-screen p-8 animate-tab-enter">
      <div className="max-w-6xl mx-auto">
        <PageHeader title={title}>
          {/* OCR 槽位当前生效模型（formula_ocr / table_ocr 共用） */}
          <ModelBadge role="ocr" />
        </PageHeader>
      </div>

      <div className="max-w-2xl mx-auto space-y-4">
        {/* upload zone */}
        <Card>
          <CardContent className="p-6">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_TYPES.join(",")}
              className="hidden"
              onChange={handleInputChange}
            />
            <div
              className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
                ${dragging ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/50"}`}
              onClick={() => fileInputRef.current?.click()}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
            >
              {preview ? (
                <div className="flex flex-col items-center gap-2">
                  <img
                    src={preview}
                    alt="Preview"
                    className="max-h-60 object-contain rounded"
                  />
                  <span className="text-xs text-muted-foreground">
                    {file?.name} ({(file?.size / 1024).toFixed(1)} KB)
                  </span>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <Upload className="size-8 text-muted-foreground" />
                  <span className="text-sm">{t("imageOcr.dropHint")}</span>
                  <span className="text-xs text-muted-foreground">
                    {t("imageOcr.formatHint")}
                  </span>
                </div>
              )}
            </div>

            {file && (
              <div className="flex items-center gap-2 mt-3">
                <Button
                  onClick={handleRecognize}
                  disabled={loading}
                  className="flex-1"
                >
                  {loading ? <Loader2 className="animate-spin" /> : <Icon />}
                  {loading ? t("imageOcr.recognizing") : recognizeButtonText}
                </Button>
                <Button variant="outline" size="sm" onClick={handleClear}>
                  {t("imageOcr.clear")}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* result */}
        {result && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">
                  {t("imageOcr.resultTitle")}
                </CardTitle>
                <div className="flex items-center gap-2">
                  {result.low_confidence && (
                    <span className="rounded bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 px-1.5 py-0.5 text-xs font-medium">
                      {t("imageOcr.lowConfidence")}
                    </span>
                  )}
                  {renderResultMeta && renderResultMeta(result, setResult)}
                  <Button variant="outline" size="sm" onClick={handleCopy}>
                    <Copy />
                    Copy
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <CodeBlock
                code={result.latex || resultEmptyText}
                language="latex"
                className="p-3 text-sm"
              />
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
