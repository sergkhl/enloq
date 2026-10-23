import {
  showTranscript,
  type TranscriptStatus,
  type TranscriptView,
} from "../../shared/transcripts";

// ADR 51 Q9: Show transcript starts a missing or failed transcript, follows it
// while it runs and says why when it can't; Saved Messages only reads.

const none: TranscriptStatus = {
  state: "none",
  transcript: null,
  reason: null,
};
const running: TranscriptStatus = {
  state: "running",
  transcript: null,
  reason: null,
};
const done: TranscriptStatus = {
  state: "done",
  transcript: { text: "Authored spoken words.", language: "en" },
  reason: null,
};

async function tap(
  reads: TranscriptStatus[],
  { start = running as TranscriptStatus | Error, saved = false } = {},
) {
  const views: TranscriptView[] = [];
  const starts: number[] = [];
  await showTranscript({
    read: async () => reads.shift()!,
    start: async () => {
      starts.push(1);
      if (start instanceof Error) throw start;
      return start;
    },
    saved,
    show: (view) => views.push(view),
    alive: () => true,
    wait: async () => undefined,
  });
  return { views, starts: starts.length };
}

test("a missing transcript starts on the tap and shows once done", async () => {
  const { views, starts } = await tap([none, running, done]);
  expect(starts).toBe(1);
  expect(views).toEqual([
    { kind: "loading" },
    { kind: "running" },
    { kind: "running" },
    { kind: "text", text: "Authored spoken words." },
  ]);
});

test("a stored transcript shows at once", async () => {
  const { views, starts } = await tap([done]);
  expect(starts).toBe(0);
  expect(views.at(-1)).toEqual({
    kind: "text",
    text: "Authored spoken words.",
  });
});

test("a failure says so and offers another try", async () => {
  const failed: TranscriptStatus = {
    state: "failed",
    transcript: null,
    reason: "Transcription failed",
  };
  const { views } = await tap([none, failed]);
  expect(views.at(-1)).toEqual({
    kind: "note",
    note: "Transcription failed.",
    retry: true,
  });
});

test("with the account's AI off the tap says so", async () => {
  const { views } = await tap([none], {
    start: new Error("AI processing is Off for this account"),
  });
  expect(views.at(-1)).toEqual({
    kind: "note",
    note: "AI processing is Off for this account.",
    retry: false,
  });
});

test("Saved Messages only reads a transcript", async () => {
  const { views, starts } = await tap([none], { saved: true });
  expect(starts).toBe(0);
  expect(views.at(-1)).toEqual({
    kind: "note",
    note: "No transcript yet.",
    retry: false,
  });
});
