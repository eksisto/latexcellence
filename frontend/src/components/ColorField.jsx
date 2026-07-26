import { useRef, useCallback } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const FALLBACK = "#D7E8B1";

function isValidHex(hex) {
  return /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(hex);
}

export default function ColorField({
  label,
  value,
  onChange,
  fallback = FALLBACK,
  className,
}) {
  const ref = useRef(null);
  const resolved = value || fallback;

  const handleSwatchClick = useCallback(() => {
    ref.current?.click();
  }, []);

  const handleNativeChange = useCallback(
    (e) => {
      onChange(e.target.value.toUpperCase());
    },
    [onChange],
  );

  const handleInputChange = useCallback(
    (e) => {
      const raw = e.target.value;
      if (isValidHex(raw)) {
        onChange(raw.toUpperCase());
      } else {
        onChange(raw);
      }
    },
    [onChange],
  );

  const handleInputBlur = useCallback(() => {
    if (!isValidHex(value)) {
      onChange(fallback);
    }
  }, [value, fallback, onChange]);

  return (
    <div className={className}>
      <Label className="mb-1.5 block">{label}</Label>
      <div className="flex items-center gap-2.5">
        <button
          type="button"
          onClick={handleSwatchClick}
          className="relative h-8 w-8 shrink-0 rounded-lg border border-input bg-transparent shadow-sm transition-shadow hover:ring-2 hover:ring-ring/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
          aria-label={label}
        >
          <span
            className="absolute inset-1 rounded-md"
            style={{ backgroundColor: resolved }}
          />
          <input
            ref={ref}
            type="color"
            value={resolved}
            onChange={handleNativeChange}
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
          />
        </button>
        <Input
          value={value}
          onChange={handleInputChange}
          onBlur={handleInputBlur}
          placeholder={fallback}
          className="h-8 w-full min-w-0 font-mono text-xs tabular-nums"
          maxLength={7}
          spellCheck={false}
        />
      </div>
    </div>
  );
}
