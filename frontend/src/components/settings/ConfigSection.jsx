/** 设置对话框共享原语：分区容器与复选框 */

export function CheckboxField({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 cursor-pointer rounded border-input accent-primary"
      />
      <span className="text-sm">{label}</span>
    </label>
  );
}

export default function ConfigSection({ title, children }) {
  return (
    <fieldset className="space-y-3 rounded-lg border p-3">
      <legend className="px-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        {title}
      </legend>
      <div className="space-y-3">{children}</div>
    </fieldset>
  );
}
