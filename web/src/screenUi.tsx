import React from "react";
import { ChevronLeft, ChevronRight, type LucideIcon } from "lucide-react";
import "./screens.css";

// The pieces the web's Settings and account screens share, in the app's
// design (ADR 44 MW4; mobile/src/settingsUi.tsx).

// A screen over the list or conversation, with Back always in reach.
export function ScreenFrame({
  title,
  onBack,
  children,
  bodyRef,
}: {
  title: string;
  onBack: () => void;
  children: React.ReactNode;
  bodyRef?: React.Ref<HTMLDivElement>;
}) {
  return (
    <section className="screen" aria-label={title}>
      <header className="screen-header">
        <button
          type="button"
          className="header-icon"
          aria-label="Back"
          onClick={onBack}
        >
          <ChevronLeft size={26} aria-hidden />
        </button>
        <h1>{title}</h1>
      </header>
      <div className="screen-body" ref={bodyRef}>
        {children}
      </div>
    </section>
  );
}

export function Section({
  title,
  children,
  outlined = false,
  sectionRef,
}: {
  // A page with one card needs no title over it.
  title?: string;
  children: React.ReactNode;
  // A link into Settings outlines what it opened (ADR 45 Q8).
  outlined?: boolean;
  sectionRef?: React.Ref<HTMLDivElement>;
}) {
  return (
    <div className="screen-section" ref={sectionRef}>
      {title && <h2>{title}</h2>}
      <div className={`screen-card${outlined ? " outlined" : ""}`}>
        {children}
      </div>
    </div>
  );
}

export function Explanation({ children }: { children: React.ReactNode }) {
  return <p className="screen-explanation">{children}</p>;
}

export function Note({
  children,
  tone = "error",
}: {
  children: React.ReactNode;
  tone?: "error" | "warning" | "info";
}) {
  return (
    <p
      className={`screen-note ${tone}`}
      role={tone === "info" ? "status" : "alert"}
    >
      {children}
    </p>
  );
}

// A row that opens another screen, with what it holds on the right.
export function LinkRow({
  label,
  detail,
  badge,
  onOpen,
}: {
  label: string;
  detail?: string;
  badge?: string;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="screen-row"
      aria-label={[label, badge, detail].filter(Boolean).join(", ")}
      onClick={onOpen}
    >
      <span className="screen-row-label">{label}</span>
      {badge && <span className="screen-badge">{badge}</span>}
      {detail && <span className="screen-row-detail">{detail}</span>}
      <ChevronRight size={18} aria-hidden />
    </button>
  );
}

// A row of the Settings menu (ADR 47 Q6), as the app's MenuRow: its icon on
// a soft accent tile, its name, what it holds and, when one needs attention,
// an amber word such as Paused. Never sparkles.
export function MenuRow({
  icon: Icon,
  label,
  detail,
  badge,
  onOpen,
}: {
  icon: LucideIcon;
  label: string;
  detail?: string;
  badge?: string;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="screen-row menu-row"
      aria-label={[label, badge, detail].filter(Boolean).join(", ")}
      onClick={onOpen}
    >
      <span className="menu-icon" aria-hidden>
        <Icon size={19} />
      </span>
      <span className="screen-row-label">{label}</span>
      {badge && <span className="screen-badge">{badge}</span>}
      {detail && <span className="screen-row-detail">{detail}</span>}
      <ChevronRight size={18} aria-hidden />
    </button>
  );
}

export function SwitchRow({
  label,
  explanation,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  explanation?: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="screen-block">
      <button
        type="button"
        role="switch"
        className="switch-line"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
      >
        <span>{label}</span>
        <span className="switch-knob" aria-hidden />
      </button>
      {explanation && <Explanation>{explanation}</Explanation>}
    </div>
  );
}

// One choice of a few side by side, with the chosen one's explanation
// beneath, as the app's ChoiceRow: a chat's Suggestions (ADR 56 Q12 A).
export function ChoiceRow<T extends string>({
  label,
  options,
  value,
  explanation,
  disabled,
  onChange,
}: {
  // The group's name; its section's title shows it.
  label: string;
  options: { value: T; label: string }[];
  // Null until the choice is known.
  value: T | null;
  explanation?: string;
  disabled: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <div className="screen-block">
      <div className="choice-segments" role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={option.value === value}
            disabled={disabled}
            onClick={() => option.value !== value && onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {explanation && <Explanation>{explanation}</Explanation>}
    </div>
  );
}

export function SmallButton({
  label,
  name,
  primary = false,
  disabled = false,
  onClick,
}: {
  label: string;
  // The accessible name, when the visible label is shorter.
  name?: string;
  primary?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`small-button${primary ? " primary-small" : ""}`}
      aria-label={name ?? label}
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
