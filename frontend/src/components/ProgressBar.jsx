import { Card, CardContent } from "@/components/ui/card";

export default function ProgressBar({ current, total, fileName }) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const basename = fileName ? fileName.split("/").pop() : null;

  return (
    <Card>
      <CardContent className="py-3">
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {basename ? `${basename} ` : ""}
            段落 {current} / {total}
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
        </div>
      </CardContent>
    </Card>
  );
}
