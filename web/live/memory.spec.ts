import { expect, MAYA, openMayaChat, OWNER_ACCOUNT, test } from "./maya";

// ADR 55 PM7 on the live service, in the Maya chat only (AGENTS.md, Live
// Telegram sends): the Correspondent Profile shows Remember this chat, Cares
// about and Traits, and Manage accounts opens About you with the account's
// own memory and traits. It only reads: Remember this chat, memory and traits
// are the owner's and are not touched, and nothing is sent.
const CHAT = `/api/accounts/${OWNER_ACCOUNT}/chats/user:${MAYA}`;
const ACCOUNT = `/api/accounts/${OWNER_ACCOUNT}`;

test("the profile and About you show memory and traits", async ({ page }) => {
  test.setTimeout(120000);
  for (const path of [
    `${CHAT}/remember`,
    `${CHAT}/memory`,
    `${CHAT}/traits`,
    `${ACCOUNT}/memory`,
    `${ACCOUNT}/traits`,
  ]) {
    const answer = await page.request.get(path);
    expect(answer.status(), path).toBe(200);
  }
  const remembered = (await (await page.request.get(`${CHAT}/remember`)).json())
    .enabled;
  const traits = await (await page.request.get(`${CHAT}/traits`)).json();
  expect(traits.scales).toHaveLength(16);
  const own = await (await page.request.get(`${ACCOUNT}/traits`)).json();
  expect(own.scales).toHaveLength(14);

  await openMayaChat(page, "owner");
  await page.getByRole("button", { name: /open profile$/ }).tap();
  await expect(
    page.getByRole("switch", { name: "Remember this chat" }),
  ).toHaveAttribute("aria-checked", String(remembered));
  for (const title of ["Cares about", "Traits"])
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Add a note" })).toBeVisible();
  console.log(
    `Maya: ${traits.scales.filter((s: { label: string | null }) => s.label).length} scales shown, ${traits.notes.length} notes; own: ${own.notes.length} notes`,
  );
  await page.getByRole("button", { name: "Back", exact: true }).tap();
  await page.getByRole("button", { name: "Back to conversations" }).tap();
  await page
    .getByRole("button", { name: /^Account: .+\. Switch account$/ })
    .tap();
  await page.getByRole("button", { name: "Manage accounts" }).tap();
  await page.getByRole("button", { name: /^About you: Sergi/ }).tap();
  for (const title of ["You care about", "Your traits"])
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
});
