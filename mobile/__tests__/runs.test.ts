import {
  groupSender,
  messageRuns,
  nameColor,
  peerIndex,
  RUN_GAP,
} from "../../shared/runs";

// ADR 51 Q13: Message Runs as Telegram's phone apps group them.
const T = Date.UTC(2026, 9, 19, 9) / 1000;

test("a run breaks on another sender, a gap over five minutes, a new day or the unread line", () => {
  const places = messageRuns(
    [
      { sender: "in", date: T },
      { sender: "in", date: T + 60 },
      { sender: "out", date: T + 70 },
      { sender: "out", date: T + 70 + RUN_GAP },
      { sender: "out", date: T + 71 + 2 * RUN_GAP },
      { sender: "out", date: T + 80 + 2 * RUN_GAP, separated: true },
      { sender: "out", date: T + 86400 },
    ],
    "UTC",
  );
  expect(places).toEqual([
    { first: true, last: false },
    { first: false, last: true },
    { first: true, last: false },
    { first: false, last: true },
    { first: true, last: true },
    { first: true, last: true },
    { first: true, last: true },
  ]);
});

test("a group's sender is its author, and the owner's own user is out", () => {
  expect(groupSender({ author_peer: "user:42" }, 1)).toBe("user:42|");
  expect(groupSender({ author_peer: "user:1" }, 1)).toBe("out");
  expect(groupSender({ author: "Ann" }, 1)).toBe("name:Ann|");
  // An anonymous admin's posts split by signature.
  expect(
    groupSender({ author_peer: "channel:42", author_signature: "Mod" }, 1),
  ).toBe("channel:42|Mod");
});

test("names take Telegram's colour by ID, even beyond a double's precision", () => {
  expect(peerIndex("user:7")).toBe(0);
  expect(peerIndex("user:8")).toBe(1);
  expect(peerIndex("channel:-1001234567890")).toBe(1001234567890 % 7);
  expect(peerIndex("user:12345678901234567890")).toBe(
    Number(12345678901234567890n % 7n),
  );
  expect(peerIndex(null)).toBe(0);
  expect(nameColor("user:9", "light")).not.toBe(nameColor("user:9", "dark"));
});
