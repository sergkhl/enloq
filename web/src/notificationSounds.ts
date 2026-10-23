// The web's one chime (ADR 44's derived design: the four other presets and
// the custom sound went with the old Settings). The service may still name
// another preset, which plays as this one.
type SoundPreset = "harp" | "bell" | "chime" | "pop" | "digital" | "custom";
export type SoundSettings = {
  sound: boolean;
  sound_preset: SoundPreset;
  sound_file_name: string | null;
  sound_revision: string | null;
};
type Read = { epoch: number; sequence: number };
type Mutation = { epoch: number; lifetime: number; previous: SoundSettings };
const harp = new URL("./assets/notification-harp.mp3", import.meta.url).href;
let settings: SoundSettings = {
  sound: true,
  sound_preset: "harp",
  sound_file_name: null,
  sound_revision: null,
};
let audio: AudioContext | null = null;
let epoch = 0;
let lifetime = 0;
let sequence = 0;
let accepted = 0;
let mutating: Mutation | null = null;
let playback = 0;
let source: AudioBufferSourceNode | null = null;
const buffers = new Map<string, Promise<AudioBuffer>>();
export const soundSettingsChanged = () =>
  window.dispatchEvent(new Event("notification-settings-changed"));

export function audioState() {
  return audio?.state === "running"
    ? "Ready"
    : "Click in the app to enable sound";
}
export function audioReady() {
  return audio?.state === "running";
}
function context() {
  if (!audio || audio.state === "closed") {
    audio = new AudioContext();
    audio.onstatechange = soundSettingsChanged;
  }
  return audio;
}
export async function unlockSound() {
  if (!settings.sound) return;
  try {
    await context().resume();
    soundSettingsChanged();
  } catch {
    /* Keep observable autoplay blocking; no duplicate playback fallback. */
  }
}
export function cancelSound() {
  ++playback;
  const prior = source;
  source = null;
  if (prior) {
    prior.onended = null;
    try {
      prior.stop();
    } catch {
      /* A source may already have ended. */
    }
    prior.disconnect();
  }
}
function apply(value: SoundSettings) {
  if (value.sound !== settings.sound) cancelSound();
  settings = value;
}
export function soundRead(): Read {
  return { epoch, sequence: ++sequence };
}
export function soundReadActive(read: Read) {
  return !mutating && read.epoch === epoch;
}
export function soundReadCurrent(read: Read) {
  return soundReadActive(read) && read.sequence >= accepted;
}
// Metadata shares sequence ordering across Attention and Settings. Events only
// need the same owner/preference epoch; a newer unchanged metadata poll must
// not discard a queued event returned by a slower Attention request.
export function acceptSoundSettings(value: SoundSettings, read: Read) {
  if (!soundReadCurrent(read)) return false;
  accepted = read.sequence;
  apply(value);
  return true;
}
export function beginSoundMutation(patch: Partial<SoundSettings> = {}) {
  const mutation = { epoch: ++epoch, lifetime, previous: settings };
  mutating = mutation;
  apply({ ...settings, ...patch });
  return mutation;
}
export function finishSoundMutation(mutation: Mutation, value?: SoundSettings) {
  if (mutating !== mutation || mutation.lifetime !== lifetime) return false;
  apply(value || mutation.previous);
  mutating = null;
  ++epoch;
  return true;
}
// BrowserGate can replace the trusted App without reloading this module.
// Settings closing alone keeps its owner's accepted save; App cleanup fences
// every old owner's read, mutation, decode and custom buffer instead.
export function resetNotificationSounds() {
  ++lifetime;
  ++epoch;
  mutating = null;
  cancelSound();
  buffers.clear();
  settings = {
    sound: true,
    sound_preset: "harp",
    sound_file_name: null,
    sound_revision: null,
  };
  const prior = audio;
  audio = null;
  if (prior) {
    prior.onstatechange = null;
    void prior.close().catch(() => {});
  }
}
function decoded(owner: AudioContext) {
  let buffer = buffers.get(harp);
  if (!buffer) {
    buffer = fetch(harp, { credentials: "same-origin" })
      .then((response) => {
        if (!response.ok) throw new Error("Sound unavailable.");
        return response.arrayBuffer();
      })
      .then((bytes) => owner.decodeAudioData(bytes))
      .catch((error) => {
        if (buffers.get(harp) === buffer) buffers.delete(harp);
        throw error;
      });
    buffers.set(harp, buffer);
  }
  return buffer;
}
async function play() {
  if (!settings.sound || !audioReady()) return false;
  cancelSound();
  const version = playback;
  const owner = audio;
  if (!owner || owner.state !== "running") return false;
  const buffer = await decoded(owner);
  if (version !== playback || !settings.sound || owner.state !== "running")
    return false;
  const playing = owner.createBufferSource();
  source = playing;
  playing.buffer = buffer;
  playing.connect(owner.destination);
  playing.onended = () => {
    playing.disconnect();
    if (source === playing) source = null;
  };
  try {
    playing.start();
  } catch (error) {
    playing.onended = null;
    playing.disconnect();
    if (source === playing) source = null;
    throw error;
  }
  return true;
}
export async function chime() {
  try {
    return await play();
  } catch {
    return false;
  }
}
