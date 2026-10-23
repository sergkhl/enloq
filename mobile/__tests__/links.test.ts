import { linkSegments, safeURL } from "../../shared/links";

// ADR 46 Q9: the link rules both clients share (shared/links.ts).
test("finds addresses, www, t.me and @usernames in plain text", () => {
  expect(
    linkSegments(
      "See https://example.com/a?b=1, www.example.org and @enloq_bot.",
    ),
  ).toEqual([
    { text: "See ", url: null },
    { text: "https://example.com/a?b=1", url: "https://example.com/a?b=1" },
    { text: ", ", url: null },
    { text: "www.example.org", url: "https://www.example.org" },
    { text: " and ", url: null },
    { text: "@enloq_bot", url: "https://t.me/enloq_bot" },
    { text: ".", url: null },
  ]);
  expect(linkSegments("(t.me/channel)")).toEqual([
    { text: "(", url: null },
    { text: "t.me/channel", url: "https://t.me/channel" },
    { text: ")", url: null },
  ]);
});

test("Telegram's own text links keep their label; only http and https open", () => {
  expect(
    linkSegments("Open the plan or this", [
      { offset: 9, length: 4, url: "https://example.com/plan" },
      { offset: 17, length: 4, url: "javascript:alert(1)" },
    ]),
  ).toEqual([
    { text: "Open the ", url: null },
    { text: "plan", url: "https://example.com/plan" },
    { text: " or ", url: null },
    { text: "this", url: null },
  ]);
  expect(safeURL("ftp://example.com")).toBeNull();
  expect(safeURL("https://exa mple.com")).toBeNull();
  // An entity outside the text is ignored.
  expect(
    linkSegments("short", [{ offset: 3, length: 9, url: "https://x.io" }]),
  ).toEqual([{ text: "short", url: null }]);
});
