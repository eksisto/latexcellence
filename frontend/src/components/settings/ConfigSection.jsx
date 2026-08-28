/** 设置对话框共享原语：分区容器与复选框 */

export function CheckboxField({ label, checked, onChange, disabled = false }) {
  return (
    <label
      className={`flex items-center gap-2 select-none ${
        disabled ? "opacity-50" : "cursor-pointer"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 cursor-pointer rounded border-input accent-primary disabled:cursor-not-allowed"
      />
      <span className="text-sm">{label}</span>
    </label>
  );
}

export default function ConfigSection({ title, children }) {
  return (
    <fieldset className="space-y-3 rounded-lg border bg-muted/40 p-3">
      <legend className="px-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        {title}
      </legend>
      <div className="space-y-3">{children}</div>
    </fieldset>
  );
}
