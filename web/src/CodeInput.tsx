import React from "react";

// A one-time code's single field, as the app's (mobile/src/CodeField.tsx,
// ADR 47 Q3): digits only, with the browser's code autofill, and the last
// digit, typed or pasted, submits the code. There is no maximum length, so a
// pasted code with spaces keeps all its digits. A field whose length is
// unknown submits only from its form's button.
export function CodeInput({
  id,
  label,
  className,
  value,
  length,
  disabled = false,
  secure = false,
  onChange,
  onComplete,
}: {
  id?: string;
  label?: string;
  className?: string;
  value: string;
  length: number | null;
  disabled?: boolean;
  secure?: boolean;
  onChange: (value: string) => void;
  onComplete: (value: string) => void;
}) {
  return (
    <input
      id={id}
      aria-label={label}
      className={className}
      autoFocus
      type={secure ? "password" : "text"}
      inputMode="numeric"
      autoComplete="one-time-code"
      placeholder={length ? "•".repeat(length) : undefined}
      value={value}
      disabled={disabled}
      onChange={(event) => {
        const digits = event.target.value.replace(/[^0-9]/g, "");
        const next = length ? digits.slice(0, length) : digits;
        onChange(next);
        if (length && next.length === length && !disabled) onComplete(next);
      }}
    />
  );
}
