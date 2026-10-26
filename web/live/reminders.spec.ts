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

// ADR 58 finite acceptance in the Owner Account's Maya chat: an exact-time
// reminder whose Message Idea names the day of this run's authored test
// messages, one without an idea, a Maya message for the refresh, a send from
// the set and a Dismiss. Bounds: at most 60 model requests, three authored
// sends, 45 minutes and $0.15 counted, under unchanged member budgets. Docker
// reads route metadata, request counts and the set's shape, never Telegram
// sessions or content.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
// The same chat from Maya's side, where her sends are outgoing.
const MAYA_CHAT = `/api/accounts/${MAYA}/chats/user:${OWNER_ACCOUNT}`;
const REMINDERS = `${CHAT}/reminders`;
const LIMIT = 150_000_000;
const REQUESTS = 60;
const SECONDS = 2700;
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
// The set's shape only: how many reminders, answered and reference
// messages and kept days it carries, never their text.
const SHAPE = String.raw`
import json,sqlite3,sys
from enloq.config import Settings
s=Settings.load('personal'); q=json.load(sys.stdin)
if q['owner']!=s.owner_account: raise RuntimeError('Wrong live account')
from enloq.storage.accounts import AccountRegistry
r=AccountRegistry(s.data); path=r.path_for(r.get(q['owner'])['directory'])/'rewriting.sqlite3'
db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
row=db.execute("SELECT body FROM suggestion_state WHERE chat_key=?",(f"{q['owner']}/user:{q['maya']}",)).fetchone()
body=json.loads(row[0]) if row else {}
packet=body.get('packet') or {}
print(json.dumps({'reminders':len(body.get('reminders') or []),'trigger':body.get('trigger'),'state':body.get('state'),'answered':len(packet.get('messages') or []),'reference':len(packet.get('context') or []),'kept':[len(d.get('messages') or []) for d in packet.get('kept') or []]}))
`;
// Zones whose local midnight may fall inside the run, so the idea's day is
// kept while it lasts (ADR 58 Q11).
const ZONES = [
  "Pacific/Kiritimati",
  "Pacific/Tongatapu",
  "Pacific/Chatham",
  "Pacific/Auckland",
  "Pacific/Noumea",
  "Australia/Lord_Howe",
  "Australia/Sydney",
  "Australia/Adelaide",
  "Asia/Tokyo",
  "Australia/Eucla",
  "Asia/Shanghai",
  "Asia/Bangkok",
  "Asia/Yangon",
  "Asia/Dhaka",
  "Asia/Kathmandu",
  "Asia/Kolkata",
  "Asia/Karachi",
  "Asia/Kabul",
  "Asia/Dubai",
  "Asia/Tehran",
  "Europe/Moscow",
  "Europe/Athens",
  "Europe/Berlin",
  "Europe/London",
  "Atlantic/Azores",
  "Atlantic/South_Georgia",
  "America/Sao_Paulo",
  "America/St_Johns",
  "America/Halifax",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Marquesas",
  "Pacific/Honolulu",
  "Pacific/Pago_Pago",
  "Etc/GMT+12",
];

function local(zone: string, at: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const get = (type: string) =>
    Number(parts.find((part) => part.type === type)!.value);
  return {
    day: `${get("year")}-${String(get("month")).padStart(2, "0")}-${String(get("day")).padStart(2, "0")}`,
    seconds: get("hour") * 3600 + get("minute") * 60 + get("second"),
    clock: `${String(get("hour")).padStart(2, "0")}:${String(get("minute")).padStart(2, "0")}`,
  };
}

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

