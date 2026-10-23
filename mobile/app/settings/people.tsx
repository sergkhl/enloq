import { Redirect } from "expo-router";
import React, { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  PEOPLE,
  askedAt,
  lastActive,
  personLine,
} from "../../../shared/people";
import { BUDGETS, budgetAmount, dollars } from "../../../shared/settings";
import type { Schemas } from "../../src/api";
import { SmallButton } from "../../src/BudgetCard";
import { useConnection } from "../../src/connection";
import { usePeople } from "../../src/people";
import {
  Divider,
  Explanation,
  RowNote,
  Section,
  SettingsPage,
} from "../../src/settingsUi";
import { usePalette } from "../../src/theme";
import { useHourCycle } from "../../src/time";

type Person = Schemas["Person"];
type Request = Schemas["JoinRequest"];

// Settings → People (ADR 48 Q19, the owner's only): Join Requests with
// Approve and Decline, then members with today's spending against each
// budget, Edit budgets and Remove. A join push opens it.
export default function PeopleRoute() {
  const { connection, role } = useConnection();
  if (connection.status === "unpaired") return <Redirect href="/pair" />;
  if (connection.status !== "paired") return null;
  if (role === "member") return <Redirect href="/settings" />;
  return <PeoplePage />;
}

function PeoplePage() {
  const { connection, api } = useConnection();
  const palette = usePalette();
  const hourCycle = useHourCycle();
  const { people, error, setPeople, load } = usePeople();
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const timezone =
    (connection.status === "paired" && connection.accounts?.timezone) || "UTC";

  async function act(id: string, action: () => Promise<void>) {
    setBusy(id);
    setNote("");
    try {
      await action();
    } catch (problem) {
      setNote(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  function remove(person: Person) {
    Alert.alert(
      PEOPLE.title,
      PEOPLE.removeConfirm(person.name || "this person"),
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove",
          style: "destructive",
          onPress: () =>
            void act(person.member, async () => {
              const removed = await api<Schemas["Removed"]>(
                `people/${person.member}/remove`,
                { body: { confirm: true } },
              );
              if (!removed.confirmed) setNote(PEOPLE.unconfirmed);
              await load();
            }),
        },
      ],
    );
  }

  return (
    <SettingsPage title={PEOPLE.title}>
      {note ? <RowNote>{note}</RowNote> : null}
      {!people ? (
        error ? (
          <RowNote tone="warning">People show once the server answers.</RowNote>
        ) : (
          <ActivityIndicator color={palette.accent} />
        )
      ) : !people.requests.length && !people.members.length ? (
        <Explanation>{PEOPLE.none}</Explanation>
      ) : (
        <>
          {people.requests.length ? (
            <Section title={PEOPLE.requests}>
              {people.requests.map((request, index) => (
                <View key={request.id}>
                  {index ? <Divider /> : null}
                  <RequestRow
                    request={request}
                    timezone={timezone}
                    hourCycle={hourCycle}
                    busy={busy === request.id}
                    onDecide={(decision) =>
                      void act(request.id, async () =>
                        setPeople(
                          await api<Schemas["People"]>(
                            `people/${request.id}/${decision}`,
                            { body: {} },
                          ),
                        ),
                      )
                    }
                  />
                </View>
              ))}
              <Explanation>{PEOPLE.requestNote}</Explanation>
            </Section>
          ) : null}
          {people.members.length ? (
            <Section title={PEOPLE.members}>
              {people.members.map((person, index) => (
                <View key={person.member}>
                  {index ? <Divider /> : null}
                  <PersonRow
                    person={person}
                    timezone={timezone}
                    hourCycle={hourCycle}
                    busy={busy === person.member}
                    onChanged={load}
                    onRemove={() => remove(person)}
                  />
                </View>
              ))}
              <Explanation>{PEOPLE.memberNote}</Explanation>
            </Section>
          ) : null}
        </>
      )}
    </SettingsPage>
  );
}

function RequestRow({
  request,
  timezone,
  hourCycle,
  busy,
  onDecide,
}: {
  request: Request;
  timezone: string;
  hourCycle: ReturnType<typeof useHourCycle>;
  busy: boolean;
  onDecide: (decision: "approve" | "decline") => void;
}) {
  const palette = usePalette();
  const name = request.name || "Someone";
  return (
    <View style={styles.person} accessibilityLabel={name}>
      <Text style={[styles.name, { color: palette.text }]}>{name}</Text>
      <Explanation>
        {[
          personLine(request),
          askedAt(request.requested_at, timezone, hourCycle),
        ]
          .filter(Boolean)
          .join(" · ")}
      </Explanation>
      <View style={styles.actions}>
        <SmallButton
          label="Decline"
          accessibilityLabel={`Decline ${name}`}
          disabled={busy}
          palette={palette}
          onPress={() => onDecide("decline")}
        />
        <SmallButton
          label="Approve"
          accessibilityLabel={`Approve ${name}`}
          primary
          disabled={busy}
          palette={palette}
          onPress={() => onDecide("approve")}
        />
      </View>
    </View>
  );
}

function PersonRow({
  person,
  timezone,
  hourCycle,
  busy,
  onChanged,
  onRemove,
}: {
  person: Person;
  timezone: string;
  hourCycle: ReturnType<typeof useHourCycle>;
  busy: boolean;
  onChanged: () => Promise<void>;
  onRemove: () => void;
}) {
  const palette = usePalette();
  const name = person.name || "Someone";
  const [editing, setEditing] = useState(false);
  return (
    <View style={styles.person} accessibilityLabel={name}>
      <Text style={[styles.name, { color: palette.text }]}>{name}</Text>
      <Explanation>
        {[
          personLine(person),
          `${person.accounts} ${person.accounts === 1 ? "account" : "accounts"}`,
          lastActive(person.last_active, timezone, hourCycle),
        ]
          .filter(Boolean)
          .join(" · ")}
      </Explanation>
      <Text style={[styles.spent, { color: palette.text }]}>
        {`${BUDGETS.daily.title}: ${dollars(person.budget.spent, 4)} of ${dollars(person.budget.limit)} today`}
      </Text>
      <Text style={[styles.spent, { color: palette.text }]}>
        {`${BUDGETS.group_briefing.title}: ${dollars(person.group_budget.spent, 4)} of ${dollars(person.group_budget.limit)} today`}
      </Text>
      {editing ? (
        <BudgetEditor
          person={person}
          onDone={async () => {
            setEditing(false);
            await onChanged();
          }}
        />
      ) : (
        <View style={styles.actions}>
          <SmallButton
            label="Remove"
            accessibilityLabel={`Remove ${name}`}
            disabled={busy}
            palette={palette}
            onPress={onRemove}
          />
          <SmallButton
            label="Edit budgets"
            accessibilityLabel={`Edit ${name}'s budgets`}
            disabled={busy}
            palette={palette}
            onPress={() => setEditing(true)}
          />
        </View>
      )}
    </View>
  );
}

function BudgetEditor({
  person,
  onDone,
}: {
  person: Person;
  onDone: () => Promise<void>;
}) {
  const { api } = useConnection();
  const palette = usePalette();
  const [daily, setDaily] = useState((person.budget.limit / 1e9).toFixed(2));
  const [group, setGroup] = useState(
    (person.group_budget.limit / 1e9).toFixed(2),
  );
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    const typed = { daily: budgetAmount(daily), group: budgetAmount(group) };
    if (typed.daily === null || typed.group === null) {
      setProblem("$0 to $10, in whole cents.");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      for (const [family, limit] of [
        ["daily", typed.daily],
        ["group_briefing", typed.group],
      ] as const)
        await api(`people/${person.member}/budget`, {
          body: { family, limit_usd: limit },
        });
      await onDone();
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }
  const input = [
    styles.input,
    {
      color: palette.text,
      borderColor: palette.line,
      backgroundColor: palette.background,
    },
  ];
  return (
    <View style={styles.editor}>
      {(
        [
          ["daily", daily, setDaily],
          ["group_briefing", group, setGroup],
        ] as const
      ).map(([family, value, set]) => (
        <View key={family} style={styles.inputRow}>
          <Text style={[styles.inputLabel, { color: palette.text }]}>
            {`${BUDGETS[family].title} $`}
          </Text>
          <TextInput
            accessibilityLabel={`${person.name || "Their"} ${BUDGETS[family].title} per day in dollars`}
            inputMode="decimal"
            keyboardType="decimal-pad"
            value={value}
            editable={!busy}
            onChangeText={(next) => {
              set(next);
              setProblem("");
            }}
            style={input}
          />
        </View>
      ))}
      <View style={styles.actions}>
        <SmallButton
          label="Cancel"
          disabled={busy}
          palette={palette}
          onPress={() => void onDone()}
        />
        <SmallButton
          label="Save"
          primary
          disabled={busy}
          palette={palette}
          onPress={() => void save()}
        />
      </View>
      {problem ? <RowNote>{problem}</RowNote> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  person: { paddingVertical: 12, gap: 6 },
  name: { fontSize: 16, fontWeight: "600" },
  spent: { fontSize: 14 },
  actions: { flexDirection: "row", gap: 10, justifyContent: "flex-end" },
  editor: { gap: 8 },
  inputRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  inputLabel: { fontSize: 15, flexShrink: 1 },
  input: {
    flex: 1,
    minWidth: 60,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    fontSize: 16,
  },
});
