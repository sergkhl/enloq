import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import {
  chooseView,
  expect,
  MAYA,
  openAiMenu,
  openMayaChat,
  ORIGIN,
  OWNER_ACCOUNT,
  readView,
  test,
} from "./maya";

// ADR 62 Phase 3 on the live service, in the Owner Account's side of the Maya
// chat (AGENTS.md, Live Telegram sends). With every switch Off there, Maya
// sends a plain text, the Owner Account answers so that each is its own block,
// and Maya sends a captioned photo: neither asks a model on the Owner
// Account's side, both show their combined originals with no status, and the
// list shows the unseen dot and their original preview after quiet. Emotion
// turned On then decides the latest block from its caption alone, asking
// emotion only, and Voice and images turned On rewrites it. The chat's
// switches, Style, the Reading View and both drafts are read first; the
// switches, Style and view are put back at the end, even when the run fails.
// Bounds: three authored sends, 30 minutes, and at most 20 model requests and
// $0.05 counted on both sides of the Maya chat since ENLOQ_SWITCHES_SINCE
// (Unix seconds; the rollout's start, which its requests count against).
// Docker reads route metadata, request counts and decision questions, never
// sessions or content.
const SIDES = {
  owner: `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`,
  maya: `/api/accounts/${MAYA}/chats/user:${OWNER_ACCOUNT}`,
};
const LIMIT = 50_000_000;
const REQUESTS = 20;
const SECONDS = 1800;
const SINCE = Number(process.env.ENLOQ_SWITCHES_SINCE || 0);
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
// Requests and counted spending of each side of the Maya chat since `since`.
const COUNT = String.raw`
import json,sqlite3,sys
from enloq.config import Settings
s=Settings.load('personal'); q=json.load(sys.stdin)
if q['owner']!=s.owner_account: raise RuntimeError('Wrong live account')
db=sqlite3.connect((s.data/'accounts.sqlite3').as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
result={}
for side,account,peer in (('owner',q['owner'],q['maya']),('maya',q['maya'],q['owner'])):
 key=f"{account}/user:{peer}"
 rows=db.execute("SELECT role,actual FROM model_reservations WHERE account_id=? AND created>=? AND (scope=? OR scope LIKE ?)",(account,q['since'],key,key+'/%')).fetchall()
 result[side]={'requests':len(rows),'roles':sorted(r['role'] for r in rows),'counted':sum(r['actual'] if r['actual'] is not None else 10000000 for r in rows)}
print(json.dumps(result))
`;
// The Owner Account's latest decision for the Maya chat: the questions it
// asked, its state and sources, and whether the photo has an image meaning.
const DECISION = String.raw`
import json,sqlite3,sys
from enloq.config import Settings
s=Settings.load('personal'); q=json.load(sys.stdin)
if q['owner']!=s.owner_account: raise RuntimeError('Wrong live account')
def connect(path):
 db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row; return db
r=connect(s.data/'accounts.sqlite3')
entry=r.execute('SELECT directory FROM accounts WHERE account_id=?',(q['owner'],)).fetchone()
path=(s.data/entry['directory']/'rewriting.sqlite3').resolve(); path.relative_to(s.data.resolve())
db=connect(path); key=f"{q['owner']}/user:{q['maya']}"
row=db.execute('SELECT body FROM emotion_state WHERE chat_key=?',(key,)).fetchone()
body=json.loads(row['body']) if row else {}
meaning=db.execute("SELECT 1 FROM derivatives WHERE source=? AND json_extract(body,'$.image_context') IS NOT NULL",(f"{key}/{q['photo']}",)).fetchone()
print(json.dumps({'asks':body.get('asks'),'state':body.get('state'),'sources':body.get('sources'),'image_inputs':len(body.get('image_inputs') or {}),'meaning':bool(meaning)}))
`;

type ChatAi = {
  suggestions: boolean;
  style: string;
  emotion: boolean;
  calming: boolean;
  media: boolean;
  remember: boolean;
};
type Block = {
  id: string;
  sources: number[];
  state: string;
  stage: string | null;
  deciding?: boolean;
};
type Snapshot = {
  ai: ChatAi;
  draft: { text: string; attachments: unknown[]; reply_to: unknown };
  blocks: Block[];
  messages: { message_id: number; text: string; outgoing: boolean }[];
};

let stamp = "";
const MARKS = {
  plain: () => `Enloq live check ADR 62, ${stamp}`,
  photo: () => `Enloq live check ADR 62 photo, ${stamp}`,
};

async function snapshot(page: Page, side: keyof typeof SIDES = "owner") {
  const answer = await page.request.get(SIDES[side]);
  expect(answer.status()).toBe(200);
  return (await answer.json()) as Snapshot;
}

