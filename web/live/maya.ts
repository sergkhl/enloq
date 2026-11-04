import { readFileSync } from "node:fs";
import { expect, test as base, type Page } from "@playwright/test";

// The live service and its two test accounts stay out of the repository, in
// `.local/live-targets.json` (read from `web/`, where live checks run):
// {"origin": "https://…", "owner": <Owner Account ID>, "maya": <Maya's ID>}.
const TARGETS_FILE = "../.local/live-targets.json";

function targets() {
  let found: {
    origin?: unknown;
    owner?: unknown;
    maya?: unknown;
    group?: unknown;
    real_group?: unknown;
  };
  try {
    found = JSON.parse(readFileSync(TARGETS_FILE, "utf8"));
  } catch {
    throw new Error(
      `Live checks need ${TARGETS_FILE} (AGENTS.md, Live Telegram sends)`,
    );
  }
  const { origin, owner, maya, group, real_group } = found;
  const id = (value: unknown) =>
    typeof value === "number" && Number.isSafeInteger(value) && value > 0;
  if (
    typeof origin !== "string" ||
    !origin.startsWith("https://") ||
    !id(owner) ||
    !id(maya)
  )
    throw new Error(
      `${TARGETS_FILE} needs an https origin and two account IDs`,
    );
  return {
    origin,
    owner: owner as number,
    maya: maya as number,
    group: typeof group === "string" ? group : null,
    realGroup: typeof real_group === "string" ? real_group : null,
  };
}

// The chats live checks may open or send in (AGENTS.md, Live Telegram sends):
// the one between the Owner Account and Maya, from either side, and the test
// group of those two alone, `channel:<id>` under `group` in the same file.
// `real_group` names the real group Maya joined at the owner's word: her side
// only, and read-only, for other people are there.
export const {
  origin: ORIGIN,
  owner: OWNER_ACCOUNT,
  maya: MAYA,
  group: TEST_GROUP,
  realGroup: REAL_GROUP,
} = targets();
const OTHER = new Map([
  [OWNER_ACCOUNT, MAYA],
  [MAYA, OWNER_ACCOUNT],
]);

// API paths that name one correspondent: a chat's own routes and avatars.
const NAMED = /^\/api\/accounts\/(\d+)\/(?:chats|avatars)\/([^/?]+)/;

// The only POSTs the real group's screen may make, reads and local switches:
// none reaches Telegram as a write.
const REAL_GROUP_POSTS =
  /\/(ask|processing|viewed|originals\/(replies|current|forwarding))$/;

export function permitted(pathname: string, method = "GET") {
  const named = NAMED.exec(pathname);
  if (!named) return true;
  const account = Number(named[1]);
  const peer = decodeURIComponent(named[2]);
  const other = OTHER.get(account);
  if (peer === REAL_GROUP)
    return (
      account === MAYA && (method === "GET" || REAL_GROUP_POSTS.test(pathname))
    );
  // The Maya chat, the test group, or the account's own avatar in the header.
  return (
    other !== undefined &&
    (peer === `user:${other}` ||
      peer === `user:${account}` ||
      peer === TEST_GROUP)
  );
}

// Every request naming another correspondent is refused. A read of the list's
// avatars is harmless, but anything else fails the check.
export const test = base.extend<{ refused: string[] }>({
  refused: async ({}, use) => {
    await use([]);
  },
  page: async ({ page, refused }, use) => {
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const { pathname } = new URL(request.url());
      if (permitted(pathname, request.method())) return route.continue();
      refused.push(`${request.method()} ${pathname}`);
      return route.abort("blockedbyclient");
    });
    await use(page);
    expect(refused.filter((line) => !line.startsWith("GET "))).toEqual([]);
  },
});
export { expect };

// Opens the Maya chat as `side` by its chat link (ADR 36), which works
// whether or not the chat is still in the seven-day list.
export async function openMayaChat(page: Page, side: "owner" | "maya") {
  const account = side === "maya" ? MAYA : OWNER_ACCOUNT;
  await page.goto(`/?account=${account}#${OTHER.get(account)}`);
  const ask = page.getByRole("button", { name: "Not now" });
  if (await ask.isVisible().catch(() => false)) await ask.click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    side === "maya" ? /^(?!Maya)\S/ : /^Maya/,
  );
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  return page;
}

// Opens the test group as `side` by its chat link (ADR 36), where a supergroup
// is `-100` and its ID.
export async function openTestGroup(page: Page, side: "owner" | "maya") {
  if (!TEST_GROUP) throw new Error("Name the test group in live-targets.json");
  const account = side === "maya" ? MAYA : OWNER_ACCOUNT;
  const id = TEST_GROUP.replace(/^channel:/, "-100").replace(/^chat:/, "-");
  await page.goto(`/?account=${account}#${id}`);
  const ask = page.getByRole("button", { name: "Not now" });
  if (await ask.isVisible().catch(() => false)) await ask.click();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  return page;
}

// Opens the real group from Maya's side by its chat link (ADR 36).
export async function openRealGroup(page: Page) {
  if (!REAL_GROUP) throw new Error("Name the group in live-targets.json");
  const id = REAL_GROUP.replace(/^channel:/, "-100");
  await page.goto(`/?account=${MAYA}#${id}`);
  const ask = page.getByRole("button", { name: "Not now" });
  if (await ask.isVisible().catch(() => false)) await ask.click();
  await expect(
    page.getByRole("button", { name: "Conversation actions" }),
  ).toBeVisible();
  return page;
}

// The header's robot toggles the Reading View, pressed for AI (ADR 52 Q13).
export async function chooseView(page: Page, view: "ai" | "originals") {
  const toggle = page.getByRole("button", { name: "AI view" });
  const ai = String(view === "ai");
  if ((await toggle.getAttribute("aria-pressed")) !== ai) await toggle.tap();
  await expect(toggle).toHaveAttribute("aria-pressed", ai);
}
