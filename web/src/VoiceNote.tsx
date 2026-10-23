import React, { createContext, useContext, useRef, useState } from "react";
import { LoaderCircle, Pause, Play } from "lucide-react";
import type { NativeMessage } from "./conversation-api";
import { mediaUrl } from "./MediaFiles";
import {
  SPEEDS,
  playsOggOpus,
  seekVoice,
  setVoiceSpeed,
  toggleVoice,
  useVoice,
  type VoiceSource,
} from "./playback";

// The open chat's title, for a phone's lock screen while a voice note plays.
export const ChatTitle = createContext("");

const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value));

// m:ss, as Telegram shows a voice note's length.
export function clock(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// Telegram's waveform (levels 0-31) in `count` bars, each its bin's peak over
// the loudest; flat bars without one (the app's rule).
export function waveformBars(
  waveform: number[] | null | undefined,
  count: number,
) {
  if (!waveform?.length) return Array<number>(count).fill(0.25);
  const peak = Math.max(1, ...waveform);
  return Array.from({ length: count }, (_, index) => {
    const start = Math.floor((index * waveform.length) / count);
    const end = Math.max(
      start + 1,
      Math.floor(((index + 1) * waveform.length) / count),
    );
    return Math.max(...waveform.slice(start, end)) / peak;
  });
}

// A voice note for the shared player; browsers without Ogg Opus play the
// service's AAC copy.
export function voiceSource(
  account: number,
  peer: string,
  message: NativeMessage,
  chat: string,
): VoiceSource {
  return {
    key: `${account}/${peer}/${message.message_id}@${message.revision}`,
    url: `${mediaUrl(account, peer, message)}${playsOggOpus() ? "" : "&variant=aac"}`,
    chat,
  };
}

// A voice note (ADR 46 CU1), as the app shows it: play or pause, the waveform
// to tap or drag to a place, its time, and the speed once it has started.
export function VoiceNote({
  account,
  peer,
  message,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
}) {
  const chat = useContext(ChatTitle);
  const note = voiceSource(account, peer, message, chat);
  const voice = useVoice(note.key);
  const [drag, setDrag] = useState<number | null>(null);
  const wave = useRef<HTMLDivElement>(null);
  const length = message.media_info?.duration ?? 0;
  const width = clamp(72 + 5 * length, 96, 200);
  const bars = waveformBars(
    message.media_info?.waveform,
    clamp(Math.round(width / 4), 16, 50),
  );
  const active = voice.key !== null;
  const total = (active && voice.duration) || length;
  const position = drag !== null ? drag * total : voice.position;
  const played = total ? position / total : 0;
  const place = (event: React.PointerEvent) => {
    const box = wave.current!.getBoundingClientRect();
    return clamp((event.clientX - box.left) / box.width, 0, 1);
  };
  const seek = (seconds: number) => seekVoice(note, clamp(seconds, 0, total));
  return (
    <div className="voice-note">
      <button
        type="button"
        className="voice-play"
        aria-label={`${voice.playing ? "Pause" : "Play"} voice message, ${clock(length || total)}`}
        onClick={() => toggleVoice(note)}
      >
        {voice.waiting ? (
          <LoaderCircle size={20} className="spin" aria-hidden />
        ) : voice.playing ? (
          <Pause size={20} aria-hidden />
        ) : (
          <Play size={20} aria-hidden />
        )}
      </button>
      <div className="voice-column">
        <div
          ref={wave}
          className="voice-wave"
          style={{ width }}
          role="slider"
          tabIndex={0}
          aria-label="Voice message position"
          aria-valuemin={0}
          aria-valuemax={Math.round(total)}
          aria-valuenow={Math.round(position)}
          aria-valuetext={`${clock(position)} of ${clock(total)}`}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            setDrag(place(event));
          }}
          onPointerMove={(event) => {
            if (drag !== null) setDrag(place(event));
          }}
          onPointerUp={(event) => {
            if (drag === null) return;
            setDrag(null);
            seek(place(event) * total);
          }}
          onPointerCancel={() => setDrag(null)}
          onKeyDown={(event) => {
            const to = {
              ArrowRight: position + 5,
              ArrowUp: position + 5,
              ArrowLeft: position - 5,
              ArrowDown: position - 5,
              Home: 0,
              End: total,
            }[event.key];
            if (to === undefined) return;
            event.preventDefault();
            seek(to);
          }}
        >
          {bars.map((level, index) => (
            <span
              key={index}
              className={index / bars.length < played ? "played" : undefined}
              style={{ height: Math.max(2, level * 22) }}
            />
          ))}
        </div>
        <div className="voice-meta">
          <span className="voice-time">
            {voice.failed
              ? "Unavailable"
              : clock(active || drag !== null ? position : total)}
          </span>
          {active && (
            <button
              type="button"
              className="voice-speed"
              aria-label={`Playback speed ${voice.speed}×`}
              onClick={() =>
                setVoiceSpeed(
                  SPEEDS[(SPEEDS.indexOf(voice.speed) + 1) % SPEEDS.length]!,
                )
              }
            >
              {voice.speed}×
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
