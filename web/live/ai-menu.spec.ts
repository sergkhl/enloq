import type { Locator, Page } from "@playwright/test";
import {
  expect,
  MAYA,
  openAiMenu,
  openMayaChat,
  openTestGroup,
  ORIGIN,
  OWNER_ACCOUNT,
  readView,
  TEST_GROUP,
  test,
} from "./maya";

// ADR 62 Phase 1 on the live service: the AI Menu in the Maya chat from both
// sides and in the test group from Maya's side (AGENTS.md, Live Telegram
// sends). AI view is switched and put back, every page opens, Soften hostile
// messages is flipped in the menu and put back, and each Guidance field's
// editor opens without saving. Nothing is sent and nothing asks a model. The
// Reading View, the chat's switches, Style and draft are read first and
// checked, or put back, at the end.

type ChatAi = {
  account: boolean;
  running: boolean;
  suggestions: boolean | null;
  style: string | null;
  calming: boolean | null;
  remember: boolean | null;
};

const SIDES = {
  owner: `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`,
  maya: `/api/accounts/${MAYA}/chats/user:${OWNER_ACCOUNT}`,
};

const ROWS = [
  "AI view",
  "Automatic suggestions",
  /^Style · /,
  "Emotion",
  "Soften hostile messages",
  "Voice and images",
  "Remember this chat",
];
const PAGES = [
  "AI view",
  "Automatic suggestions",
  "Style",
  "Emotion",
  "Soften hostile messages",
  "Voice and images",
  "Remember this chat",
];

async function read(page: Page, path: string) {
  const answer = await page.request.get(path);
  expect(answer.status(), path).toBe(200);
  const snapshot = await answer.json();
  return { ai: snapshot.ai as ChatAi, draft: snapshot.draft.text as string };
}

// The guidance version and the model calls counted today, for the log.
async function budget(page: Page) {
  const answer = await page.request.get("/api/settings");
  expect(answer.status()).toBe(200);
  const settings = await answer.json();
  return {
    calls: settings.budget.calls as number,
    version: settings.guidance?.version as number,
  };
}

// Each Guidance field of the open page opens its editor and closes unsaved.
// Every page but AI view's holds at least one, once the settings are read.
async function openFields(page: Page, name: string) {
  const region = page.getByRole("region", { name, exact: true });
  if (name !== "AI view")
    await expect(
      region.getByRole("heading", { name: "Guidance", exact: true }),
    ).toBeVisible();
  const fields = region.getByRole("button", { name: /for all chats$/i });
  const count = await fields.count();
  for (let index = 0; index < count; index++) {
    const field = fields.nth(index);
    const label = (await field.getAttribute("aria-label"))!.split(",")[0]!;
    await field.tap();
    await expect(page.getByRole("textbox", { name: label })).toBeVisible();
    await expect(page.getByText(/^For all chats · Version \d+$/)).toBeVisible();
    await page.getByRole("button", { name: "Back", exact: true }).last().tap();
    await expect(page.getByRole("textbox", { name: label })).toHaveCount(0);
  }
  return count;
}

async function backFromPage(page: Page) {
  await page
    .getByRole("region", { name: "Conversation" })
    .getByRole("button", { name: "Back", exact: true })
    .tap();
}

async function rows(menu: Locator) {
  return menu.locator(".ai-row-label");
}

