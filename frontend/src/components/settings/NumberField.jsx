import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * 设置分区共享数字输入（草稿态承载自由输入）。
 *
 * 受控数字输入在非法中间值（清空、删除中）不提交时会被 React 受控值
 * 立即回弹，字段无法清空。这里用本地草稿态承载自由输入，仅在失焦时把
 * 空/非法值归一化到 [min, max]，输入中只有合法整数才即时提交。
 *
 * - min：下限，默认 1（多数配置字段均 ≥ 1）；传 0 允许 0 值
 *   （如 writing_chat_retention_days 的"不自动删，仅统计"语义）
 * - max：可选上限（如 SMTP 端口 ≤ 65535），失焦时越界值收敛到上限
 */
export default function NumberField({
  label,
  value,
  onChange,
  hint,
  min = 1,
  max,
}) {
  const [draft, setDraft] = useState(() => String(value ?? min));

  // 外部值变化（如重置/保存后回填）时同步草稿
  useEffect(() => {
    setDraft(String(value ?? min));
  }, [value, min]);

  const commit = () => {
    const n = parseInt(draft, 10);
    let safe = Number.isNaN(n) || n < min ? min : n;
    if (max != null && safe > max) safe = max;
    if (String(safe) !== draft) setDraft(String(safe));
    onChange(safe);
  };

  const inRange = (n) =>
    Number.isInteger(n) && n >= min && (max == null || n <= max);

  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input
        type="number"
        min={String(min)}
        max={max != null ? String(max) : undefined}
        value={draft}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          // 输入中仅当是合法的 [min, max] 整数才即时提交（"" 等中间态不阻塞输入）
          if (/^\d+$/.test(raw) && inRange(Number(raw))) {
            onChange(Number(raw));
          }
        }}
        onBlur={commit}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
