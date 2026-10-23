import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust } from "./fixture";

// CU3 (ADR 46): AI controls and the Correspondent Profile in the web, in
// the app's design.
test.use({ permissions: ["clipboard-read", "clipboard-write"] });

test.beforeEach(async ({ page }) => {
  await trust(page);
});

async function post(page: Page, path: string, data: object = {}) {
  const answer = await page.request.post(path, {
    data,
    headers: { Origin: ORIGIN },
  });
  expect(answer.status()).toBe(200);
  return answer.json();
}

// One titled section of a screen.
function section(page: Page, title: string) {
  return page.locator(".screen-section", {
    has: page.getByRole("heading", { name: title, exact: true }),
  });
}

async function view(page: Page, reading_view: "ai" | "originals") {
  await post(page, "/api/preferences/reading-view", { reading_view });
}

test("the profile reads the person's details, what they care about and their traits", async ({
  page,
}) => {
  await post(page, "/api/test/details");
  await page.reload();
  await page.getByRole("button", { name: /^Message details/ }).tap();
  // The header's button names the emotion when one shows (ADR 47).
  await page
    .getByRole("button", { name: /^Message details, .*open profile$/ })
    .tap();
  await expect(page.getByRole("heading", { name: "Morgan Lee" })).toBeVisible();
  await expect(page.getByText("@authored_morgan")).toBeVisible();
  await expect(page.getByText("Authored bio for the journeys.")).toBeVisible();
  await expect(page.getByText("No current estimate")).toBeVisible();
  // Remember this chat is Off by default since ADR 56 (Q9).
  await expect(
    page.getByRole("switch", { name: "Remember this chat" }),
  ).toHaveAttribute("aria-checked", "false");
  await expect(
    page.getByText(/^Off by default\. On, Enloq notes what this person/),
  ).toBeVisible();
  const cares = section(page, "Cares about");
  await expect(cares.getByText("Nothing noted yet.")).toBeVisible();
  const field = cares.getByRole("textbox", { name: "Add a note" });
  await field.fill("Prefers calls on Friday");
  await expect(page.getByText("23/300 bytes")).toBeVisible();
  // Letters outside English take two bytes each.
  await field.fill("Пятница");
  await expect(page.getByText("14/300 bytes")).toBeVisible();
  await field.fill("Prefers calls on Friday");
  await cares.getByRole("button", { name: "Add note" }).tap();
  await expect(cares.getByText("Prefers calls on Friday")).toBeVisible();
  await expect(cares.getByText("Added or edited by you")).toBeVisible();
  await cares
    .getByRole("button", { name: "Edit: Prefers calls on Friday" })
    .tap();
  await cares
    .getByRole("textbox", { name: "Edit note" })
    .fill("Prefers calls on Friday morning");
  await cares.getByRole("button", { name: "Save" }).tap();
  await expect(
    cares.getByText("Prefers calls on Friday morning"),
  ).toBeVisible();
  await cares.getByRole("button", { name: "Clear…" }).tap();
  await cares.getByRole("button", { name: "Clear memory" }).tap();
  await expect(cares.getByText("Nothing noted yet.")).toBeVisible();
  // The owner sets a trait the model never changes, and adds a note.
  const traits = section(page, "Traits");
  await traits.getByRole("button", { name: "Change traits" }).tap();
  await traits.getByRole("button", { name: "Formality: Formal" }).tap();
  await traits.getByRole("button", { name: "Done" }).tap();
  await expect(traits.getByText("Set by you")).toBeVisible();
  await traits
    .getByRole("textbox", { name: "Add a trait note" })
    .fill("Answers late at night");
  await traits.getByRole("button", { name: "Add trait note" }).tap();
  await expect(traits.getByText("Answers late at night")).toBeVisible();
  await traits
    .getByRole("button", { name: "Remove: Answers late at night" })
    .tap();
  await expect(traits.getByText("Answers late at night")).toHaveCount(0);
  // The profile holds Copy chat link since ADR 47 Q8.
  await page.getByRole("button", { name: "Copy chat link" }).tap();
  await expect(page.getByText("Chat link copied")).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).tap();
  await expect(page.getByText("Later message 39")).toBeVisible();
});

