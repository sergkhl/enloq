import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react-native";
// The module doubles first, so these imports get them.
import {
  mockAudio,
  mockClipboard,
  mockFiles,
  mockImages,
  mockLottie,
  mockParams,
  mockStore,
  mockVideo,
  reset,
  serve,
} from "./setup";
import { File } from "expo-file-system";
import * as Sharing from "expo-sharing";
import React from "react";
import { AccessibilityInfo } from "react-native";
import ConversationRoute from "../app/chat/[account]/[peer]";
import { ConnectionProvider } from "../src/connection";
import type { NativeMessage, Snapshot } from "../src/conversation";
import { showRows } from "../src/motion";
import { stopPlayback } from "../src/playback";
import { waveformBars } from "../src/VoiceNote";

// ADR 46 CU1: the app plays voice notes, video messages, videos, GIFs and
// stickers, and shares files and transcripts from the long-press sheet.

const ORIGINALS =
  "https://enloq.example.com/api/accounts/1/chats/user%3A101/originals";
const WAVE = [0, 4, 8, 16, 31, 16, 8, 4];

function message(id: number, extra = {}): NativeMessage {
  return {
    account_id: 1,
    peer: "user:101",
    message_id: id,
    revision: 1,
    date: 1_700_000_000 + id * 60,
    text: "",
    outgoing: false,
    service: false,
    deleted: false,
    media: null,
    ...extra,
  } as unknown as NativeMessage;
}

const voice = (id: number, duration: number) =>
  message(id, {
    media: "audio",
    media_info: { voice: true, duration, waveform: WAVE },
  });

function asset(format: string, extra = {}) {
  return {
    state: "ready",
    kind: format === "mp4" ? "gif" : "sticker",
    format,
    mime: null,
    width: 320,
    height: 240,
    duration: null,
    fps: null,
    frames: null,
    renderer_supported: true,
    features: [],
    revision: 1,
    version: null,
    ...extra,
  };
}

