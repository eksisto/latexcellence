import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BadgeInfo } from "lucide-react";

export default function DiffViewer({ original, modified, changes = [] }) {
  const { t } = useTranslation("components");
  const hasChanges = changes.length > 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">
          {hasChanges
            ? t("diffViewer.changesFound", { count: changes.length })
            : t("diffViewer.noChanges")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {hasChanges ? (
          <ul className="space-y-2">
            {changes.map((change, i) => (
              <li
                key={i}
                className="flex flex-col gap-1 rounded-lg border border-border p-3 text-sm"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5 text-xs font-medium",
                      change.type === "typo"
                        ? "bg-destructive/10 text-destructive"
                        : "bg-blue-500/10 text-blue-600 dark:text-blue-400"
                    )}
                  >
                    {change.type}
                  </span>
                  {change.explanation && (
                    <span className="text-muted-foreground text-xs">
                      {change.explanation}
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 mt-1">
                  <span className="text-muted-foreground text-xs">{t("common:original")}</span>
                  <span className="text-foreground/70 line-through">
                    {change.original}
                  </span>
                  <span className="text-muted-foreground text-xs">{t("diffViewer.modified")}</span>
                  <span className="text-foreground font-medium">
                    {change.corrected || change.optimized}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <BadgeInfo className="size-4" />
            <span>{original || t("diffViewer.noContent")}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
