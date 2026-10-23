import { expect, test } from "@playwright/test";
import { ORIGIN } from "./fixture";

// MW4: a browser pairs with the code the server posts to the Owner Account's
// Saved Messages (ADR 44 Q10); the fixture's synthetic Owner Account keeps
// its posts, and /fixture/pairing-code reads the latest code. The code's
// last digit submits it (ADR 47 Q3).
test("a new browser connects with the code from Saved Messages", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Pair this browser" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /^Connect to 127\.0\.0\.1:8777$/ })
    .tap();
  const field = page.getByLabel("Pairing code");
  await expect(field).toBeVisible();
  const { code } = await (
    await page.request.get("/fixture/pairing-code")
  ).json();
  // A wrong code, sent by its sixth digit, says how many tries are left and
  // empties the field.
  await field.fill(code === "000000" ? "111111" : "000000");
  await expect(page.getByRole("alert")).toContainText("4 tries left");
  await expect(field).toHaveValue("");
  await field.fill(code);
  await expect(
    page.getByRole("button", { name: /^Account: .+\. Switch account$/ }),
  ).toBeVisible();
});

// ADR 47 Q11, Q13: while the Owner Account is signed out, Connect turns into
// Sign in with Telegram. The fixture names an Owner Account that isn't on the
// server yet, which the synthetic login signs in for a number ending in its
// ID, with the code 12345; signing it in pairs this browser as the owner's.
test("a browser pairs by signing a signed-out Owner Account in", async ({
  page,
}) => {
  const owner = (account_id: number) =>
    page.request.post("/fixture/owner", {
      data: { account_id },
      headers: { Origin: ORIGIN },
    });
  expect((await owner(11)).status()).toBe(200);
  try {
    await page.goto("/");
    await page
      .getByRole("button", { name: /^Connect to 127\.0\.0\.1:8777$/ })
      .tap();
    await expect(
      page.getByText(/Sign in with your Telegram account to use Enloq/),
    ).toBeVisible();
    await page.getByLabel("Phone number").fill("+1 202 555 0111");
    await page.getByRole("button", { name: "Continue" }).tap();
    // Telegram's five-digit code submits itself.
    await page.getByLabel("Login code").fill("12345");
    await expect(
      page.getByRole("button", { name: /^Account: .+\. Switch account$/ }),
    ).toBeVisible();
  } finally {
    // Account 1 is the Owner Account again; the signed-in one is removed.
    expect((await owner(1)).status()).toBe(200);
  }
});
