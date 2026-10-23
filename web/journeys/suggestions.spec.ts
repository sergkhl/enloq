import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, trust, openSettings } from "./fixture";

// ADR 49: contrasting answer stances above the composer (Q4, Q8), the When to
// suggest field (Q9) and the owner's notice of a decision-model outage (Q14).
// ADR 56: a fold kept for every client (Q12 B) and each chat's Suggestions
// choice in its profile (Q12 A).
test.beforeEach(async ({ page }) => {
  await trust(page);
});

const ANSWERS = [
  { purpose: "accept", text: "Yes, I'll be there." },
  { purpose: "decline", text: "Sorry, I can't make it this time." },
  { purpose: "later", text: "Let me check and get back to you." },
];

// The fixture's chat (`scripts/suggestion_fixture.py`) and its answers.
const CHAT = "/api/accounts/1/chats/user%3A126";

async function answers(page: Page) {
  const setup = await page.request.post("/api/test/suggestions", {
    data: {
      action: "setup",
      options: ANSWERS,
      text: "We're having a barbecue on Saturday! Are you coming?",
      code: "excited",
    },
    headers: { Origin: ORIGIN },
  });
  expect(setup.status()).toBe(200);
  await page.getByRole("button", { name: /^Suggestion fixture/ }).tap();
}

test("answer stances fold for every client, reopen and insert into the draft with Undo", async ({
  page,
}) => {
  await answers(page);
  const area = page.getByRole("region", { name: "Response suggestions" });
  await expect(area.getByRole("button", { name: /^Accept/ })).toBeVisible();
  await expect(area.getByRole("button", { name: /^Decline/ })).toBeVisible();
  await expect(area.getByRole("button", { name: /^Later/ })).toBeVisible();

  await area.getByRole("button", { name: "Hide suggestions" }).tap();
  const reopen = area.getByRole("button", { name: "Suggestions (3)" });
  await expect(reopen).toBeVisible();
  await expect(area.getByRole("button", { name: /^Accept/ })).toHaveCount(0);
  // The server keeps the fold, so every client and a reload show it.
  await expect
    .poll(async () => (await (await page.request.get(CHAT)).json()).suggestions)
    .toMatchObject({ folded: true });
  await page.reload();
  await expect(reopen).toBeVisible();
  await expect(area.getByRole("button", { name: /^Accept/ })).toHaveCount(0);
  await reopen.tap();
  await expect(area.getByRole("button", { name: /^Accept/ })).toBeVisible();

  const field = page.getByRole("textbox", { name: "Message" });
  await area.getByRole("button", { name: /^Decline/ }).tap();
  await expect(field).toHaveValue("Sorry, I can't make it this time.");
  await page.getByRole("button", { name: "Undo suggestion" }).tap();
  await expect(field).toHaveValue("");
});

test("the profile's Suggestions choice turns the chat's suggestions off and on", async ({
  page,
}) => {
  await answers(page);
  const area = page.getByRole("region", { name: "Response suggestions" });
  await expect(area.getByRole("button", { name: /^Accept/ })).toBeVisible();
  const profile = page.getByRole("button", {
    name: /^Suggestion fixture,.* open profile$/,
  });
  const choice = page.getByRole("radiogroup", { name: "Suggestions" });
  try {
    await profile.tap();
    await expect(choice.getByRole("radio", { name: "On" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    // Idioms is chosen per chat since its launch (ADR 56 Q20 A).
    await expect(choice.getByRole("radio")).toHaveCount(3);
    await choice.getByRole("radio", { name: "Idioms" }).tap();
    await expect(choice.getByRole("radio", { name: "Idioms" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expect(
      page.getByText(
        "Suggestions use idioms, proverbs, set expressions and phrasal verbs in this chat's language.",
      ),
    ).toBeVisible();
    await choice.getByRole("radio", { name: "On" }).tap();
    await expect(
      page.getByText(
        "Short reply suggestions appear above the composer when a message is emotional or calls for a reply.",
      ),
    ).toBeVisible();
    await choice.getByRole("radio", { name: "Off" }).tap();
    await expect(choice.getByRole("radio", { name: "Off" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expect(
      page.getByText("No reply suggestions in this chat."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Back", exact: true }).tap();
    // Off shows nothing in the chat; On again shows the set prepared before
    // while it is still valid.
    await expect(area).toHaveCount(0);
    await profile.tap();
    await choice.getByRole("radio", { name: "On" }).tap();
    await expect(choice.getByRole("radio", { name: "On" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await page.getByRole("button", { name: "Back", exact: true }).tap();
    await expect(area.getByRole("button", { name: /^Accept/ })).toBeVisible();
  } finally {
    // The journeys share the fixture: the chat goes back to On.
    const restored = await page.request.post(`${CHAT}/suggestions/choice`, {
      data: { choice: "on" },
      headers: { Origin: ORIGIN },
    });
    expect(restored.status()).toBe(200);
  }
});

test("Settings → AI reports a decision-model outage; Guidance offers When to suggest", async ({
  page,
}) => {
  const outage = await page.request.post("/fixture/decision-outage", {
    data: {},
    headers: { Origin: ORIGIN },
  });
  expect(outage.status()).toBe(200);
  try {
    await openSettings(page);
    await page.getByRole("button", { name: /^AI/ }).tap();
    await expect(page.getByRole("heading", { name: "AI" })).toBeVisible();
    await expect(
      page.getByText(/^Decision model unavailable since /),
    ).toBeVisible();
    await page.getByRole("button", { name: /^Guidance/ }).tap();
    // Idiom sets have their own field (ADR 56 Q6).
    await expect(
      page.getByRole("button", { name: "Idioms, Default" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "When to suggest, Default" }).tap();
    await expect(
      page.getByRole("textbox", { name: "When to suggest" }),
    ).toHaveValue(/^Answer yes when the latest communication asks the owner/);
    await page.getByRole("button", { name: "Back" }).tap();
    await page.getByRole("button", { name: "Back" }).tap();
  } finally {
    const restored = await page.request.post("/fixture/decision-restored", {
      data: {},
      headers: { Origin: ORIGIN },
    });
    expect(restored.status()).toBe(200);
  }
  // Settings reads the state again when it opens: the notice is gone.
  await page.reload();
  await openSettings(page);
  await page.getByRole("button", { name: /^AI/ }).tap();
  await expect(page.getByRole("switch", { name: "Suggestions" })).toBeVisible();
  await expect(
    page.getByText(/^Decision model unavailable since /),
  ).toHaveCount(0);
});
