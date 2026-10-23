import { Redirect, useLocalSearchParams } from "expo-router";
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, ScrollView } from "react-native";
import { BudgetCard } from "../../src/BudgetCard";
import { PEOPLE } from "../../../shared/people";
import { useConnection } from "../../src/connection";
import { useSettings } from "../../src/settings";
import { RowNote, Section, SettingsPage } from "../../src/settingsUi";
import { usePalette } from "../../src/theme";
import { useHourCycle } from "../../src/time";

// Settings → Budget (ADR 47 Q6): the Daily and Group Briefing Budgets with
// today's spending (ADR 45 Q6). A budget message's link opens this page with
// its budget scrolled into view and outlined for a moment (ADR 45 Q8).
export default function BudgetRoute() {
  const { connection } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  return <BudgetPage />;
}

function BudgetPage() {
  const { connection, role } = useConnection();
  const readOnly = role === "member";
  const palette = usePalette();
  const hourCycle = useHourCycle();
  const { settings, error, write } = useSettings();
  const params = useLocalSearchParams<{ section?: string }>();
  const target =
    params.section === "daily" || params.section === "group"
      ? params.section
      : null;
  const [outlined, setOutlined] = useState(target);
  const scroll = useRef<ScrollView>(null);
  const scrolled = useRef(false);
  const unreachable = connection.status === "paired" && connection.unreachable;
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";

  // The outline fades once the owner has seen where they were taken.
  useEffect(() => {
    if (!outlined) return;
    const timer = setTimeout(() => setOutlined(null), 2500);
    return () => clearTimeout(timer);
  }, [outlined]);

  function reveal(family: "daily" | "group", y: number) {
    if (target !== family || scrolled.current) return;
    scrolled.current = true;
    scroll.current?.scrollTo({ y: Math.max(0, y - 8), animated: true });
  }

  return (
    <SettingsPage title="Budget" scrollRef={scroll}>
      {unreachable ? (
        <RowNote tone="warning">
          Settings can be changed once the server answers.
        </RowNote>
      ) : null}
      {settings ? (
        <>
          {readOnly ? (
            <RowNote tone="info">{PEOPLE.memberBudget}</RowNote>
          ) : null}
          <Section
            onLayout={(event) => reveal("daily", event.nativeEvent.layout.y)}
          >
            <BudgetCard
              family="daily"
              outlined={outlined === "daily"}
              state={settings.budget}
              disabled={unreachable}
              timezone={timezone}
              hourCycle={hourCycle}
              write={write}
              readOnly={readOnly}
            />
          </Section>
          <Section
            onLayout={(event) => reveal("group", event.nativeEvent.layout.y)}
          >
            <BudgetCard
              family="group_briefing"
              outlined={outlined === "group"}
              state={settings.group_budget}
              disabled={unreachable}
              timezone={timezone}
              hourCycle={hourCycle}
              write={write}
              readOnly={readOnly}
            />
          </Section>
        </>
      ) : error ? (
        <RowNote tone="warning">Settings show once the server answers.</RowNote>
      ) : (
        <ActivityIndicator color={palette.accent} />
      )}
    </SettingsPage>
  );
}
