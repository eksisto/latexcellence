import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, PencilLine, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function EditOpCard({ edit_op, reason, onApply, applying }) {
  const { t } = useTranslation(["pages", "common"]);
  const [isEditing, setIsEditing] = useState(false);
  const [editedText, setEditedText] = useState(edit_op.new_latex);

  const handleApply = () => {
    if (isEditing) {
      onApply(editedText);
    } else {
      onApply();
    }
  };

  return (
    <Card className="w-full max-w-[90%] border-blue-500/30">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs flex items-center gap-2">
          <PencilLine className="size-3.5" />
          {t("pages:writingChat.editSuggestion", {
            paragraphIndex: edit_op.paragraph_index,
            filePath: edit_op.file_path,
          })}
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-1 space-y-2">
        {reason && <p className="text-xs text-muted-foreground">{reason}</p>}
        <div
          className={`grid grid-cols-1 ${isEditing ? "" : "md:grid-cols-2"} gap-2`}
        >
          <div>
            <div className="text-xs text-muted-foreground mb-1">
              {t("common:original")}
            </div>
            <pre className="text-xs font-mono bg-muted rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
              {edit_op.original_latex}
            </pre>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">
              {t("pages:writingChat.newText")}
            </div>
            {isEditing ? (
              <textarea
                autoFocus
                className="w-full min-h-32 text-xs font-mono bg-background border border-border rounded p-2 resize-y whitespace-pre-wrap"
                value={editedText}
                onChange={(e) => setEditedText(e.target.value)}
              />
            ) : (
              <pre className="text-xs font-mono bg-green-500/5 border border-green-500/20 rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">
                {edit_op.new_latex}
              </pre>
            )}
          </div>
        </div>
        {edit_op.diff_text && !isEditing && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              {t("pages:writingChat.viewUnifiedDiff")}
            </summary>
            <pre className="font-mono bg-muted rounded p-2 mt-1 max-h-64 overflow-y-auto whitespace-pre">
              {edit_op.diff_text}
            </pre>
          </details>
        )}
        <div className="flex items-center gap-2">
          {edit_op.applied ? (
            <span className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
              <Check className="size-3.5" />
              {t("pages:writingChat.applied")}
            </span>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  if (isEditing) {
                    setIsEditing(false);
                    setEditedText(edit_op.new_latex);
                  } else {
                    setIsEditing(true);
                    setEditedText(edit_op.new_latex);
                  }
                }}
              >
                <PencilLine className="size-3" />
                {isEditing
                  ? t("pages:writingChat.cancelEdit")
                  : t("pages:writingChat.manualEdit")}
              </Button>
              <Button size="sm" onClick={handleApply} disabled={applying}>
                {applying && <Loader2 className="size-3 animate-spin" />}
                {t("pages:writingChat.applyToTex")}
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
