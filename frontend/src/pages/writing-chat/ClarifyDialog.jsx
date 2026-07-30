import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { HelpCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

export default function ClarifyDialog({
  pendingClarification,
  onSubmit,
  onCancel,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const options = pendingClarification?.options;
  const hasOptions = Array.isArray(options) && options.length > 0;
  const [text, setText] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    setText("");
  }, [pendingClarification?.clarify_id]);

  const handleOpenAutoFocus = useCallback((e) => {
    e.preventDefault();
    inputRef.current?.focus();
  }, []);

  const fillOption = (opt) => {
    setText(opt);
    inputRef.current?.focus();
  };

  const handleSubmit = () => {
    const answer = text.trim();
    if (!answer) return;
    onSubmit(answer);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <Dialog
      open={!!pendingClarification}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent
        className="sm:max-w-lg"
        onOpenAutoFocus={handleOpenAutoFocus}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <HelpCircle className="size-4 text-primary" />
            {t("pages:writingChat.clarifyTitle")}
          </DialogTitle>
          <DialogDescription>
            {pendingClarification?.question || ""}
          </DialogDescription>
        </DialogHeader>

        <div className="py-2 space-y-3">
          {hasOptions && (
            <div className="flex flex-wrap gap-2">
              {options.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => fillOption(opt)}
                  className={`px-3 py-1.5 text-xs rounded-full border transition-colors cursor-pointer ${
                    text === opt
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border hover:border-muted-foreground/30 text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {opt}
                </button>
              ))}
            </div>
          )}

          <Textarea
            ref={inputRef}
            className="min-h-24 resize-none text-sm"
            placeholder={t("pages:writingChat.clarifyInputPlaceholder")}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {t("common:cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={!text.trim()}>
            {t("pages:writingChat.clarifySubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
