import { buildLine } from "../src/build";

// ADR 43 IB2: the account sheet names the Saved Build it runs.
test("a Saved Build shows its version, commit and build date", () => {
  expect(
    buildLine("0.1.0", { commit: "1a2b3c4", time: "2026-10-16T16:40:12Z" }),
  ).toBe("Enloq 0.1.0 · 1a2b3c4 · 2026-10-16");
  expect(buildLine("0.1.0", { commit: "1a2b3c4" })).toBe(
    "Enloq 0.1.0 · 1a2b3c4",
  );
});

test("a build without a commit is a development build", () => {
  expect(buildLine("0.1.0")).toBe("Enloq 0.1.0 · development build");
  expect(buildLine("0.1.0", { commit: "", time: "2026-10-16T00:00:00Z" })).toBe(
    "Enloq 0.1.0 · development build",
  );
});
