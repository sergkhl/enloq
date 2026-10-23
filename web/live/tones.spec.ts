import { expect, openMayaChat, test } from "./maya";

// ADR 53 CA1 on the live service, in the Maya chat only (AGENTS.md, Live
// Telegram sends): the tone sheet offers three tones, and a draft rewrites in
// the remembered one. Nothing is sent, the tone is left as it was and the
// draft is cleared.
test("three tones, and a draft rewrites in the remembered one", async ({
  page,
}) => {
  test.setTimeout(120000);
  await openMayaChat(page, "owner");
  await page.getByRole("button", { name: "Rewrite tone" }).tap();
  const tones = page.getByRole("radiogroup", { name: "Rewrite tone" });
  await expect(tones.getByRole("radio")).toHaveText([
    "Neutral",
    "Professional",
    "Friendly casual",
  ]);
  await expect(tones.getByRole("radio", { checked: true })).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(tones).toBeHidden();

  const field = page.getByRole("textbox", { name: "Message" });
  const note = `[Enloq live check ADR 53] hey can u send me the file by tmrw thx ${Date.now()}`;
  await field.fill(note);
  await page.getByRole("button", { name: "Rewrite", exact: true }).tap();
  // While it runs, Undo stops it; the rewrite is done when Send returns.
  await expect(page.getByRole("button", { name: "Send" })).toBeEnabled({
    timeout: 90000,
  });
  const rewritten = await field.inputValue();
  expect(rewritten).not.toBe(note);
  expect(rewritten.trim().length).toBeGreaterThan(0);
  console.log(`Rewrite: ${rewritten}`);
  await page.getByRole("button", { name: "Undo rewrite" }).tap();
  await expect(field).toHaveValue(note);
  await field.fill("");
  await expect(field).toHaveValue("");
});