test("message reminders through the live service: early sets, refresh, a send from the set and Dismiss", async ({
  page,
}) => {
  test.setTimeout(SECONDS * 1000);
  const began = Date.now() / 1000;
  const evidence: Record<string, any> = { began, sets: [], sends: 0 };
  const metadata = (script = READ) =>
    JSON.parse(
      execFileSync("docker", ["exec", "-i", "enloq", "python", "-c", script], {
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
  // The zone whose local midnight comes soonest, at least six minutes away.
  const now = new Date();
  const choice = ZONES.map((zone) => ({
    zone,
    until: 86400 - local(zone, now).seconds,
  }))
    .filter((item) => item.until >= 360)
    .sort((a, b) => a.until - b.until)[0]!;
  const zone = choice.zone;
  const keepsToday = choice.until <= 22 * 60;
  const day = local(zone, now).day;
  const [, month, date] = day.split("-").map(Number);
  evidence.zone = { zone, minutes_to_midnight: choice.until / 60, keepsToday };

  await openMayaChat(page, "owner");
  const initial = await (await page.request.get(CHAT)).json();
  expect(
    !initial.draft.text &&
      !initial.draft.attachments.length &&
      !initial.draft.selected_media &&
      !initial.draft.reply_to,
    "The authored check needs an empty Maya composer",
  ).toBe(true);
  expect(
    (await (await page.request.get(`${CHAT}/remember`)).json()).enabled,
  ).toBe(false);
  const settings = await (
    await page.request.get(`${CHAT}/suggestions/settings`)
  ).json();
  evidence.settings = settings;
  const existing = (await (await page.request.get(REMINDERS)).json()).items;
  expect(existing, "Live acceptance preserves any existing schedule").toEqual(
    [],
  );
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
  const change = (path: string, data: unknown) =>
    page.request.post(CHAT + path, { data, headers: { Origin: ORIGIN } });
  async function bounds() {
    const counted = metadata();
    const budget = (await (await page.request.get("/api/settings")).json())
      .budget;
    expect(counted.requests).toBeLessThanOrEqual(REQUESTS);
    expect(counted.counted).toBeLessThanOrEqual(LIMIT);
    expect(budget.spent - startedSpend).toBeLessThanOrEqual(LIMIT);
    expect(budget.reached).toBe(false);
    expect(evidence.sends).toBeLessThanOrEqual(3);
    expect(Date.now() / 1000 - began).toBeLessThan(SECONDS - 120);
    return counted;
  }
  async function send(text: string, side: "owner" | "maya" = "owner") {
    await bounds();
    expect(evidence.sends).toBeLessThan(3);
    evidence.sends++;
    await page.getByRole("button", { name: "Send", exact: true }).tap();
    await expect(field).toHaveValue("");
    const sender = side === "maya" ? MAYA_CHAT : CHAT;
    await expect
      .poll(
        async () =>
          (await (await page.request.get(sender)).json()).messages.some(
            (message: any) =>
              message.outgoing &&
              message.message_id > 0 &&
              message.text === text,
          ),
        { timeout: 60000, intervals: [1000] },
      )
      .toBe(true);
  }
  async function ready(previous: number) {
    let value: any;
    await expect
      .poll(
        async () => {
          await bounds();
          value = (await (await page.request.get(CHAT)).json()).suggestions;
          return (
            value.kind === "manual" &&
            value.source_time > previous &&
            ["ready", "failed"].includes(value.state)
          );
        },
        { timeout: 600000, intervals: [3000] },
      )
      .toBe(true);
    const shape = metadata(SHAPE);
    evidence.sets.push({
      ...shape,
      state: value.state,
      options: value.options,
    });
    expect(
      value.state,
      "Format/provider reliability is separate from semantic quality",
    ).toBe("ready");
    return { view: value, shape };
  }
  const exact = (minutes: number) => {
    const at = new Date(Date.now() + minutes * 60000);
    const value = local(zone, at);
    return { start: value.day, clock: value.clock };
  };
  const ideaDay = `${date} ${MONTHS[month! - 1]}`;
  try {
    expect(
      (
        await change("/suggestions/settings", {
          automatic: true,
          style: "friendly",
        })
      ).status(),
    ).toBe(200);
    // 1. The day's authored test message.
    const first = `[Enloq authored reminder check ${began.toFixed(0)}] A test message for the day.`;
    await field.fill(first);
    await send(first);
    // 2. An exact reminder whose idea names that day, and a check-in, due
    // after the day ends when it ends inside the run.
    const lead = keepsToday ? choice.until / 60 + 3 : 4;
    const due = exact(lead);
    const idea = `Ask Maya how the ${ideaDay} Enloq test went [keep it short]`;
    const withIdea = await change("/reminders", {
      repeat: "once",
      start: due.start,
      timezone: zone,
      delivery: "exact",
      time: due.clock,
      language: "English",
      idea,
    });
    expect(withIdea.status(), await withIdea.text()).toBe(200);
    const reminder = await withIdea.json();
    evidence.kept_on_save = reminder.kept;
    expect(reminder.kept.day).toBe(day);
    const checkIn = exact(lead + 1);
    const plain = await change("/reminders", {
      repeat: "once",
      start: checkIn.start,
      timezone: zone,
      delivery: "exact",
      time: checkIn.clock,
      language: "English",
      idea: "",
    });
    expect(plain.status(), await plain.text()).toBe(200);
    const check = await plain.json();
    // Listing makes no model call.
    const listed = metadata().requests;
    await page.goto("/?tab=reminders");
    await expect(
      page.getByRole("button", { name: /^Daily digest · / }),
    ).toBeVisible();
    expect(metadata().requests).toBe(listed);
    // 3. The idea's day ends in its zone and is kept.
    if (keepsToday) {
      await expect
        .poll(
          async () =>
            (
              await (
                await page.request.get(`${REMINDERS}/${reminder.id}`)
              ).json()
            ).kept,
          { timeout: (choice.until + 120) * 1000, intervals: [5000] },
        )
        .toMatchObject({ day, state: "kept" });
      evidence.kept = (
        await (await page.request.get(`${REMINDERS}/${reminder.id}`)).json()
      ).kept;
      expect(evidence.kept.count).toBeGreaterThan(0);
    }
    // 4. Two minutes before each alert, the chat's set is prepared; the
    // second replaces the first, which may still be running.
    let previous = initial.suggestions?.source_time || 0;
    let shown = await ready(previous);
    if (shown.shape.reminders < 2) {
      previous = shown.view.source_time;
      shown = await ready(previous);
    }
    expect(shown.shape.reminders).toBe(2);
    expect(shown.shape.trigger).toBe("prepare");
    // Prepared before the first alert, so the alert never waits for it.
    expect(shown.view.source_time).toBeLessThan(reminder.next_at);
    evidence.lead_seconds = reminder.next_at - shown.view.source_time;
    if (keepsToday) expect(shown.shape.kept.length).toBe(1);
    previous = shown.view.source_time;
    // Both fall due; the bar lists them.
    await expect
      .poll(
        async () =>
          (await (await page.request.get(CHAT)).json()).reminders.length,
        { timeout: 330000, intervals: [5000] },
      )
      .toBe(2);
    // 5. Maya writes; 30 quiet seconds later the set is refreshed in the
    // background, with no client opening the chat meanwhile.
    await openMayaChat(page, "maya");
    const mayas = `[Enloq authored reminder check ${began.toFixed(0)}] Maya's reply: are you free on Friday?`;
    await field.fill(mayas);
    await send(mayas, "maya");
    await page.goto("about:blank");
    shown = await ready(previous);
    expect(shown.shape.trigger).toBe("refresh");
    expect(shown.shape.answered).toBeGreaterThan(0);
    previous = shown.view.source_time;
    await openMayaChat(page, "owner");
    const bar = page.getByRole("region", { name: "Reminders" });
    await expect(
      bar.getByRole("button", { name: /^Edit reminder: / }),
    ).toHaveCount(2);
    // 6. Dismiss the check-in; opening prepares the set again for the idea.
    await bar
      .getByRole("button", { name: "Dismiss reminder: Check-in reminder" })
      .tap();
    await expect(
      bar.getByRole("button", { name: /^Edit reminder: / }),
    ).toHaveCount(1);
    await page.reload();
    shown = await ready(previous);
    expect(shown.shape.reminders).toBe(1);
    // 7. A send from the set completes the reminder with an idea, and the
    // Once schedule ends with its Kept Messages.
    const option = page
      .getByRole("region", { name: "Response suggestions" })
      .locator(".suggestion-list button")
      .first();
    await option.tap();
    const chosen = shown.view.options[0].text;
    await expect(field).toHaveValue(chosen);
    await send(chosen);
    await expect
      .poll(
        async () => (await (await page.request.get(REMINDERS)).json()).items,
        { timeout: 60000, intervals: [2000] },
      )
      .toEqual([]);
    evidence.check_in = check.id;
    evidence.result = await bounds();
  } finally {
    const draft = (await (await page.request.get(CHAT)).json()).draft;
    if (draft.text)
      await change("/draft", {
        revision: draft.revision,
        text: "",
        reply_to: draft.reply_to,
      });
    for (const item of (await (await page.request.get(REMINDERS)).json()).items)
      await page.request.delete(`${REMINDERS}/${item.id}`, {
        data: { revision: item.revision },
        headers: { Origin: ORIGIN },
      });
    await change("/suggestions/settings", settings);
    evidence.restored = await (
      await page.request.get(`${CHAT}/suggestions/settings`)
    ).json();
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
