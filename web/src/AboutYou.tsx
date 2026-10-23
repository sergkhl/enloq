import React from "react";
import { MemoryPanel } from "./MemoryPanel";
import { ScreenFrame, Section } from "./screenUi";
import { TraitsPanel } from "./TraitsPanel";

// About you (ADR 55 Q21, Q25): one account's Account Memory, what you care
// about, and your traits, opened from Manage accounts.
export function AboutYou({
  account,
  name,
  onBack,
}: {
  account: number;
  name: string;
  onBack: () => void;
}) {
  return (
    <ScreenFrame title="About you" onBack={onBack}>
      <p className="small">{name}</p>
      <Section title="You care about">
        <MemoryPanel account={account} />
      </Section>
      <Section title="Your traits">
        <TraitsPanel account={account} />
      </Section>
    </ScreenFrame>
  );
}
