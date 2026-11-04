import { expect, MAYA, openRealGroup, REAL_GROUP, test } from "./maya";

// ADR 59 on real traffic, in the real group Maya joined at the owner's word:
// from her side only, and it sends nothing, since other people are there (the
// guard in maya.ts refuses any other write). Group AI goes On for the run and
// back Off, so her copy spends nothing on briefings between runs. It prints only
// statuses and counts: answers hold other people's words, and never leave this
// page. At most 60 model requests besides the embeddings its messages need.
test.setTimeout(600000);

const QUESTIONS = [
  "О чём сегодня говорили в чате?",
  "Что сегодня спрашивали про аренду жилья или визу?",
  "What did people ask about today?",
];
// No group's messages answer this.
const NOTHING = "What is the airspeed of a laden swallow on Mars?";

type AskView = {
  status: string;
  statements: { citations: number[]; unsupported: number[] }[];
};

test("Ask answers in the real group, and nothing is sent", async ({ page }) => {
  const chat = `/api/accounts/${MAYA}/chats/${encodeURIComponent(REAL_GROUP!)}`;
  const setProcessing = async (enabled: boolean) => {
    const answer = await page.request.post(`${chat}/processing`, {
      data: { enabled },
      headers: { Origin: new URL(page.url()).origin },
    });
    expect(answer.status()).toBe(200);
  };
  await openRealGroup(page);
  await setProcessing(true);
  try {
    await openRealGroup(page);
    await page.reload();
    await page.getByRole("button", { name: "Conversation actions" }).tap();
    await page
      .getByRole("dialog", { name: "Conversation actions" })
      .getByRole("button", { name: "Ask this group" })
      .tap();
    const ask = page.getByRole("region", { name: "Ask this group" });
    const question = ask.getByRole("textbox", { name: "Question" });
    const send = ask.getByRole("button", { name: "Ask", exact: true });
    const asking = async (words: string) => {
      const started = Date.now();
      await question.fill(words);
      const answered = page.waitForResponse(
        (response) => response.url().endsWith("/ask"),
        { timeout: 300000 },
      );
      await send.tap();
      const response = await answered;
      expect(response.status()).toBe(200);
      const view = (await response.json()) as AskView;
      await expect(ask.getByText("Answering…")).toHaveCount(0);
      await expect(ask.getByRole("alert")).toHaveCount(0);
      console.log(
        `${view.status}: ${view.statements.length} statements, ` +
          `${view.statements.flatMap((s) => s.citations).length} kept citations, ` +
          `${view.statements.flatMap((s) => s.unsupported).length} unsupported, ` +
          `${((Date.now() - started) / 1000).toFixed(1)} s`,
      );
      return view;
    };
    const views = [];
    for (const words of QUESTIONS) views.push(await asking(words));
    expect(
      views.filter((view) => view.status === "answer").length,
    ).toBeGreaterThan(0);
    expect((await asking(NOTHING)).status).toBe("not_found");

    // A source opens the group at its original, and Back returns to the answer.
    for (const words of QUESTIONS) {
      const view = await asking(words);
      if (!view.statements.some((s) => s.citations.length)) continue;
      const source = ask.getByRole("button", { name: /^Source \d+$/ }).first();
      const id = (await source.getAttribute("aria-label"))!.split(" ")[1];
      await source.tap();
      await expect(ask).toBeHidden();
      await expect(
        page
          .locator(
            `section.native-source[data-source-id="${id}"], ` +
              `article.message-bubble[data-source-ids~="${id}"], ` +
              "article.app-bubble.highlighted",
          )
          .first(),
      ).toBeInViewport();
      await page.getByRole("button", { name: "Back to answer" }).tap();
      await expect(
        ask.getByRole("button", { name: /^Source \d+$/ }).first(),
      ).toBeVisible();
      break;
    }
  } finally {
    await setProcessing(false);
  }
});
