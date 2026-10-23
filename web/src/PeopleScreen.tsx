import React, { useCallback, useEffect, useRef, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { useChanges } from "./events";
import {
  Explanation,
  Note,
  ScreenFrame,
  Section,
  SmallButton,
} from "./screenUi";
import { PEOPLE, askedAt, lastActive, personLine } from "../../shared/people";
import { BUDGETS, budgetAmount, dollars } from "../../shared/settings";

type People = components["schemas"]["People"];
type Person = components["schemas"]["Person"];
type Request = components["schemas"]["JoinRequest"];
type Removed = components["schemas"]["Removed"];

const message = (problem: unknown) =>
  problem instanceof Error ? problem.message : String(problem);

// The owner's People, as the app's (mobile/app/settings/people.tsx): any
// change to members rereads it.
export function usePeople() {
  const [people, setPeople] = useState<People | null>(null);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const load = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const value = await workspaceApi<People>("people");
      if (mine === generation.current) {
        setPeople(value);
        setError("");
      }
    } catch (problem) {
      if (mine === generation.current) setError(message(problem));
    }
  }, []);
  useEffect(() => {
    void load();
    return () => {
      ++generation.current;
    };
  }, [load]);
  useChanges((change) => {
    if (
      change.all ||
      ["people", "spending", "accounts"].some((scope) =>
        change.shared.includes(scope),
      )
    )
      void load();
  });
  return { people, error, setPeople, load };
}

// Settings → People (ADR 48 Q19, owner only): Join Requests with Approve
// and Decline, then members with today's spending against each budget, Edit
// and Remove.
export function PeopleScreen({
  timezone,
  onBack,
}: {
  timezone: string;
  onBack: () => void;
}) {
  const { people, error, setPeople, load } = usePeople();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  async function act(id: string, action: () => Promise<void>) {
    setBusy(id);
    setNote("");
    try {
      await action();
    } catch (problem) {
      setNote(message(problem));
    } finally {
      setBusy("");
    }
  }
  return (
    <ScreenFrame title={PEOPLE.title} onBack={onBack}>
      {note && <Note>{note}</Note>}
      {!people ? (
        error ? (
          <Note tone="warning">People show once the server answers.</Note>
        ) : (
          <Explanation>Loading…</Explanation>
        )
      ) : !people.requests.length && !people.members.length ? (
        <Explanation>{PEOPLE.none}</Explanation>
      ) : (
        <>
          {people.requests.length > 0 && (
            <Section title={PEOPLE.requests}>
              {people.requests.map((request) => (
                <RequestRow
                  key={request.id}
                  request={request}
                  timezone={timezone}
                  busy={busy === request.id}
                  onDecide={(decision) =>
                    void act(request.id, async () =>
                      setPeople(
                        await workspaceApi<People>(
                          `people/${request.id}/${decision}`,
                          {},
                        ),
                      ),
                    )
                  }
                />
              ))}
              <Explanation>{PEOPLE.requestNote}</Explanation>
            </Section>
          )}
          {people.members.length > 0 && (
            <Section title={PEOPLE.members}>
              {people.members.map((person) => (
                <PersonRow
                  key={person.member}
                  person={person}
                  timezone={timezone}
                  busy={busy === person.member}
                  onChanged={load}
                  onRemove={() =>
                    void act(person.member, async () => {
                      if (
                        !window.confirm(
                          PEOPLE.removeConfirm(person.name || "this person"),
                        )
                      )
                        return;
                      const removed = await workspaceApi<Removed>(
                        `people/${person.member}/remove`,
                        { confirm: true },
                      );
                      if (!removed.confirmed) setNote(PEOPLE.unconfirmed);
                      await load();
                    })
                  }
                />
              ))}
              <Explanation>{PEOPLE.memberNote}</Explanation>
            </Section>
          )}
        </>
      )}
    </ScreenFrame>
  );
}

function RequestRow({
  request,
  timezone,
  busy,
  onDecide,
}: {
  request: Request;
  timezone: string;
  busy: boolean;
  onDecide: (decision: "approve" | "decline") => void;
}) {
  const name = request.name || "Someone";
  return (
    <div className="screen-block person" role="group" aria-label={name}>
      <strong>{name}</strong>
      <Explanation>
        {[personLine(request), askedAt(request.requested_at, timezone)]
          .filter(Boolean)
          .join(" · ")}
      </Explanation>
      <div className="person-actions">
        <SmallButton
          label="Decline"
          name={`Decline ${name}`}
          disabled={busy}
          onClick={() => onDecide("decline")}
        />
        <SmallButton
          label="Approve"
          name={`Approve ${name}`}
          primary
          disabled={busy}
          onClick={() => onDecide("approve")}
        />
      </div>
    </div>
  );
}

function PersonRow({
  person,
  timezone,
  busy,
  onChanged,
  onRemove,
}: {
  person: Person;
  timezone: string;
  busy: boolean;
  onChanged: () => Promise<void>;
  onRemove: () => void;
}) {
  const name = person.name || "Someone";
  const [editing, setEditing] = useState(false);
  return (
    <div className="screen-block person" role="group" aria-label={name}>
      <strong>{name}</strong>
      <Explanation>
        {[
          personLine(person),
          `${person.accounts} ${person.accounts === 1 ? "account" : "accounts"}`,
          lastActive(person.last_active, timezone),
        ]
          .filter(Boolean)
          .join(" · ")}
      </Explanation>
      <p className="budget-spent">
        {`${BUDGETS.daily.title}: ${dollars(person.budget.spent, 4)} of ${dollars(person.budget.limit)} today`}
      </p>
      <p className="budget-spent">
        {`${BUDGETS.group_briefing.title}: ${dollars(person.group_budget.spent, 4)} of ${dollars(person.group_budget.limit)} today`}
      </p>
      {editing ? (
        <BudgetEditor
          person={person}
          onDone={async () => {
            setEditing(false);
            await onChanged();
          }}
        />
      ) : (
        <div className="person-actions">
          <SmallButton
            label="Remove"
            name={`Remove ${name}`}
            disabled={busy}
            onClick={onRemove}
          />
          <SmallButton
            label="Edit budgets"
            name={`Edit ${name}'s budgets`}
            disabled={busy}
            onClick={() => setEditing(true)}
          />
        </div>
      )}
    </div>
  );
}

function BudgetEditor({
  person,
  onDone,
}: {
  person: Person;
  onDone: () => Promise<void>;
}) {
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
        await workspaceApi(`people/${person.member}/budget`, {
          family,
          limit_usd: limit,
        });
      await onDone();
    } catch (failure) {
      setProblem(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {(
        [
          ["daily", daily, setDaily],
          ["group_briefing", group, setGroup],
        ] as const
      ).map(([family, value, set]) => (
        <div className="budget-editor" key={family}>
          <span>{BUDGETS[family].title} $</span>
          <input
            aria-label={`${person.name || "Their"} ${BUDGETS[family].title} per day in dollars`}
            inputMode="decimal"
            value={value}
            disabled={busy}
            onChange={(event) => {
              set(event.target.value);
              setProblem("");
            }}
          />
        </div>
      ))}
      <div className="person-actions">
        <SmallButton
          label="Cancel"
          disabled={busy}
          onClick={() => void onDone()}
        />
        <SmallButton
          label="Save"
          primary
          disabled={busy}
          onClick={() => void save()}
        />
      </div>
      {problem && <Note>{problem}</Note>}
    </>
  );
}