describe("media in the conversation", () => {
  let served: Snapshot;
  let prepared: string[];
  let calls: ReturnType<typeof serve>;

  beforeEach(() => {
    reset();
    stopPlayback();
    mockImages.length = 0;
    mockLottie.length = 0;
    mockVideo.players.length = 0;
    prepared = [];
    mockStore.set(
      "enloq.credential",
      JSON.stringify({ origin: "https://enloq.example.com", token: "t0ken" }),
    );
    mockParams.account = "1";
    mockParams.peer = "user:101";
    served = {
      timezone: "UTC",
      chat: { account_id: 1, peer: "user:101", title: "Anna", photo_id: null },
      messages: [
        voice(1, 2),
        message(2, { text: "Between the notes" }),
        voice(3, 5),
        message(5, {
          media: "video_note",
          media_info: { duration: 3, width: 240, height: 240 },
        }),
        message(6, {
          media: "video",
          media_info: {
            duration: 65,
            size: 3_100_000,
            width: 640,
            height: 360,
          },
        }),
        message(7, {
          media: "video",
          media_info: { duration: 900, size: 60 * 1024 * 1024 },
        }),
        message(9, { media: "gif", media_info: { duration: 3, size: 90000 } }),
        message(10, {
          media: "sticker",
          media_info: { sticker: "static", emoji: "🙂", size: 3000 },
        }),
        message(11, {
          media: "sticker",
          media_info: { sticker: "animated", emoji: "🎉", size: 3000 },
        }),
        message(12, {
          media: "sticker",
          media_info: { sticker: "video", emoji: "😺", size: 3000 },
        }),
        message(13, {
          media: "document",
          media_info: { file_name: "report.pdf", size: 48213 },
        }),
      ],
      blocks: [],
      unread_sources: [],
    } as unknown as Snapshot;
    calls = serve((url, init) => {
      if (url.endsWith("/api/accounts"))
        return {
          status: 200,
          body: {
            mode: "personal",
            timezone: "UTC",
            budget: [],
            login_available: true,
            accounts: [],
          },
        };
      if (url.endsWith("/preferences/reading-view"))
        return {
          status: 200,
          body: {
            reading_view:
              init.method === "POST"
                ? JSON.parse(init.body!).reading_view
                : "originals",
          },
        };
      if (url.endsWith("/chats/user%3A101"))
        return { status: 200, body: served };
      if (url.endsWith("/originals/1/transcript/status"))
        return {
          status: 200,
          body: {
            state: "done",
            transcript: {
              text: "Authored first note transcript.",
              language: "en",
            },
            reason: null,
          },
        };
      if (url.endsWith("/originals/1/transcript"))
        return {
          status: 200,
          body: { text: "Authored first note transcript.", language: "en" },
        };
      if (url.includes("/media/prepare-video")) {
        prepared.push(url);
        return {
          status: 200,
          body: {
            state: "ready",
            revision: 1,
            version: null,
            mime: "video/mp4",
          },
        };
      }
      if (url.endsWith("/9/media/representation?revision=1"))
        return { status: 200, body: asset("mp4", { state: "missing" }) };
      if (url.endsWith("/9/media/prepare-preview"))
        return { status: 200, body: asset("mp4") };
      if (url.endsWith("/10/media/representation?revision=1"))
        return { status: 200, body: asset("webp") };
      if (url.endsWith("/11/media/representation?revision=1"))
        return { status: 200, body: asset("tgs") };
      if (url.endsWith("/11/media/animation?revision=1"))
        return { status: 200, body: { v: "5.7.0", layers: [] } };
      if (url.includes("/ready-sources"))
        return { status: 200, body: { content: [], next_cursor: null } };
      if (url.endsWith("/notifications/attention"))
        return { status: 200, body: { events: [], sound: false } };
      throw new Error(`unexpected ${url}`);
    });
  });

  function mount() {
    return render(
      <ConnectionProvider>
        <ConversationRoute />
      </ConnectionProvider>,
    );
  }

  const player = () => mockAudio.players[0]!;
  const status = (extra = {}) =>
    act(() =>
      player().emit("playbackStatusUpdate", {
        playing: true,
        currentTime: 1,
        duration: 2,
        isBuffering: false,
        isLoaded: true,
        didJustFinish: false,
        error: null,
        ...extra,
      }),
    );
  // FlatList draws only its first rows at once: each case mounts its own.
  const only = (...ids: number[]) => {
    served.messages = served.messages.filter((item) =>
      ids.includes(item.message_id),
    );
  };
  const playFirst = async () => {
    // The inverted list draws the newest first: the earlier note is last.
    const buttons = await screen.findAllByLabelText(/^Play voice message/);
    await fireEvent.press(buttons.at(-1)!);
  };

  test("a voice note plays with the phone locked, at the phone's speed, and on into the next", async () => {
    only(1, 2, 3);
    await mount();
    await playFirst();
    // An iPhone plays the service's AAC copy (the tests run as iOS).
    expect(player().source).toEqual({
      uri: `${ORIGINALS}/1/media?revision=1&variant=aac`,
      headers: { Authorization: "Bearer t0ken" },
    });
    expect(player().playing).toBe(true);
    expect(mockAudio.modes.at(-1)).toEqual({
      playsInSilentMode: true,
      shouldPlayInBackground: true,
      interruptionMode: "doNotMix",
    });
    expect(player().lockScreen).toEqual({
      title: "Voice message",
      artist: "Anna",
    });
    await status();
    expect(screen.getByLabelText(/^Pause voice message/)).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Playback speed 1×"));
    expect(player().rate).toBe(1.5);
    expect(mockFiles.get("file:///document/playback.json")).toBe(
      JSON.stringify({ speed: 1.5 }),
    );
    expect(screen.getByLabelText("Playback speed 1.5×")).toBeTruthy();
    // The next voice note plays on, past the text between them.
    await status({ didJustFinish: true, playing: false });
    expect(player().source).toEqual({
      uri: `${ORIGINALS}/3/media?revision=1&variant=aac`,
      headers: { Authorization: "Bearer t0ken" },
    });
    expect(player().rate).toBe(1.5);
    // The last one ends the playback and leaves the lock screen.
    await status({ didJustFinish: true, playing: false });
    expect(player().playing).toBe(false);
    expect(player().lockScreen).toBeNull();
  });

  test("seeking moves a playing note, and changing the reading view stops it", async () => {
    only(1, 2, 3);
    await mount();
    await playFirst();
    await status({ currentTime: 0.5 });
    const positions = screen.getAllByLabelText("Voice message position");
    await fireEvent(positions.at(-1)!, "accessibilityAction", {
      nativeEvent: { actionName: "increment" },
    });
    expect(player().seeks.at(-1)).toBe(2);
    await fireEvent.press(
      screen.getByRole("togglebutton", { name: "AI view" }),
    );
    await waitFor(() => expect(player().playing).toBe(false));
    expect(player().lockScreen).toBeNull();
  });

  test("a video message plays in its circle and takes the slot from a voice note", async () => {
    only(1, 5);
    await mount();
    await playFirst();
    await fireEvent.press(await screen.findByLabelText("Play video message"));
    const circle = mockVideo.players.find((item) =>
      JSON.stringify(item.source).includes("/originals/5/media?revision=1"),
    )!;
    expect(circle.playing).toBe(true);
    expect(player().playing).toBe(false);
    expect(screen.getByTestId("video-view")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Pause video message"));
    expect(circle.playing).toBe(false);
  });

  test("a video prepares on Play and plays with controls; one over 50 MiB says so", async () => {
    only(6, 7);
    await mount();
    expect(
      await screen.findByText(
        "Video is over the 50 MiB limit. Open Telegram to view it.",
      ),
    ).toBeTruthy();
    expect(screen.getAllByLabelText("Play video")).toHaveLength(1);
    // Play opens the chat's Media Carousel, which prepares and plays it
    // (ADR 51 Q11).
    await fireEvent.press(screen.getByLabelText("Play video"));
    // Both videos are in it, the oversized one too.
    expect(await screen.findByText("1 / 2")).toBeTruthy();
    await waitFor(() =>
      expect(prepared).toEqual([`${ORIGINALS}/6/media/prepare-video`]),
    );
    expect(await screen.findByTestId("video-controls")).toBeTruthy();
    const video = mockVideo.players.find((item) =>
      JSON.stringify(item.source).includes("/originals/6/media?revision=1"),
    )!;
    expect(video.playing).toBe(true);
    await fireEvent.press(screen.getByLabelText("Close"));
    await waitFor(() => expect(screen.queryByText("1 / 2")).toBeNull());
  });

  test("an album is one grid, and its photos open the carousel there", async () => {
    // ADR 51 Q10, Q12: three photos sent together, then one of its own.
    served = {
      ...served,
      messages: [
        message(20, { media: "image", album: "42", text: "At the lake" }),
        message(21, { media: "image", album: "42" }),
        message(22, { media: "image", album: "42" }),
        message(23, { media: "image" }),
      ],
    };
    await mount();
    const album = await screen.findByLabelText("Album, 3 items");
    expect(album).toBeTruthy();
    expect(screen.getByText("At the lake")).toBeTruthy();
    expect(screen.getAllByLabelText("Photo, opens full screen")).toHaveLength(
      4,
    );
    await fireEvent.press(
      within(album).getAllByLabelText("Photo, opens full screen")[1]!,
    );
    expect(await screen.findByText("2 / 4")).toBeTruthy();
    await fireEvent.press(screen.getByLabelText("Close"));
    await waitFor(() => expect(screen.queryByText("2 / 4")).toBeNull());
  });

  test("GIFs and stickers load on screen; GIFs loop, TGS plays and video stickers stay still", async () => {
    only(9, 10, 11, 12);
    await mount();
    await screen.findByLabelText("GIF");
    // Nothing loads until the rows are on screen.
    expect(calls.some((call) => call.url.includes("representation"))).toBe(
      false,
    );
    await act(async () => showRows("1/user:101", ["m9", "m10", "m11", "m12"]));
    await waitFor(() =>
      expect(
        mockVideo.players.some((item) =>
          JSON.stringify(item.source).includes("/9/media/preview?revision=1"),
        ),
      ).toBe(true),
    );
    const gif = mockVideo.players.find((item) =>
      JSON.stringify(item.source).includes("/9/media/preview"),
    )!;
    expect([gif.loop, gif.muted, gif.playing]).toEqual([true, true, true]);
    await waitFor(() =>
      expect(mockImages.map((image) => image.uri)).toContain(
        `${ORIGINALS}/10/media/preview?revision=1`,
      ),
    );
    expect(await screen.findByTestId("lottie")).toBeTruthy();
    expect(mockLottie).toContain("play");
    expect(mockImages.map((image) => image.uri)).toContain(
      `${ORIGINALS}/12/media?revision=1&variant=poster`,
    );
    expect(
      calls.some((call) => call.url.includes("/12/media/representation")),
    ).toBe(false);
    expect(screen.getByLabelText("Sticker 🎉")).toBeTruthy();
  });

  test("with reduce motion on, GIFs and animated stickers hold still", async () => {
    jest
      .spyOn(AccessibilityInfo, "isReduceMotionEnabled")
      .mockResolvedValue(true);
    only(9, 11);
    await mount();
    await screen.findByLabelText("GIF");
    await act(async () => showRows("1/user:101", ["m9", "m11"]));
    expect(await screen.findByTestId("lottie")).toBeTruthy();
    await waitFor(() =>
      expect(
        mockVideo.players.some((item) =>
          JSON.stringify(item.source).includes("/9/media/preview"),
        ),
      ).toBe(true),
    );
    const gif = mockVideo.players.find((item) =>
      JSON.stringify(item.source).includes("/9/media/preview"),
    )!;
    expect(gif.playing).toBe(false);
    expect(mockLottie).not.toContain("play");
    jest.restoreAllMocks();
  });

  test("the sheet copies a voice note's transcript and shares a file", async () => {
    const download = File.downloadFileAsync as unknown as jest.Mock;
    download.mockResolvedValue({
      uri: "file:///cache/enloq/shared/report.pdf",
    });
    only(1, 13);
    await mount();
    await fireEvent(
      (await screen.findAllByLabelText(/^Play voice message/)).at(-1)!,
      "longPress",
    );
    await fireEvent.press(
      screen.getByRole("menuitem", { name: "Copy transcript" }),
    );
    await waitFor(() =>
      expect(mockClipboard).toEqual(["Authored first note transcript."]),
    );
    expect(await screen.findByText("Transcript copied")).toBeTruthy();
    await fireEvent(screen.getByLabelText("report.pdf, 47 KB"), "longPress");
    await fireEvent.press(screen.getByRole("menuitem", { name: "Share" }));
    await waitFor(() => expect(Sharing.shareAsync).toHaveBeenCalled());
    expect(download.mock.calls.at(-1)![0]).toBe(
      `${ORIGINALS}/13/media?revision=1&download=1`,
    );
    expect(download.mock.calls.at(-1)![2]).toEqual({
      headers: { Authorization: "Bearer t0ken" },
      idempotent: true,
    });
    expect(Sharing.shareAsync).toHaveBeenLastCalledWith(
      "file:///cache/enloq/shared/report.pdf",
    );
    // The card itself opens Share too.
    await fireEvent.press(screen.getByLabelText("report.pdf, 47 KB"));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
  });
});

test("waveform bars keep each bin's peak over the loudest", () => {
  expect(waveformBars(WAVE, 4)).toEqual([4 / 31, 16 / 31, 1, 8 / 31]);
  expect(waveformBars(null, 3)).toEqual([0.25, 0.25, 0.25]);
});
