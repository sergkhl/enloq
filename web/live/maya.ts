import { readFileSync } from "node:fs";
import { expect, test as base, type Page } from "@playwright/test";

// The live service and its two test accounts stay out of the repository, in
// `.local/live-targets.json` (read from `web/`, where live checks run):
// {"origin": "https://…", "owner": <Owner Account ID>, "maya": <Maya's ID>}.
const TARGETS_FILE = "../.local/live-targets.json";

function targets() {
  let found: { origin?: unknown; owner?: unknown; maya?: unknown };
  try {
    found = JSON.parse(readFileSync(TARGETS_FILE, "utf8"));
  } catch {
    throw new Error(
      `Live checks need ${TARGETS_FILE} (AGENTS.md, Live Telegram sends)`,
    );
  }
  const { origin, owner, maya } = found;
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
  return { origin, owner: owner as number, maya: maya as number };
}

// The one chat live checks may open or send in (AGENTS.md, Live Telegram
// sends): between the Owner Account and Maya, from either side.
export const { origin: ORIGIN, owner: OWNER_ACCOUNT, maya: MAYA } = targets();
const OTHER = new Map([
  [OWNER_ACCOUNT, MAYA],
  [MAYA, OWNER_ACCOUNT],
]);

// API paths that name one correspondent: a chat's own routes and avatars.
const NAMED = /^\/api\/accounts\/(\d+)\/(?:chats|avatars)\/([^/?]+)/;

export function permitted(pathname: string) {
  const named = NAMED.exec(pathname);
  if (!named) return true;
  const account = Number(named[1]);
  const peer = decodeURIComponent(named[2]);
  const other = OTHER.get(account);
  // The Maya chat, or the account's own avatar in the header.
  return (
    other !== undefined &&
    (peer === `user:${other}` || peer === `user:${account}`)
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
      if (permitted(pathname)) return route.continue();
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

// The header's robot toggles the Reading View, pressed for AI (ADR 52 Q13).
export async function chooseView(page: Page, view: "ai" | "originals") {
  const toggle = page.getByRole("button", { name: "AI view" });
  const ai = String(view === "ai");
  if ((await toggle.getAttribute("aria-pressed")) !== ai) await toggle.tap();
  await expect(toggle).toHaveAttribute("aria-pressed", ai);
}