// Each switch through its own route, as the AI Menu writes it.
async function setSwitches(page: Page, ai: ChatAi) {
  const post = async (path: string, data: object) => {
    const answer = await page.request.post(SIDES.owner + path, {
      data,
      headers: { Origin: ORIGIN },
    });
    expect(answer.status(), path).toBe(200);
  };
  await post("/suggestions/settings", {
    automatic: ai.suggestions,
    style: ai.style,
  });
  await post("/emotion", { enabled: ai.emotion });
  await post("/calming", { enabled: ai.calming });
  await post("/voice-and-images", { enabled: ai.media });
  await post("/remember", { enabled: ai.remember });
}

async function send(page: Page, text: string) {
  const field = page.getByRole("textbox", { name: "Message" });
  await field.fill(text);
  await page.getByRole("button", { name: "Send" }).tap();
  await expect(field).toHaveValue("");
}

// A small PNG drawn in the page, so the test image says what it is.
async function testImage(page: Page, label: string) {
  const url = await page.evaluate((label) => {
    const canvas = document.createElement("canvas");
    canvas.width = 480;
    canvas.height = 160;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#e8f0e2";
    context.fillRect(0, 0, 480, 160);
    context.fillStyle = "#1d2433";
    context.font = "24px sans-serif";
    context.fillText(label, 20, 90);
    return canvas.toDataURL("image/png");
  }, label);
  return Buffer.from(url.split(",")[1], "base64");
}

// The Owner Account's block of Maya's message carrying `mark`, once `done`
// holds for it.
async function settled(
  page: Page,
  mark: string,
  done: (block: Block) => boolean,
  timeout: number,
) {
  let found: Block | undefined;
  await expect
    .poll(
      async () => {
        const value = await snapshot(page);
        const message = value.messages.find(
          (m) => !m.outgoing && m.text.includes(mark),
        );
        found =
          message &&
          value.blocks.find((b) => b.sources.includes(message.message_id));
        return !!found && done(found);
      },
      { timeout, intervals: [5000] },
    )
    .toBe(true);
  return found!;
}

function bubble(page: Page, block: Block) {
  return page.locator(
    `article.app-bubble.incoming[data-source-ids="${block.sources.join(" ")}"]`,
  );
}

const original = (b: Block) =>
  b.state === "original" && !b.deciding && !b.stage;

