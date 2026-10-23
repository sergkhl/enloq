import {
  createAudioPlayer,
  setAudioModeAsync,
  type AudioPlayer,
  type AudioStatus,
} from "expo-audio";
import { File, Paths } from "expo-file-system";
import { useSyncExternalStore } from "react";

// One recording at a time (ADR 46 CU1). Voice notes share one audio player;
// a video message or a video takes the slot from it, or it from them.
type Owner = { key: string; stop: () => void };
let owner: Owner | null = null;

export function claimPlayback(key: string, stop: () => void) {
  if (owner && owner.key !== key) owner.stop();
  owner = { key, stop };
}

export function releasePlayback(key: string) {
  if (owner?.key === key) owner = null;
}

// Leaving the chat or changing the reading view stops whatever plays, and
// videos forget the positions they kept while the chat was open.
export function stopPlayback() {
  const playing = owner;
  owner = null;
  playing?.stop();
  stopVoice();
  positions.clear();
}

// A video's position while its chat stays open.
export const positions = new Map<string, number>();

export type VoiceSource = {
  // `${account}/${peer}/${message_id}@${revision}`
  key: string;
  uri: string;
  headers: Record<string, string>;
  // What the lock screen shows: the chat's title.
  chat: string;
};

export type VoiceState = {
  key: string | null;
  playing: boolean;
  // Seconds; `duration` is 0 until the player knows it.
  position: number;
  duration: number;
  waiting: boolean;
  failed: boolean;
};

const IDLE: VoiceState = {
  key: null,
  playing: false,
  position: 0,
  duration: 0,
  waiting: false,
  failed: false,
};

export const SPEEDS = [1, 1.5, 2] as const;
export type Speed = (typeof SPEEDS)[number];

// The speed is the phone's own (ADR 46 CU1), kept beside the app's documents.
const settings = () => new File(Paths.document, "playback.json");

function savedSpeed(): Speed {
  try {
    const file = settings();
    if (file.exists) {
      const value = (JSON.parse(file.textSync()) as { speed?: unknown }).speed;
      if (SPEEDS.includes(value as Speed)) return value as Speed;
    }
  } catch {
    // An unreadable file is the default speed.
  }
  return 1;
}

let player: AudioPlayer | null = null;
let current: VoiceSource | null = null;
let state = IDLE;
let speed: Speed | null = null;
let modeSet = false;
// A seek asked for before the new source loaded.
let pending: number | null = null;
// Play was asked for: a note still loading shows as playing, not paused.
let wanted = false;
// The chat's voice notes in order, for playing on (CU1).
let queue: () => VoiceSource[] = () => [];
const listeners = new Set<() => void>();

function publish(next: VoiceState) {
  state = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setVoiceQueue(read: () => VoiceSource[]) {
  queue = read;
}

function update(status: AudioStatus) {
  if (!current) return;
  if (pending !== null && status.isLoaded) {
    void player?.seekTo(pending).catch(() => undefined);
    pending = null;
  }
  if (status.didJustFinish) {
    const list = queue();
    const index = list.findIndex((note) => note.key === current?.key);
    const next = index >= 0 ? list[index + 1] : undefined;
    if (next) void playVoice(next);
    else stopVoice();
    return;
  }
  if (status.error) wanted = false;
  publish({
    key: current.key,
    playing: status.playing || (wanted && !status.isLoaded),
    position: status.currentTime,
    duration: status.duration,
    waiting: wanted && (status.isBuffering || !status.isLoaded),
    failed: !!status.error,
  });
}

function shared() {
  if (!player) {
    player = createAudioPlayer(null, { updateInterval: 200 });
    player.addListener("playbackStatusUpdate", update);
  }
  return player;
}

export function voiceSpeed(): Speed {
  speed ??= savedSpeed();
  return speed;
}

export function setVoiceSpeed(next: Speed) {
  speed = next;
  try {
    settings().write(JSON.stringify({ speed: next }));
  } catch {
    // The speed still applies until the app closes.
  }
  player?.setPlaybackRate(next);
  publish({ ...state });
}

export async function playVoice(note: VoiceSource, at?: number) {
  const audio = shared();
  claimPlayback("voice", pauseVoice);
  if (!modeSet) {
    modeSet = true;
    // Voice notes keep playing with the phone locked or another app open.
    await setAudioModeAsync({
      playsInSilentMode: true,
      shouldPlayInBackground: true,
      interruptionMode: "doNotMix",
    }).catch(() => {
      modeSet = false;
    });
  }
  if (current?.key !== note.key || state.failed) {
    current = note;
    pending = at ?? null;
    publish({
      ...IDLE,
      key: note.key,
      playing: true,
      position: at ?? 0,
      waiting: true,
    });
    audio.replace({ uri: note.uri, headers: note.headers });
    audio.setPlaybackRate(voiceSpeed());
    audio.setActiveForLockScreen(true, {
      title: "Voice message",
      artist: note.chat,
    });
  } else if (at !== undefined) await audio.seekTo(at).catch(() => undefined);
  wanted = true;
  audio.play();
}

export function pauseVoice() {
  wanted = false;
  player?.pause();
  if (current) publish({ ...state, playing: false, waiting: false });
}

export function toggleVoice(note: VoiceSource) {
  if (current?.key === note.key && state.playing) pauseVoice();
  else void playVoice(note);
}

export function seekVoice(note: VoiceSource, seconds: number) {
  if (current?.key !== note.key || state.failed)
    return void playVoice(note, seconds);
  void player?.seekTo(seconds).catch(() => undefined);
  publish({ ...state, position: seconds });
}

export function stopVoice() {
  releasePlayback("voice");
  if (player) {
    player.pause();
    player.clearLockScreenControls();
  }
  current = null;
  pending = null;
  wanted = false;
  publish(IDLE);
}

// This note's playback: its state while it is the current one, else idle.
export function useVoice(key: string): VoiceState & { speed: Speed } {
  const mine = useSyncExternalStore(subscribe, () =>
    state.key === key ? state : IDLE,
  );
  return { ...mine, speed: voiceSpeed() };
}