test("a failed block retries, and a block shows its originals alone", async ({
  page,
}) => {
  await view(page, "ai");
  // The failing engine leaves with this journey, whatever happens in it:
  // the journeys after it share the server and rewrite with the offline one.
  try {
    await post(page, "/api/test/stream", { fail: true });
    await post(page, "/api/test/stream", { action: "finish" });
    await page.reload();
    await page.getByRole("button", { name: /^Streaming/ }).tap();
    const bubble = page
      .locator("article.app-bubble")
      .filter({ hasText: "Streaming fixture original source" });
    await expect(bubble.getByRole("button", { name: "Retry" })).toBeVisible({
      timeout: 15000,
    });
    await bubble.getByRole("button", { name: "Retry" }).tap();
    await expect
      .poll(
        async () =>
          (await post(page, "/api/test/stream", { action: "status" })).calls,
      )
      .toBe(2);
  } finally {
    await post(page, "/api/test/stream", { action: "restore" });
  }
  // A rewritten block's sheet shows its originals, leaving the others as AI.
  await post(page, "/api/test/menu", { settled: true });
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await page.getByRole("button", { name: /^Menu fixture/ }).tap();
  const block = page.locator("article.app-bubble").filter({
    has: page.getByText(/^AI · 2 messages/),
  });
  await block.click({ button: "right" });
  await page
    .getByRole("menu", { name: "Message actions" })
    .getByRole("menuitem", { name: "Show original" })
    .tap();
  await expect(page.getByText(/^AI · 2 messages/)).toHaveCount(0);
  await page.locator("article.app-bubble").first().click({ button: "right" });
  await page
    .getByRole("menu", { name: "Message actions" })
    .getByRole("menuitem", { name: "Show AI text" })
    .tap();
  await expect(page.getByText(/^AI · 2 messages/)).toBeVisible();
});

test("earlier messages rewrite, a skipped file shows its note, and an account's AI switches", async ({
  page,
}) => {
  await view(page, "ai");
  await post(page, "/api/test/media-kinds");
  await post(page, "/api/test/older");
  await page.reload();
  await page.getByRole("button", { name: /^Mixed block/ }).tap();
  await expect(page.getByText("Not analysed: File contract.pdf")).toBeVisible({
    timeout: 15000,
  });
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await page.getByRole("button", { name: /^Earlier/ }).tap();
  await expect(
    page.getByText("2 earlier blocks are not rewritten"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Rewrite earlier messages" }).tap();
  await expect(page.getByText("2 earlier blocks are not rewritten")).toBeHidden(
    { timeout: 20000 },
  );
  // Manage accounts switches each signed-in account's AI processing.
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await page
    .getByRole("button", { name: /^Account: .+\. Switch account$/ })
    .tap();
  await page.getByRole("button", { name: "Manage accounts" }).tap();
  const ai = page.getByRole("switch", {
    name: "AI processing for Demo account",
  });
  await expect(ai).toHaveAttribute("aria-checked", "true");
  await ai.tap();
  await expect(ai).toHaveAttribute("aria-checked", "false");
  await ai.tap();
  await expect(ai).toHaveAttribute("aria-checked", "true");
  // About you (ADR 55 Q21): the account's own memory and traits.
  await page.getByRole("button", { name: "About you: Demo account" }).tap();
  const mine = section(page, "You care about");
  await mine
    .getByRole("textbox", { name: "Add a note" })
    .fill("No calls after 9");
  await mine.getByRole("button", { name: "Add note" }).tap();
  await expect(mine.getByText("No calls after 9")).toBeVisible();
  await mine.getByRole("button", { name: "Remove: No calls after 9" }).tap();
  await expect(mine.getByText("Nothing noted yet.")).toBeVisible();
  await expect(section(page, "Your traits")).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).tap();
  await expect(
    page.getByRole("heading", { name: "Manage accounts" }),
  ).toBeVisible();
});
