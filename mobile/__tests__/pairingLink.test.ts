import { parsePairingLink, serverName } from "../src/pairingLink";

const id = "0123456789abcdef0123456789abcdef";

test("accepts the exact link a server prints", () => {
  expect(
    parsePairingLink(` https://enloq.globesoul.com/?connect=${id}\n`),
  ).toEqual({
    origin: "https://enloq.globesoul.com",
    id,
  });
  expect(parsePairingLink(`http://127.0.0.1:8776/?connect=${id}`)).toEqual({
    origin: "http://127.0.0.1:8776",
    id,
  });
  expect(serverName("https://enloq.globesoul.com")).toBe("enloq.globesoul.com");
});

test.each([
  `http://enloq.globesoul.com/?connect=${id}`,
  `https://enloq.globesoul.com/?connect=${id.slice(1)}`,
  `https://enloq.globesoul.com/?connect=${id.toUpperCase()}`,
  `https://user@enloq.globesoul.com/?connect=${id}`,
  `https://enloq.globesoul.com/app?connect=${id}`,
  `https://enloq.globesoul.com/?connect=${id}&next=x`,
  "enloq.globesoul.com",
  "",
])("refuses %p", (text) => {
  expect(parsePairingLink(text)).toBeNull();
});

test("initials skip separators and keep two words", () => {
  const { initials } =
    require("../src/Avatar") as typeof import("../src/Avatar");
  expect(initials("Мария · документы")).toBe("МД");
  expect(initials("Alex · weekend plans")).toBe("AW");
  expect(initials("anna")).toBe("A");
  expect(initials("· 42")).toBe("?");
});
