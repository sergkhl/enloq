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

// ADR 25's complete manual messages: a draft set and a bracketed set in each
// Style, plus one long draft, with automation off and no sends. Bounds: at
// most 20 model requests, 20 minutes and $0.05 counted (unknowns included).
// Docker reads route metadata and request counts, never sessions or content.
// A continuation after a provider failure keeps the original start in
// ENLOQ_MANUAL_ACCEPTANCE_SINCE (Unix seconds) and runs from set
// ENLOQ_MANUAL_ACCEPTANCE_FROM (1–7); it never resets a bound.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
const LIMIT = 50_000_000;
const REQUESTS = 20;
const SECONDS = 1200;
const SINCE = Number(process.env.ENLOQ_MANUAL_ACCEPTANCE_SINCE || 0);
const FROM = Number(process.env.ENLOQ_MANUAL_ACCEPTANCE_FROM || 1);
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
const DRAFT =
  "Enloq authored check: Friday doesn't work for me, maybe next week?";
const BRACKETED =
  "Enloq authored check: Friday doesn't work for me [suggest Saturday instead and ask what time suits]";
const LONG = [
  "Enloq authored long check: thank you for planning the weekend trip, it sounds lovely.",
  "I can't leave on Friday evening because of work, but I could take the first train on Saturday morning.",
  "If that's too late for the boat tour, go ahead without me and I'll meet you at the guesthouse in the afternoon.",
  "I'd also like to pay my share of the room now rather than later, so tell me how much it is.",
  "And if the weather turns bad, I'm happy to swap the hike for the museum and a long lunch instead.",
  "I'll bring the board games and the spare charger, since we forgot both last time.",
  "Let me know which option works best for everyone, and I'll book my ticket tonight.",
].join(" ");
const STYLES = ["friendly", "professional", "idioms"];
const SETS = [
  ...STYLES.flatMap((style) => [
    [style, "draft", DRAFT],
    [style, "bracketed", BRACKETED],
  ]),
  ["friendly", "long", LONG],
];

test("manual suggestions develop drafts and bracketed instructions in each Style, replacing with Undo", async ({
  page,
}) => {
  const began = SINCE || Date.now() / 1000;
  expect(Date.now() / 1000 - began).toBeLessThan(SECONDS - 60);
  test.setTimeout((SECONDS - (Date.now() / 1000 - began)) * 1000);
  const evidence: Record<string, any> = {
    began,
    from: FROM,
    routes: null,
    sets: [],
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
    "The authored check needs an empty Maya composer",
  ).toBe(true);
  expect(
    (await (await page.request.get(`${CHAT}/remember`)).json()).enabled,
  ).toBe(false);
  const settings = await (
    await page.request.get(`${CHAT}/suggestions/settings`)
  ).json();
  evidence.settings = settings;
  const budget = (await (await page.request.get("/api/settings")).json())
    .budget;
  expect(budget.reached).toBe(false);
  const field = page.getByRole("textbox", { name: "Message", exact: true });
  const button = page.getByRole("button", { name: "Suggestions", exact: true });
  const change = (path: string, data: unknown) =>
    page.request.post(CHAT + path, { data, headers: { Origin: ORIGIN } });
  async function bounds() {
    const counted = metadata();
    const now = (await (await page.request.get("/api/settings")).json()).budget;
    expect(counted.requests).toBeLessThanOrEqual(REQUESTS);
    expect(counted.counted).toBeLessThanOrEqual(LIMIT);
    expect(now.spent - budget.spent).toBeLessThanOrEqual(LIMIT);
    expect(now.reached).toBe(false);
    expect(Date.now() / 1000 - began).toBeLessThan(SECONDS - 60);
    return counted;
  }
  async function set(style: string, kind: string, draft: string) {
    await bounds();
    expect(metadata().requests).toBeLessThanOrEqual(REQUESTS - 2);
    await field.fill(draft);
    const before = await (await page.request.get(CHAT)).json();
    const previous = before.suggestions.source_time || 0;
    await expect(button).toBeEnabled();
    await button.tap();
    let shown: any;
    await expect
      .poll(
        async () => {
          shown = (await (await page.request.get(CHAT)).json()).suggestions;
          return (
            shown.kind === "manual" &&
            shown.source_time > previous &&
            ["ready", "failed"].includes(shown.state)
          );
        },
        { timeout: 420000, intervals: [2000] },
      )
      .toBe(true);
    evidence.sets.push({
      style,
      kind,
      state: shown.state,
      reason: shown.reason,
      attempt: shown.attempt,
      options: shown.options,
      lengths: (shown.options || []).map((option: any) => option.text.length),
    });
    expect(
      shown.state,
      `${style} ${kind} set must pass format and provider checks`,
    ).toBe("ready");
    expect(shown.style).toBe(style);
    expect(shown.options.length).toBeGreaterThan(0);
    for (const option of shown.options)
      expect(/\[[^[\]]*[^\s[\]][^[\]]*\]/.test(option.text)).toBe(false);
    // Generation never touches the draft; a choice replaces it, with Undo.
    await expect(field).toHaveValue(draft);
    await expect(button).toBeEnabled();
    await page
      .getByRole("region", { name: "Response suggestions" })
      .locator(".suggestion-list button")
      .first()
      .tap();
    await expect(field).toHaveValue(shown.options[0].text);
    await page.getByRole("button", { name: "Undo suggestion" }).tap();
    await expect(field).toHaveValue(draft);
    const after = await (await page.request.get(CHAT)).json();
    expect(after.operations.map((op: any) => op.id)).toEqual(
      before.operations.map((op: any) => op.id),
    );
    await field.fill("");
    await expect
      .poll(async () => (await (await page.request.get(CHAT)).json()).draft)
      .toMatchObject({ text: "" });
  }
  try {
    for (const [index, [style, kind, draft]] of SETS.entries()) {
      if (index + 1 < FROM) continue;
      expect(
        (
          await change("/suggestions/settings", {
            automatic: false,
            style,
          })
        ).status(),
      ).toBe(200);
      await set(style!, kind!, draft!);
    }
    expect(
      (
        await page.request.post(`${CHAT}/rewrite`, {
          data: { revision: 0 },
          headers: { Origin: ORIGIN },
        })
      ).status(),
    ).toBe(404);
    evidence.result = await bounds();
  } finally {
    const current = (await (await page.request.get(CHAT)).json()).draft;
    if (current.text)
      await change("/draft", {
        revision: current.revision,
        text: "",
        reply_to: current.reply_to,
      });
    await change("/suggestions/settings", settings);
    evidence.restored = await (
      await page.request.get(`${CHAT}/suggestions/settings`)
    ).json();
    evidence.actual = metadata();
    evidence.elapsed_seconds = Date.now() / 1000 - began;
    mkdirSync("../.local/live-evidence", { recursive: true, mode: 0o700 });
    const path = `../.local/live-evidence/manual-suggestions-${Date.now()}.json`;
    writeFileSync(path, JSON.stringify(evidence, null, 2) + "\n", {
      mode: 0o600,
    });
    chmodSync(path, 0o600);
  }
});
