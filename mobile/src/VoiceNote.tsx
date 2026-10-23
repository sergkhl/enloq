import Ionicons from "@expo/vector-icons/Ionicons";
import React, { createContext, useContext, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type GestureResponderEvent,
} from "react-native";
import { useBubbleMenu } from "./bubbleMenu";
import { useConnection } from "./connection";
import type { NativeMessage } from "./conversation";
import { voiceSource } from "./Media";
import {
  SPEEDS,
  seekVoice,
  setVoiceSpeed,
  toggleVoice,
  useVoice,
} from "./playback";
import type { Palette } from "./theme";

// The open chat's title, for the lock screen while a voice note plays.
export const ChatTitle = createContext("");

const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value));

// m:ss, as Telegram shows a voice note's length.
export function clock(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// Telegram's waveform (levels 0-31) in `count` bars, each its bin's peak over
// the loudest; flat bars without one.
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

// A voice note (ADR 46 CU1): play or pause, the waveform to tap or drag to a
// place, its time, and the speed once it has started.
export function VoiceNote({
  account,
  peer,
  message,
  palette,
  inverse,
}: {
  account: number;
  peer: string;
  message: NativeMessage;
  palette: Palette;
  inverse: boolean;
}) {
  const { connection } = useConnection();
  const chat = useContext(ChatTitle);
  const note =
    connection.status === "paired"
      ? voiceSource(connection.credential, account, peer, message, chat)
      : null;
  const voice = useVoice(note?.key ?? "");
  const menu = useBubbleMenu();
  const [drag, setDrag] = useState<number | null>(null);
  const start = useRef(0);
  const length = message.media_info?.duration ?? 0;
  const width = clamp(72 + 5 * length, 96, 200);
  const bars = waveformBars(
    message.media_info?.waveform,
    clamp(Math.round(width / 4), 16, 50),
  );
  if (!note) return null;
  const active = voice.key !== null;
  const total = (active && voice.duration) || length;
  const position = drag !== null ? drag * total : voice.position;
  const played = total ? position / total : 0;
  const ink = inverse ? palette.onAccent : palette.accent;
  const text = inverse ? palette.onAccent : palette.muted;

  // Where the finger is along the waveform, from 0 to 1.
  const place = (event: GestureResponderEvent) =>
    clamp((event.nativeEvent.pageX - start.current) / width, 0, 1);

  return (
    <View style={styles.row}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${voice.playing ? "Pause" : "Play"} voice message, ${clock(length || total)}`}
        onPress={() => toggleVoice(note)}
        {...menu}
        style={[styles.play, { backgroundColor: ink }]}
      >
        {voice.waiting ? (
          <ActivityIndicator
            color={inverse ? palette.accent : palette.onAccent}
          />
        ) : (
          <Ionicons
            name={voice.playing ? "pause" : "play"}
            size={20}
            color={inverse ? palette.accent : palette.onAccent}
          />
        )}
      </Pressable>
      <View style={styles.column}>
        <View
          accessible
          accessibilityRole="adjustable"
          accessibilityLabel="Voice message position"
          accessibilityValue={{ text: `${clock(position)} of ${clock(total)}` }}
          accessibilityActions={[
            { name: "increment", label: "Forward 5 seconds" },
            { name: "decrement", label: "Back 5 seconds" },
          ]}
          onAccessibilityAction={(event) =>
            seekVoice(
              note,
              clamp(
                position +
                  (event.nativeEvent.actionName === "increment" ? 5 : -5),
                0,
                total,
              ),
            )
          }
          onStartShouldSetResponder={() => true}
          onMoveShouldSetResponder={() => true}
          onResponderTerminationRequest={() => false}
          onResponderGrant={(event) => {
            start.current =
              event.nativeEvent.pageX - event.nativeEvent.locationX;
            setDrag(place(event));
          }}
          onResponderMove={(event) => setDrag(place(event))}
          onResponderRelease={(event) => {
            setDrag(null);
            seekVoice(note, place(event) * total);
          }}
          onResponderTerminate={() => setDrag(null)}
          style={[styles.wave, { width }]}
        >
          {bars.map((level, index) => (
            <View
              key={index}
              pointerEvents="none"
              style={[
                styles.bar,
                {
                  height: Math.max(2, level * 22),
                  backgroundColor: ink,
                  opacity: index / bars.length < played ? 1 : 0.45,
                },
              ]}
            />
          ))}
        </View>
        <View style={styles.meta}>
          <Text style={[styles.time, { color: text }]}>
            {voice.failed
              ? "Unavailable"
              : clock(active || drag !== null ? position : total)}
          </Text>
          {active ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Playback speed ${voice.speed}×`}
              hitSlop={8}
              onPress={() =>
                setVoiceSpeed(
                  SPEEDS[(SPEEDS.indexOf(voice.speed) + 1) % SPEEDS.length]!,
                )
              }
              style={[styles.speed, { borderColor: text }]}
            >
              <Text style={[styles.speedText, { color: text }]}>
                {voice.speed}×
              </Text>
            </Pressable>
          ) : null}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  play: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
  },
  column: { gap: 4 },
  wave: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    height: 26,
  },
  bar: { width: 2, borderRadius: 1 },
  meta: { flexDirection: "row", alignItems: "center", gap: 8 },
  time: { fontSize: 12, fontVariant: ["tabular-nums"] },
  speed: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  speedText: { fontSize: 11, fontWeight: "700" },
});