test("the AI Menu in the Maya chat from both sides and in the test group", async ({
  page,
}) => {
  test.setTimeout(900000);
  const view = await readView(page);
  const before = {
    owner: await read(page, SIDES.owner),
    maya: await read(page, SIDES.maya),
  };
  const spent = await budget(page);
  console.log(
    `Before: view ${view}; owner ${JSON.stringify(before.owner.ai)}; maya ${JSON.stringify(before.maya.ai)}; ${spent.calls} calls today`,
  );
  let fields = 0;
  try {
    for (const side of ["owner", "maya"] as const) {
      await openMayaChat(page, side);
      const ai = before[side].ai;
      await expect(
        page.getByRole("button", {
          name: ai.running
            ? "AI Menu, AI on in this chat"
            : "AI Menu, AI off in this chat",
        }),
      ).toBeVisible();
      const menu = await openAiMenu(page);
      await expect(await rows(menu)).toHaveText(ROWS);
      for (const [name, value] of [
        ["Automatic suggestions", ai.suggestions],
        ["Soften hostile messages", ai.calming],
        ["Remember this chat", ai.remember],
      ] as const)
        await expect(menu.getByRole("switch", { name })).toHaveAttribute(
          "aria-checked",
          String(value),
        );
      // AI view switches in place, and back.
      const aiView = menu.getByRole("switch", { name: "AI view" });
      await aiView.tap();
      await expect(aiView).toHaveAttribute(
        "aria-checked",
        String(view !== "ai"),
      );
      await aiView.tap();
      await expect(aiView).toHaveAttribute(
        "aria-checked",
        String(view === "ai"),
      );
      if (side === "owner") {
        // One switch flips in the menu and back.
        const calm = menu.getByRole("switch", {
          name: "Soften hostile messages",
        });
        await calm.tap();
        await expect(calm).toHaveAttribute("aria-checked", String(!ai.calming));
        await calm.tap();
        await expect(calm).toHaveAttribute("aria-checked", String(ai.calming));
      }
      await page.keyboard.press("Escape");
      // Every page opens; the owner's side opens each Guidance field too.
      for (const name of PAGES) {
        const opened = await openAiMenu(page);
        await opened
          .getByRole("button", { name, exact: name !== "Style" })
          .first()
          .tap();
        await expect(page.getByRole("heading", { name })).toBeVisible();
        if (side === "owner") fields += await openFields(page, name);
        await backFromPage(page);
      }
      await page.screenshot({
        path: `../.local/live-results/ai-menu-${side}.png`,
      });
    }
    if (!TEST_GROUP)
      throw new Error("Name the test group in live-targets.json");
    await openTestGroup(page, "maya");
    const menu = await openAiMenu(page);
    const labels = await rows(menu);
    await expect(labels.first()).toHaveText("AI view");
    await expect(labels.last()).toHaveText("Hide bots");
    await expect(
      menu.getByRole("switch", { name: "Group AI processing" }),
    ).toBeVisible();
    await menu.getByRole("button", { name: "Group AI processing" }).tap();
    await expect(
      page.getByRole("heading", { name: "Group AI processing" }),
    ).toBeVisible();
    fields += await openFields(page, "Group AI processing");
    await backFromPage(page);
    await openAiMenu(page);
    await page.screenshot({ path: "../.local/live-results/ai-menu-group.png" });
    await page.keyboard.press("Escape");
  } finally {
    // Put back what the check may have changed, even when it failed.
    const restored = await page.request.post("/api/preferences/reading-view", {
      data: { reading_view: view },
      headers: { Origin: ORIGIN },
    });
    expect(restored.status()).toBe(200);
    if (before.owner.ai.calming !== null) {
      const calm = await page.request.post(`${SIDES.owner}/calming`, {
        data: { enabled: before.owner.ai.calming },
        headers: { Origin: ORIGIN },
      });
      expect(calm.status()).toBe(200);
    }
  }
  expect(await readView(page)).toBe(view);
  for (const side of ["owner", "maya"] as const) {
    const after = await read(page, side === "owner" ? SIDES.owner : SIDES.maya);
    expect(after.ai).toEqual(before[side].ai);
    expect(after.draft).toBe(before[side].draft);
  }
  const now = await budget(page);
  expect(now.version).toBe(spent.version);
  // Q17's fields: 13 on the private pages, Faithfulness rules twice, and the
  // group's briefings.
  expect(fields).toBe(13);
  console.log(
    `${fields} Guidance editors opened unsaved; guidance version ${now.version} unchanged; ${now.calls - spent.calls} calls counted meanwhile across the server's chats`,
  );
});
