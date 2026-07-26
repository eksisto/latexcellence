import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, ShieldCheck, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

export default function CaptchaDialog({
  challenge,
  submitting,
  onSubmit,
  onDismiss,
}) {
  const { t } = useTranslation(["pages", "common"]);
  const [selected, setSelected] = useState(new Set());

  const toggle = (hash) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(hash)) next.delete(hash);
      else next.add(hash);
      return next;
    });
  };

  if (!challenge) return null;

  const gridSize = challenge.images.length <= 4 ? 2 : 3;

  return (
    <Dialog
      open={!!challenge}
      onOpenChange={(open) => {
        if (!open) onDismiss();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-amber-500" />
            {t("pages:writingChat.captchaTitle", {
              instruction: challenge.instruction
                ? ` — ${challenge.instruction}`
                : "",
            })}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {t("pages:writingChat.captchaDesc", {
              engine: challenge.engine_name
                ? `("${challenge.engine_name}")`
                : "",
            })}
          </p>

          <div
            className="grid gap-2 mx-auto"
            style={{
              gridTemplateColumns: `repeat(${gridSize}, minmax(0, 1fr))`,
            }}
          >
            {challenge.images.map((img) => {
              const isSelected = selected.has(img.hash);
              return (
                <button
                  key={img.hash}
                  type="button"
                  onClick={() => toggle(img.hash)}
                  className={`relative rounded-lg overflow-hidden border-2 transition-all cursor-pointer ${
                    isSelected
                      ? "border-primary ring-2 ring-primary/30"
                      : "border-border hover:border-muted-foreground/50"
                  }`}
                >
                  <img
                    src={`data:image/jpeg;base64,${img.base64}`}
                    alt={t("pages:writingChat.captchaImage")}
                    className="w-full aspect-square object-cover"
                    draggable={false}
                  />
                  {isSelected && (
                    <div className="absolute inset-0 bg-primary/20 flex items-center justify-center">
                      <Check className="size-6 text-primary drop-shadow-md" />
                    </div>
                  )}
                </button>
              );
            })}
          </div>

          <p className="text-xs text-muted-foreground text-center">
            {t("pages:writingChat.captchaSelected", { count: selected.size })}
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onDismiss} disabled={submitting}>
            {t("pages:writingChat.captchaSkip")}
          </Button>
          <Button
            onClick={() => onSubmit(Array.from(selected))}
            disabled={submitting || selected.size === 0}
          >
            {submitting && <Loader2 className="size-3 animate-spin mr-1" />}
            {t("pages:writingChat.captchaSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
