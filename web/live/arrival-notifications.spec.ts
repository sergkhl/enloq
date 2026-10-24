import { execFileSync } from "node:child_process";
import { chmodSync, writeFileSync } from "node:fs";
import {
  expect,
  MAYA,
  openMayaChat,
  ORIGIN,
  OWNER_ACCOUNT,
  test,
} from "./maya";

// VPS browser first, through the existing service owner. Three authored native
// sends, at most eight model requests (three screens, one block decision,
// one softening plus its three no-work retries), 15 minutes and $0.10 counted.
// Read-only local metadata proves eligibility before quiet and silent updates;
// this does not claim that a particular phone displayed an OS banner.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
const READ = String.raw`
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
notice=db.execute('SELECT * FROM incoming_notices WHERE chat_key=?',(key,)).fetchone()
result={'sources':[dict(row) if (row:=db.execute('SELECT state,deadline FROM notice_sources WHERE chat_key=? AND message_id=?',(key,i)).fetchone()) else None for i in q['sources']]}
if notice:
 result['notice']={k:notice[k] for k in ('kind','silent','eligible','quiet','burst','alerted','state')}
 result['notice']['latest_selected']=str(q['sources'][-1]) in json.loads(notice['inputs'] or '{}')
result['blocks']=[dict(row) for row in db.execute("SELECT state,calmed,rendered_revision=revision AS current,coalesce((SELECT json_extract(payload,'$.decided') FROM incoming_deferred WHERE scope=blocks.id AND input_revision=blocks.revision),0) AS decided FROM blocks WHERE chat_key=? AND EXISTS(SELECT 1 FROM json_each(sources) WHERE value=?)",(key,q['sources'][0]))]
print(json.dumps(result))
`;

function status(sources: number[]): any {
  return JSON.parse(
    execFileSync("docker", ["exec", "-i", "enloq", "python", "-c", READ], {
      input: JSON.stringify({ owner: OWNER_ACCOUNT, maya: MAYA, sources }),
      encoding: "utf8",
    }),
  );
}

test("arrival screens release clean previews before quiet and later updates stay silent", async ({
  page,
}) => {
  test.setTimeout(900000);
  await openMayaChat(page, "owner");
  const initial = await (await page.request.get(CHAT)).json();
  expect(
    !initial.draft.text &&
      !initial.draft.attachments.length &&
      !initial.draft.selected_media,
  ).toBe(true);
  expect(
    (await (await page.request.get(`${CHAT}/remember`)).json()).enabled,
  ).toBe(false);
  const settings = await (
    await page.request.get(`${CHAT}/suggestions/settings`)
  ).json();
  const calming = await (await page.request.get(`${CHAT}/calming`)).json();
  const change = (suffix: string, data: unknown) =>
    page.request.post(CHAT + suffix, { data, headers: { Origin: ORIGIN } });
  const stamp = Date.now();
  const texts = [
    `I'm angry about the delay. Please send the file on Friday. [Enloq authored arrival check ${stamp}:1]`,
    `You useless idiot. Send the file on Friday. [Enloq authored arrival check ${stamp}:2]`,
    `The folder is blue. [Enloq authored arrival check ${stamp}:3]`,
  ];
  const sources: number[] = [];
  const evidence: Record<string, any> = {};
  async function send(index: number) {
    await openMayaChat(page, "maya");
    const field = page.getByRole("textbox", { name: "Message" });
    expect((await field.inputValue()).length === 0).toBe(true);
    await field.fill(texts[index]!);
    await page.getByRole("button", { name: "Send", exact: true }).tap();
    await expect(field).toHaveValue("");
    let source: number | undefined;
    await expect
      .poll(
        async () => {
          const value = await (await page.request.get(CHAT)).json();
          source = value.messages.find(
            (m: any) => !m.outgoing && m.text === texts[index],
          )?.message_id;
          return !!source;
        },
        { timeout: 30000, intervals: [500] },
      )
      .toBe(true);
    sources.push(source!);
  }
  try {
    expect(
      (
        await change("/suggestions/settings", {
          automatic: false,
          style: settings.style,
        })
      ).status(),
    ).toBe(200);
    expect((await change("/calming", { enabled: true })).status()).toBe(200);
    await send(0);
    await expect
      .poll(() => status(sources).notice?.eligible, { timeout: 10000 })
      .toBeTruthy();
    evidence.first = status(sources);
    expect(
      evidence.first.notice.eligible - (evidence.first.sources[0].deadline - 5),
    ).toBeLessThan(10);
    expect(evidence.first.notice.kind).toBe("original");
    // Focusing this exact chat consumes its first eligible notice silently.
    await openMayaChat(page, "owner");
    await expect
      .poll(() => status(sources).notice.alerted, { timeout: 10000 })
      .toBe(1);
    await send(1);
    await expect
      .poll(() => status(sources).sources[1]?.state, { timeout: 10000 })
      .not.toBe("waiting");
    evidence.hostile = status(sources);
    if (evidence.hostile.sources[1].state === "hostile")
      expect(evidence.hostile.notice.latest_selected).toBe(false);
    await send(2);
    await expect
      .poll(() => status(sources).notice.latest_selected, { timeout: 10000 })
      .toBe(true);
    evidence.followup = status(sources);
    expect(evidence.followup.notice.burst).toBe(evidence.first.notice.burst);
    expect(evidence.followup.notice.silent).toBe(1);
    await expect
      .poll(
        () =>
          status(sources).blocks.some(
            (b: any) =>
              b.state === "failed" ||
              (b.state === "ready" && b.current) ||
              (b.state === "original" && b.decided),
          ),
        { timeout: 600000, intervals: [5000] },
      )
      .toBe(true);
    evidence.settled = status(sources);
    expect(evidence.settled.notice.latest_selected).toBe(true);
    expect(evidence.settled.notice.silent).toBe(1);
    evidence.quality =
      evidence.first.sources[0].state === "clean" &&
      evidence.hostile.sources[1].state === "hostile"
        ? "as expected"
        : "Low quality or screening unavailable";
  } finally {
    await change("/suggestions/settings", settings);
    await change("/calming", calming);
    const path = "../.local/live-results/arrival-notifications.json";
    writeFileSync(path, JSON.stringify(evidence, null, 2) + "\n", {
      mode: 0o600,
    });
    chmodSync(path, 0o600);
  }
});
