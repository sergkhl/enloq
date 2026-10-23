import {
  outsideReplies,
  quoteMediaLabel,
  replyQuoteText,
  replyTarget,
  type QuoteReply,
  type QuoteTarget,
} from "../../shared/replyQuote";

// 2026-10-16 12:00 UTC and the same hour two days earlier.
const NOW = Date.UTC(2026, 9, 16, 12) / 1000;
const EARLIER = NOW - 2 * 86400;
const options = {
  timezone: "UTC",
  own: "user:1",
  sender: (target: QuoteTarget) => `Sender of ${target.message_id}`,
};

function reply(values: Partial<QuoteReply> = {}): QuoteReply {
  return { message_id: 20, date: NOW, reply_to: 10, ...values };
}

function target(values: Partial<QuoteTarget> = {}): QuoteTarget {
  return {
    message_id: 10,
    date: NOW - 60,
    text: "Authored  question\nacross lines",
    deleted: false,
    ...values,
  };
}

beforeEach(() => jest.useFakeTimers({ now: NOW * 1000 }));
afterEach(() => jest.useRealTimers());

test("a same-day message shows its sender and one line of its wording", () => {
  expect(replyQuoteText(reply(), target(), options)).toEqual({
    kind: "message",
    sender: "Sender of 10",
    date: null,
    body: "Authored question across lines",
    media: null,
    quoted: false,
    target: 10,
    deleted: false,
  });
});

test("an earlier day adds the date, and the year only outside this one", () => {
  expect(
    replyQuoteText(reply(), target({ date: EARLIER }), options).date,
  ).toMatch(/Oct 14|14 Oct/);
  const lastYear = Date.UTC(2025, 2, 4, 9) / 1000;
  expect(
    replyQuoteText(reply(), target({ date: lastYear }), options).date,
  ).toMatch(/2025/);
});

test("media read as labels, with a caption after its label", () => {
  const voice = target({
    text: "",
    media: "audio",
    media_info: { duration: 12, voice: true },
  });
  expect(quoteMediaLabel(voice)).toBe("Voice message · 0:12");
  expect(
    quoteMediaLabel(
      target({ media: "document", media_info: { file_name: "plan.pdf" } }),
    ),
  ).toBe("plan.pdf");
  const image = replyQuoteText(
    reply(),
    target({ media: "image", text: "Look" }),
    options,
  );
  expect([image.body, image.media]).toEqual(["Image · Look", "image"]);
  expect(quoteMediaLabel(target({ media: "link" }))).toBeNull();
});

test("a deleted message and a quoted fragment", () => {
  // ADR 52 Q10: what it last said, marked deleted.
  const deleted = replyQuoteText(
    reply(),
    target({ deleted: true, text: "Last words" }),
    options,
  );
  expect([deleted.body, deleted.deleted]).toEqual(["Last words", true]);
  const quoted = replyQuoteText(
    reply({ reply_quote: " Authored\nfragment " }),
    target(),
    options,
  );
  expect([quoted.body, quoted.quoted]).toEqual(["Authored fragment", true]);
});

test("another chat, a story and an unknown message never jump", () => {
  const elsewhere = replyQuoteText(
    reply({
      reply_elsewhere: { peer: "channel:9", sender: "Anna", date: EARLIER },
    }),
    null,
    options,
  );
  expect(elsewhere).toMatchObject({
    kind: "elsewhere",
    sender: "Anna · in another chat",
    body: "Message",
    target: null,
  });
  expect(elsewhere.date).not.toBeNull();
  expect(
    replyQuoteText(
      reply({ reply_elsewhere: { sender_peer: "user:1" } }),
      null,
      options,
    ).sender,
  ).toBe("You · in another chat");
  expect(
    replyQuoteText(reply({ reply_story: true, reply_to: null }), null, options)
      .body,
  ).toBe("Reply to a story");
  expect(replyQuoteText(reply(), null, options).body).toBe(
    "Reply to an earlier message",
  );
  expect(
    replyQuoteText(reply(), null, { ...options, pending: true }).kind,
  ).toBe("pending");
  expect(replyTarget(reply({ reply_elsewhere: { peer: "chat:3" } }))).toBe(
    null,
  );
});

test("a merged bubble quotes each outside message once, in reply order", () => {
  const messages: QuoteReply[] = [
    { message_id: 30, date: NOW },
    { message_id: 31, date: NOW, reply_to: 5 },
    { message_id: 32, date: NOW, reply_to: 30 },
    { message_id: 33, date: NOW, reply_to: 5 },
    {
      message_id: 34,
      date: NOW,
      reply_to: 5,
      reply_elsewhere: { peer: "channel:9" },
    },
    { message_id: 35, date: NOW, reply_story: true },
  ];
  expect(outsideReplies(messages).map((m) => m.message_id)).toEqual([
    31, 34, 35,
  ]);
});