test("with every switch Off the Maya chat shows originals until Emotion and Voice and images turn On", async ({
  page,
}) => {
  const began = Date.now() / 1000;
  const since = SINCE || began;
  expect(began - since).toBeLessThan(SECONDS - 600);
  test.setTimeout((SECONDS - (began - since)) * 1000);
  stamp = new Date().toISOString();
  const evidence: Record<string, any> = { began, since, sends: 0 };
  const docker = (script: string, input: object = {}) =>
    JSON.parse(
      execFileSync("docker", ["exec", "-i", "enloq", "python", "-c", script], {
        input: JSON.stringify({ owner: OWNER_ACCOUNT, maya: MAYA, ...input }),
        timeout: 90000,
      }).toString(),
    );
  evidence.routes = docker(VERIFY);
  const view = await readView(page);
  const before = {
    owner: await snapshot(page),
    maya: await snapshot(page, "maya"),
  };
  for (const side of ["owner", "maya"] as const)
    expect(
      !before[side].draft.text &&
        !before[side].draft.attachments.length &&
        !before[side].draft.reply_to,
      `The authored check needs an empty ${side} composer`,
    ).toBe(true);
  evidence.before = { view, owner: before.owner.ai, maya: before.maya.ai };
  console.log(`Before: ${JSON.stringify(evidence.before)}`);
  const bounds = () => {
    const counted = docker(COUNT, { since });
    const requests = counted.owner.requests + counted.maya.requests;
    expect(requests).toBeLessThanOrEqual(REQUESTS);
    expect(counted.owner.counted + counted.maya.counted).toBeLessThanOrEqual(
      LIMIT,
    );
    expect(Date.now() / 1000 - since).toBeLessThan(SECONDS);
    return counted;
  };
  bounds();
  try {
    await setSwitches(page, {
      ...before.owner.ai,
      suggestions: false,
      emotion: false,
      calming: false,
      media: false,
      remember: false,
    });
    const sent = Date.now() / 1000;
    await openMayaChat(page, "maya");
    await send(
      page,
      `The bakery on the corner opens at eight tomorrow. (${MARKS.plain()})`,
    );
    evidence.sends++;
    await openMayaChat(page, "owner");
    const reply = `Good to know. (Enloq live check, ${stamp})`;
    await send(page, reply);
    evidence.sends++;
    await expect(
      page
        .locator("article.app-bubble.outgoing")
        .filter({ hasText: reply })
        .getByLabel("Sent"),
    ).toBeVisible({ timeout: 30000 });
    await openMayaChat(page, "maya");
    await page.getByLabel("Attach images").setInputFiles({
      name: "live-check.png",
      mimeType: "image/png",
      buffer: await testImage(page, "Enloq live check ADR 62"),
    });
    await expect(
      page.getByRole("button", { name: "Remove image 1" }),
    ).toBeVisible({ timeout: 30000 });
    await send(page, `A cake for Sunday, I think. (${MARKS.photo()})`);
    evidence.sends++;
    await expect(
      page.getByRole("button", { name: "Remove image 1" }),
    ).toBeHidden({ timeout: 90000 });
    // The waits that follow read the API: nothing views the chat before the
    // list shows its unseen dot.
    await page.goto("about:blank");
    const plain = await settled(page, MARKS.plain(), original, 240000);
    const photo = await settled(page, MARKS.photo(), original, 240000);
    // Past their quiet seconds no model was asked on the owner's side.
    await page.waitForTimeout(35000);
    expect(docker(COUNT, { since: sent }).owner.requests).toBe(0);
    expect(
      original((await snapshot(page)).blocks.find((b) => b.id === photo.id)!),
    ).toBe(true);

    // The list: the unseen dot and the original preview, read before opening.
    await page.goto(`/?account=${OWNER_ACCOUNT}`);
    const row = page.getByRole("button", { name: /^Maya.*, new messages/ });
    await expect(row).toBeVisible({ timeout: 30000 });
    await expect(row.locator(".row-preview")).toContainText(MARKS.photo());

    // The chat: combined originals with no status, and the robot's outline.
    await openMayaChat(page, "owner");
    if (view !== "ai") await chooseView(page, "ai");
    const head = (block: Block) =>
      bubble(page, block).locator(".bubble-ai-head");
    await expect(head(plain)).toHaveText(/^1 message · /);
    await expect(head(photo)).toHaveText(/^1 message · /);
    for (const block of [plain, photo])
      await expect(bubble(page, block).locator(".bubble-status")).toHaveCount(
        0,
      );
    await expect(
      page.getByRole("button", { name: "AI Menu, AI off in this chat" }),
    ).toBeVisible();

    // Emotion On decides the latest block from its caption alone (Q8, Q20).
    let menu = await openAiMenu(page);
    await menu.getByRole("switch", { name: "Emotion" }).tap();
    await expect(menu.getByRole("switch", { name: "Emotion" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await page.keyboard.press("Escape");
    let decision: any;
    await expect
      .poll(
        () => {
          decision = docker(DECISION, { photo: photo.sources[0] });
          return (
            decision.state === "ready" &&
            JSON.stringify(decision.sources) === JSON.stringify(photo.sources)
          );
        },
        { timeout: 240000, intervals: [5000] },
      )
      .toBe(true);
    evidence.emotion = decision;
    expect(decision.asks).toEqual(["emotion"]);
    expect(decision.sources).toEqual(photo.sources);
    expect([decision.image_inputs, decision.meaning]).toEqual([0, false]);
    expect(docker(COUNT, { since: sent }).owner.roles).toEqual([
      "emotion_classification",
    ]);

    // Voice and images On rewrites it; the earlier block stays as it is.
    menu = await openAiMenu(page);
    await menu.getByRole("switch", { name: "Voice and images" }).tap();
    await expect(
      menu.getByRole("switch", { name: "Voice and images" }),
    ).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Escape");
    await page.goto("about:blank");
    await settled(page, MARKS.photo(), (b) => b.state === "ready", 420000);
    expect(
      (await snapshot(page)).blocks.find((b) => b.id === plain.id)?.state,
    ).toBe("original");
    expect(docker(DECISION, { photo: photo.sources[0] }).meaning).toBe(true);
    await openMayaChat(page, "owner");
    await expect(head(photo)).toHaveText(/^AI · 1 message · /);
    await expect(head(plain)).toHaveText(/^1 message · /);
    await page.screenshot({ path: "../.local/live-results/switches.png" });
    evidence.result = bounds();
  } finally {
    await setSwitches(page, before.owner.ai);
    const restored = await snapshot(page);
    evidence.restored = restored.ai;
    if ((await readView(page)) !== view) {
      await openMayaChat(page, "owner");
      await chooseView(page, view);
    }
    evidence.actual = docker(COUNT, { since });
    evidence.elapsed_seconds = Date.now() / 1000 - since;
    mkdirSync("../.local/live-evidence", { recursive: true, mode: 0o700 });
    const path = `../.local/live-evidence/switches-${Date.now()}.json`;
    writeFileSync(path, JSON.stringify(evidence, null, 2) + "\n", {
      mode: 0o600,
    });
    chmodSync(path, 0o600);
    console.log(
      `Requests: owner ${evidence.actual.owner.requests}, maya ${evidence.actual.maya.requests}; ${evidence.sends} sends; ${Math.round(evidence.elapsed_seconds)} s`,
    );
  }
  for (const name of [
    "suggestions",
    "style",
    "emotion",
    "calming",
    "media",
    "remember",
  ] as const)
    expect(evidence.restored[name], name).toBe(before.owner.ai[name]);
  expect(await readView(page)).toBe(view);
});
