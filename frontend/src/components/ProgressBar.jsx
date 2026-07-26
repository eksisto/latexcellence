import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Pause, Play, Square } from "lucide-react";
import { useTranslation } from "react-i18next";

export default function ProgressBar({
  current,
  total,
  fileName,
  isPaused,
  onPauseToggle,
  onStop,
}) {
  const { t } = useTranslation("components");
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const basename = fileName ? fileName.split("/").pop() : null;

  return (
    <Card>
      <CardContent className="py-3">
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {basename ? `${basename} ` : ""}
            {t("progressBar.paragraphProgress", { current, total })}
          </span>
          <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-xs text-muted-foreground whitespace-nowrap tabular-nums">
            {pct}%
          </span>
          {(onPauseToggle || onStop) && (
            <div className="flex items-center gap-1 ml-1">
              {onPauseToggle && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  onClick={onPauseToggle}
                  title={
                    isPaused ? t("progressBar.resume") : t("progressBar.pause")
                  }
                >
                  {isPaused ? (
                    <Play className="size-3.5" />
                  ) : (
                    <Pause className="size-3.5" />
                  )}
                </Button>
              )}
              {onStop && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-destructive hover:text-destructive"
                  onClick={onStop}
                  title={t("progressBar.stop")}
                >
                  <Square className="size-3.5" />
                </Button>
              )}
            </div>
          )}
        </div>
        {isPaused && (
          <p className="text-xs text-muted-foreground mt-1.5">
            {t("progressBar.paused")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
