import { useSyncExternalStore } from "react";

// One recording at a time (ADR 46 CU1), as in the app. Voice notes share one
// audio element; a video message or a video takes the slot from it, or it
// from them.
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
  url: string;
  // What a phone's lock screen shows: the chat's title.
  chat: string;
};

export type VoiceState = {
  key: string | null;
  playing: boolean;
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
// The browser's own speed, as each phone keeps its own (ADR 46 CU1).
const SPEED_KEY = "reply-assistant.playback-speed";

function savedSpeed(): Speed {
  try {
    const value = Number(localStorage.getItem(SPEED_KEY));
    if (SPEEDS.includes(value as Speed)) return value as Speed;
  } catch {
    // Without storage the speed starts at 1×.
  }
  return 1;
}

let audio: HTMLAudioElement | null = null;
let current: VoiceSource | null = null;
let state = IDLE;
let speed: Speed | null = null;
// A seek asked for before the new source loaded.
let pending: number | null = null;
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

// Browsers that can't play Ogg Opus ask for the service's AAC copy.
let oggOpus: boolean | null = null;
export function playsOggOpus() {
  oggOpus ??= new Audio().canPlayType('audio/ogg; codecs="opus"') !== "";
  return oggOpus;
}

function update() {
  if (!audio || !current) return;
  publish({
    key: current.key,
    playing: !audio.paused,
    position: audio.currentTime,
    duration: Number.isFinite(audio.duration) ? audio.duration : 0,
    waiting: !audio.paused && audio.readyState < 3,
    failed: !!audio.error,
  });
}

function shared() {
  if (audio) return audio;
  const element = new Audio();
  element.preload = "auto";
  for (const name of [
    "timeupdate",
    "durationchange",
    "play",
    "pause",
    "waiting",
    "playing",
    "error",
  ])
    element.addEventListener(name, update);
  element.addEventListener("loadedmetadata", () => {
    if (pending !== null) element.currentTime = pending;
    pending = null;
  });
  element.addEventListener("ended", () => {
    const list = queue();
    const index = list.findIndex((note) => note.key === current?.key);
    const next = index >= 0 ? list[index + 1] : undefined;
    if (next) playVoice(next);
    else stopVoice();
  });
  audio = element;
  return element;
}

export function voiceSpeed(): Speed {
  speed ??= savedSpeed();
  return speed;
}

export function setVoiceSpeed(next: Speed) {
  speed = next;
  try {
    localStorage.setItem(SPEED_KEY, String(next));
  } catch {
    // The speed still applies until the page closes.
  }
  if (audio) audio.playbackRate = next;
  publish({ ...state });
}

export function playVoice(note: VoiceSource, at?: number) {
  const element = shared();
  claimPlayback("voice", pauseVoice);
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
    element.src = note.url;
    // A new source resets the rate.
    element.defaultPlaybackRate = voiceSpeed();
    element.playbackRate = voiceSpeed();
    if ("mediaSession" in navigator)
      navigator.mediaSession.metadata = new MediaMetadata({
        title: "Voice message",
        artist: note.chat,
      });
  } else if (at !== undefined) element.currentTime = at;
  void element.play().catch(() => update());
}

export function pauseVoice() {
  audio?.pause();
}

export function toggleVoice(note: VoiceSource) {
  if (current?.key === note.key && state.playing) pauseVoice();
  else playVoice(note);
}

export function seekVoice(note: VoiceSource, seconds: number) {
  if (current?.key !== note.key || state.failed)
    return playVoice(note, seconds);
  if (audio) audio.currentTime = seconds;
  publish({ ...state, position: seconds });
}

export function stopVoice() {
  releasePlayback("voice");
  if (audio) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
  current = null;
  pending = null;
  publish(IDLE);
}

// This note's playback: its state while it is the current one, else idle.
export function useVoice(key: string): VoiceState & { speed: Speed } {
  const mine = useSyncExternalStore(subscribe, () =>
    state.key === key ? state : IDLE,
  );
  return { ...mine, speed: voiceSpeed() };
}

// Leaving the page stops playback, as leaving the chat does.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => stopPlayback());
  window.addEventListener("browser-auth-lost", () => stopPlayback());
}
