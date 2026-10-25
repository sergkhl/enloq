import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import {
  expect,
  MAYA,
  openMayaChat,
  ORIGIN,
  OWNER_ACCOUNT,
  test,
} from "./maya";

// ADR 58 finite acceptance: <=60 model requests, two authored native sends,
// 30 minutes and $0.10 counted, under unchanged member budgets. Existing
// service owner only; Docker reads metadata, never Telegram sessions/content.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
const LIMIT = 100_000_000;
const VERIFY = String.raw`
import asyncio,json
from enloq.config import Settings
from enloq.storage.accounts import AccountRegistry
from enloq.storage.members import OWNER
from enloq.inference.openrouter import OpenRouterEngine,MuseEngine,within_adopted_rates,MUSE_ADOPTED_RATES
from enloq.inference.decider import DeciderEngine,CHECKPOINT
s=Settings.load('personal'); r=AccountRegistry(s.data); user=r.provider_user(OWNER)
async def main():
 result=[]
 for cls in (OpenRouterEngine,MuseEngine,DeciderEngine):
  engine=cls(s,user=user)
  try:
   if cls is DeciderEngine:
    await engine.prepare({}); rates=engine.rates
   else:
    rates=await engine.verify()
    assert within_adopted_rates(rates,MUSE_ADOPTED_RATES) if cls is MuseEngine else within_adopted_rates(rates)
   result.append({'model':engine.model,'provider':engine.provider,'prompt':rates['prompt'],'completion':rates['completion']})
  finally: await engine.client.aclose()
 return {'routes':result,'checkpoint':CHECKPOINT}
print(json.dumps(asyncio.run(main())))
`;
const READ = String.raw`
import json,sqlite3,sys
from enloq.config import Settings
s=Settings.load('personal'); q=json.load(sys.stdin)
if q['owner']!=s.owner_account: raise RuntimeError('Wrong live account')
db=sqlite3.connect((s.data/'accounts.sqlite3').as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
key=f"{q['owner']}/user:{q['maya']}"
rows=db.execute("SELECT r.role,r.status,r.actual FROM model_reservations r WHERE r.account_id=? AND r.created>=? AND (r.scope=? OR r.scope LIKE ?)",(q['owner'],q['since'],key,key+'/%')).fetchall()
print(json.dumps({'requests':len(rows),'counted':sum(r['actual'] if r['actual'] is not None else 10000000 for r in rows)}))
`;

test("recurring reminder review and confirmed manual sends through the live service", async ({
  page,
}) => {
  const continuation = process.env.ENLOQ_REMINDER_ACCEPTANCE_CONTINUE === "1";
  const began = Number(
    process.env.ENLOQ_REMINDER_ACCEPTANCE_SINCE || Date.now() / 1000,
  );
  const priorSends = Number(process.env.ENLOQ_REMINDER_ACCEPTANCE_SENDS || 0);
  expect(
    Number.isFinite(began) &&
      (continuation ? priorSends === 1 : priorSends === 0),
  ).toBe(true);
  test.setTimeout(Math.max(1, 1800 - (Date.now() / 1000 - began)) * 1000);
  const evidence: Record<string, any> = {
    began,
    continuation,
    routes: null,
    sets: [],
    sends: priorSends,
  };
  const metadata = () =>
    JSON.parse(
      execFileSync("docker", ["exec", "-i", "enloq", "python", "-c", READ], {
        input: JSON.stringify({
          owner: OWNER_ACCOUNT,
          maya: MAYA,
          since: began,
        }),
        timeout: 15000,
      }).toString(),
    );
  evidence.routes = JSON.parse(
    execFileSync("docker", ["exec", "-i", "enloq", "python", "-c", VERIFY], {
      timeout: 90000,
    }).toString(),
  );
  await openMayaChat(page, "owner");
  const initial = await (await page.request.get(CHAT)).json();
  expect(
    !initial.draft.text &&
      !initial.draft.attachments.length &&
      !initial.draft.selected_media &&
      !initial.draft.reply_to,
  ).toBe(true);
  expect(
    (await (await page.request.get(`${CHAT}/remember`)).json()).enabled,
  ).toBe(false);
  const settings = await (
    await page.request.get(`${CHAT}/suggestions/settings`)
  ).json();
  const schedule = await (await page.request.get(`${CHAT}/reminder`)).json();
  expect(
    schedule.schedule,
    "Live acceptance preserves any existing user schedule",
  ).toBeNull();
  const global = await (await page.request.get("/api/settings")).json();
  expect(global.suggestions && !global.budget.reached).toBe(true);
  const accounts = (await (await page.request.get("/api/accounts")).json())
    .accounts;
  expect(
    accounts.find((account: any) => account.account_id === OWNER_ACCOUNT)
      ?.ai_enabled,
  ).toBe(true);
  expect(
    accounts.find((account: any) => account.account_id === MAYA)?.ai_enabled,
  ).toBe(false);
  const startedSpend = global.budget.spent;
  const field = page.getByRole("textbox", { name: "Message", exact: true });
  const seed = "[Enloq authored reminder check] I'd like to check in. ";
  const change = (path: string, data: unknown) =>
    page.request.post(CHAT + path, { data, headers: { Origin: ORIGIN } });
  async function bounds() {
    const counted = metadata();
    const budget = (await (await page.request.get("/api/settings")).json())
      .budget;
    expect(counted.requests).toBeLessThanOrEqual(60);
    expect(counted.counted).toBeLessThanOrEqual(LIMIT);
    expect(budget.spent - startedSpend).toBeLessThanOrEqual(LIMIT);
    expect(budget.reached).toBe(false);
    expect(Date.now() / 1000 - began).toBeLessThan(1740);
    return counted;
  }
  async function createDue() {
    await bounds();
    const current = await (await page.request.get(`${CHAT}/reminder`)).json();
    const target = new Date(Date.now() + 90_000);
    const answer = await change("/reminder", {
      revision: current.revision,
      frequency: "weekly",
      weekday: (target.getUTCDay() + 6) % 7,
      month_day: 1,
      timezone: "UTC",
      delivery: "exact",
      time: target.toISOString().slice(11, 16),
      language: "English",
    });
    expect(answer.status()).toBe(200);
    const saved = await answer.json();
    expect(saved.next_at - Date.now() / 1000).toBeLessThanOrEqual(95);
    let occurrence: any;
    await expect
      .poll(
        async () => {
          occurrence = (
            await (await page.request.get(`${CHAT}/reminder`)).json()
          ).occurrence;
          return !!occurrence;
        },
        { timeout: 105000, intervals: [1000] },
      )
      .toBe(true);
    return occurrence.id as string;
  }
  async function ready(previous: number) {
    let value: any;
    await expect
      .poll(
        async () => {
          await bounds();
          value = (await (await page.request.get(CHAT)).json()).suggestions;
          return (
            value.source_time > previous &&
            ["ready", "failed"].includes(value.state)
          );
        },
        { timeout: 600000, intervals: [2000] },
      )
      .toBe(true);
    expect(
      value.state,
      "Format/provider reliability is separate from semantic quality",
    ).toBe("ready");
    evidence.sets.push({
      style: value.style,
      options: value.options,
      attempt: value.attempt,
    });
    return value;
  }
  async function authoredSend(index: number) {
    await bounds();
    expect(evidence.sends).toBeLessThan(2);
    await field.fill(
      `[Enloq authored reminder acceptance ${index}] Manual delivery check.`,
    );
    evidence.sends++;
    await page.getByRole("button", { name: "Send", exact: true }).tap();
    await expect(field).toHaveValue("");
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${CHAT}/reminder`)).json())
            .occurrence,
        { timeout: 30000, intervals: [1000] },
      )
      .toBeNull();
    // Confirmed operations leave the pending-operation projection as soon
    // as their native messages are stored. Assert on that native evidence.
    await expect
      .poll(
        async () => {
          const state = await (await page.request.get(CHAT)).json();
          return state.messages.some(
            (message: any) =>
              message.outgoing &&
              message.message_id > 0 &&
              message.text ===
                `[Enloq authored reminder acceptance ${index}] Manual delivery check.`,
          );
        },
        { timeout: 30000, intervals: [1000] },
      )
      .toBe(true);
  }
  try {
    expect(
      (
        await change("/suggestions/settings", {
          automatic: true,
          style: "friendly",
        })
      ).status(),
    ).toBe(200);
    const occurrence = await createDue();
    const beforeList = metadata();
    await page.goto("/?tab=reminders");
    await expect(
      page.getByRole("heading", { name: "Reminders", exact: true }),
    ).toBeVisible();
    expect(metadata().requests).toBe(beforeList.requests);
    const reviewUrl = `/?account=${OWNER_ACCOUNT}&reminder=${occurrence}#${MAYA}`;
    const previous = initial.suggestions?.source_time || 0;
    await page.goto(reviewUrl);
    let shown = await ready(previous);
    await expect(field).toHaveValue("");
    const reused = metadata().requests;
    await page.reload();
    await expect(field).toBeVisible();
    expect(metadata().requests).toBe(reused);
    if (continuation) {
      await authoredSend(2);
      evidence.result = await bounds();
      return;
    }
    for (const style of ["professional", "idioms"]) {
      await bounds();
      expect(Date.now() / 1000 - began).toBeLessThan(1200);
      expect(metadata().requests).toBeLessThan(50);
      expect(
        (
          await change("/suggestions/settings", { automatic: true, style })
        ).status(),
      ).toBe(200);
      await field.fill(seed);
      const previous = shown.source_time;
      await page
        .getByRole("button", { name: "Generate suggestions", exact: true })
        .tap();
      shown = await ready(previous);
      await expect(field).toHaveValue(seed);
      if (shown.options.length) {
        await page.locator(".suggestion-list button").first().tap();
        expect((await field.inputValue()).startsWith(seed.trimEnd())).toBe(
          true,
        );
        await page
          .getByRole("button", { name: "Undo suggestion", exact: true })
          .tap();
        await expect(field).toHaveValue(seed);
      }
    }
    await authoredSend(1);
    await createDue();
    await authoredSend(2);
    evidence.result = await bounds();
  } finally {
    const draft = (await (await page.request.get(CHAT)).json()).draft;
    if (
      draft.text === seed ||
      draft.text.startsWith("[Enloq authored reminder acceptance")
    ) {
      await change("/draft", {
        revision: draft.revision,
        text: "",
        reply_to: draft.reply_to,
      });
    }
    const current = await (await page.request.get(`${CHAT}/reminder`)).json();
    if (current.schedule)
      expect(
        (
          await page.request.delete(`${CHAT}/reminder`, {
            data: { revision: current.revision },
            headers: { Origin: ORIGIN },
          })
        ).status(),
      ).toBe(200);
    await change("/suggestions/settings", settings);
    evidence.actual = metadata();
    evidence.elapsed_seconds = Date.now() / 1000 - began;
    mkdirSync("../.local/live-evidence", { recursive: true, mode: 0o700 });
    const path = `../.local/live-evidence/reminders-${Date.now()}.json`;
    writeFileSync(path, JSON.stringify(evidence, null, 2) + "\n", {
      mode: 0o600,
    });
    chmodSync(path, 0o600);
  }
});
