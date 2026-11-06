import Ionicons from "@expo/vector-icons/Ionicons";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import { router } from "expo-router";
import React, {
  useCallback,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
  useWindowDimensions,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  ACCOUNT_OFF,
  AI_CONTROLS,
  ASK_THIS_GROUP,
  HIDE_BOTS,
  PRIVATE_CONTROLS,
  answered,
  chatWrite,
  groupNote,
  hasSwitch,
  robotLabel,
  serverNote,
  switchState,
  type AiControl,
  type ChatAi,
} from "../../shared/aiMenu";
import {
  SUGGESTION_STYLES,
  type SuggestionStyle,
} from "../../shared/suggestions";
import type { Schemas } from "./api";
import { readCached, chatKey } from "./cache";
import { useConnection } from "./connection";
import type { Snapshot } from "./conversation";
import type { ReadingView } from "./data";
import type { Palette } from "./theme";

// A chat's AI Menu (ADR 62): the robot at the header's end opens it as a
// bottom sheet. Its first row is AI view, the global Reading View; a private
// chat's controls follow, and a group's Group AI processing, Ask this group
// and Hide bots. A tap on a switch flips it; a tap on its row opens the
// control's page (Q16), where its panels and Guidance fields are.

// The robot: filled while AI work can run in the chat, an outline otherwise,
// and its name says which (Q14).
export function RobotButton({
  ai,
  palette,
  onPress,
}: {
  ai: ChatAi | null | undefined;
  palette: Palette;
  onPress: () => void;
}) {
  const on = !!ai?.running;
  return (
    <Pressable
      testID="ai-menu"
      accessibilityRole="button"
      accessibilityLabel={robotLabel(ai)}
      hitSlop={8}
      onPress={onPress}
      style={[
        styles.robot,
        on
          ? { backgroundColor: palette.accent, borderColor: palette.accent }
          : { borderColor: palette.line },
      ]}
    >
      <MaterialCommunityIcons
        name={on ? "robot" : "robot-outline"}
        size={20}
        color={on ? palette.onAccent : palette.muted}
      />
    </Pressable>
  );
}

// The open chat's latest snapshot, for its AI Menu's pages: the chat screen
// beneath them keeps reading it, so a page shows what the chat shows.
type Shown = {
  snapshot: Snapshot;
  // When the read behind it started.
  readAt: number;
  live: boolean;
  reload: () => Promise<void>;
};
const shownChats = new Map<string, Shown>();
const watchers = new Set<() => void>();

export function showChat(key: string, value: Shown | null) {
  if (value) shownChats.set(key, value);
  else shownChats.delete(key);
  for (const watcher of watchers) watcher();
}

function watch(watcher: () => void) {
  watchers.add(watcher);
  return () => {
    watchers.delete(watcher);
  };
}

// A page's chat: the open chat's, or what the phone kept of it.
export function useShownChat(account: number, peer: string) {
  const key = `${account}/${peer}`;
  const shown = useSyncExternalStore(watch, () => shownChats.get(key) ?? null);
  const [kept, setKept] = useState<Snapshot | null>(null);
  useEffect(() => {
    if (shown) return;
    let live = true;
    void readCached<Snapshot>(chatKey(account, peer)).then(
      (copy) => live && setKept(copy),
    );
    return () => {
      live = false;
    };
  }, [account, peer, shown]);
  return (
    shown ??
    (kept && {
      snapshot: kept,
      readAt: 0,
      live: false,
      reload: async () => undefined,
    })
  );
}

// A private chat's switches and Style as the menu shows them: the snapshot's,
// or a change's answer until a read started after it shows it.
export function useChatAi(
  account: number,
  peer: string,
  ai: ChatAi | null | undefined,
  readAt: number,
  reload: () => Promise<void>,
) {
  const { api } = useConnection();
  const [answer, setAnswer] = useState<{ ai: ChatAi; at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const shown = answer && readAt <= answer.at ? answer.ai : (ai ?? null);
  const change = useCallback(
    async (control: AiControl, value: boolean | SuggestionStyle) => {
      if (!shown || busy) return;
      const write = chatWrite(control, shown, value);
      if (!write) return;
      setBusy(true);
      setProblem("");
      try {
        const reply = await api<{
          automatic?: boolean;
          style?: SuggestionStyle;
          enabled?: boolean;
        }>(
          `accounts/${account}/chats/${encodeURIComponent(peer)}${write.suffix}`,
          {
            body: write.body,
          },
        );
        setAnswer({ ai: answered(shown, control, reply), at: Date.now() });
        void reload();
      } catch (error) {
        setProblem(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [account, api, busy, peer, reload, shown],
  );
  return { ai: shown, busy, problem, change };
}

// Opens a control's page over the chat; the sheet closes first, since a
// Modal would stay above the page.
export function openControl(account: number, peer: string, control: AiControl) {
  router.push({
    pathname: "/ai/[account]/[peer]",
    params: { account: String(account), peer, control },
  });
}

// The sheet itself, scrolling when its rows outgrow the screen.
function Sheet({
  visible,
  palette,
  onClose,
  children,
}: {
  visible: boolean;
  palette: Palette;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const { height } = useWindowDimensions();
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        {/* Behind the sheet, so its rows stay separate for screen readers. */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={StyleSheet.absoluteFill}
          onPress={onClose}
        />
        <SafeAreaView
          edges={["bottom"]}
          style={[styles.sheet, { backgroundColor: palette.background }]}
        >
          <ScrollView
            accessibilityLabel="AI Menu"
            style={{ maxHeight: height * 0.85 }}
            contentContainerStyle={styles.rows}
          >
            {children}
          </ScrollView>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

// One control: its name and line open its page, and its switch, when it has
// one, flips it in place.
export function MenuRow({
  control,
  label,
  detail,
  line,
  notes = [],
  value,
  disabled,
  palette,
  onToggle,
  onOpen,
}: {
  control: AiControl | "hide-bots" | "ask";
  label: string;
  // What it holds, such as Style's choice.
  detail?: string;
  line?: string;
  // Why it can't run, under it.
  notes?: (string | null | undefined)[];
  // Null for a row without a switch.
  value: boolean | null;
  disabled: boolean;
  palette: Palette;
  onToggle?: (value: boolean) => void;
  onOpen?: () => void;
}) {
  const shownNotes = notes.filter(Boolean) as string[];
  const text = (
    <>
      <Text style={[styles.label, { color: palette.text }]}>
        {label}
        {detail ? (
          <Text style={{ color: palette.muted }}>{` · ${detail}`}</Text>
        ) : null}
      </Text>
      {line ? (
        <Text style={[styles.line, { color: palette.muted }]}>{line}</Text>
      ) : null}
      {shownNotes.map((note) => (
        <Text key={note} style={[styles.line, { color: palette.warning }]}>
          {note}
        </Text>
      ))}
    </>
  );
  return (
    <View style={styles.row}>
      {onOpen ? (
        <Pressable
          testID={`ai-${control}`}
          accessibilityRole="button"
          accessibilityLabel={detail ? `${label}, ${detail}` : label}
          accessibilityHint={[line, ...shownNotes].filter(Boolean).join(" ")}
          onPress={onOpen}
          style={({ pressed }) => [
            styles.text,
            pressed ? styles.pressed : null,
          ]}
        >
          {text}
        </Pressable>
      ) : (
        <View style={styles.text}>{text}</View>
      )}
      {value === null ? null : (
        <Switch
          testID={`ai-${control}-switch`}
          accessibilityLabel={label}
          value={value}
          disabled={disabled}
          onValueChange={onToggle}
          trackColor={{ true: palette.accent }}
        />
      )}
      {onOpen ? (
        <Pressable
          accessible={false}
          importantForAccessibility="no-hide-descendants"
          hitSlop={8}
          onPress={onOpen}
        >
          <Ionicons name="chevron-forward" size={18} color={palette.muted} />
        </Pressable>
      ) : (
        // A row without a page keeps its switch in line with the others'.
        <View style={styles.more} />
      )}
    </View>
  );
}

function Refusal({ text, palette }: { text: string; palette: Palette }) {
  return text ? (
    <Text
      accessibilityRole="alert"
      style={[styles.note, { color: palette.error }]}
    >
      {text}
    </Text>
  ) : null;
}

// A private chat's AI Menu (Q15): AI view, then Automatic suggestions,
// Style, Emotion, Soften hostile messages, Voice and images and Remember this
// chat, with the notes saying why nothing runs.
export function PrivateAiMenu({
  visible,
  account,
  peer,
  ai,
  view,
  onView,
  offline,
  palette,
  onClose,
}: {
  visible: boolean;
  account: number;
  peer: string;
  ai: ReturnType<typeof useChatAi>;
  view: ReadingView;
  onView: (view: ReadingView) => void;
  // The switches wait for the server.
  offline: boolean;
  palette: Palette;
  onClose: () => void;
}) {
  const open = (control: AiControl) => {
    onClose();
    openControl(account, peer, control);
  };
  const style = SUGGESTION_STYLES.find((item) => item.value === ai.ai?.style);
  return (
    <Sheet visible={visible} palette={palette} onClose={onClose}>
      <MenuRow
        control="view"
        label={AI_CONTROLS.view.label}
        line={AI_CONTROLS.view.line}
        value={view === "ai"}
        disabled={false}
        palette={palette}
        onToggle={(on) => onView(on ? "ai" : "originals")}
        onOpen={() => open("view")}
      />
      {PRIVATE_CONTROLS.map((control) => {
        const value = switchState(control, ai.ai);
        return (
          <MenuRow
            key={control}
            control={control}
            label={AI_CONTROLS[control].label}
            detail={control === "style" ? style?.label : undefined}
            line={
              control === "style"
                ? style?.explanation
                : AI_CONTROLS[control].line
            }
            notes={[serverNote(control, ai.ai)]}
            value={hasSwitch(control) ? (value ?? false) : null}
            disabled={value === null || ai.busy || offline}
            palette={palette}
            onToggle={(on) => void ai.change(control, on)}
            onOpen={() => open(control)}
          />
        );
      })}
      {ai.ai && !ai.ai.account ? (
        <Text style={[styles.note, { color: palette.muted }]}>
          {ACCOUNT_OFF}
        </Text>
      ) : null}
      {offline ? (
        <Text style={[styles.note, { color: palette.muted }]}>
          The switches can be changed once the server answers.
        </Text>
      ) : null}
      <Refusal text={ai.problem} palette={palette} />
    </Sheet>
  );
}

// A group's AI Menu (Q13): AI view, Group AI processing with its notes, Ask
// this group while it is On, and Hide bots; a retained group's holds AI view
// and Hide bots.
export function GroupAiMenu({
  visible,
  account,
  peer,
  processing,
  accountEnabled,
  retained,
  hideBots,
  view,
  onView,
  busy,
  note,
  palette,
  onProcessing,
  onAsk,
  onHideBots,
  onClose,
}: {
  visible: boolean;
  account: number;
  peer: string;
  processing: Schemas["GroupProcessing"] | null | undefined;
  accountEnabled: boolean;
  retained: boolean;
  hideBots: boolean;
  view: ReadingView;
  onView: (view: ReadingView) => void;
  busy: boolean;
  // The latest refusal.
  note: string;
  palette: Palette;
  onProcessing: () => void;
  onAsk: () => void;
  onHideBots: () => void;
  onClose: () => void;
}) {
  const open = (control: AiControl) => {
    onClose();
    openControl(account, peer, control);
  };
  return (
    <Sheet visible={visible} palette={palette} onClose={onClose}>
      <MenuRow
        control="view"
        label={AI_CONTROLS.view.label}
        line={AI_CONTROLS.view.line}
        value={view === "ai"}
        disabled={false}
        palette={palette}
        onToggle={(on) => onView(on ? "ai" : "originals")}
        onOpen={() => open("view")}
      />
      {retained ? null : (
        <MenuRow
          control="group"
          label={AI_CONTROLS.group.label}
          line={AI_CONTROLS.group.line}
          notes={[groupNote(processing, accountEnabled)]}
          value={processing?.processing_enabled ?? false}
          disabled={!processing || busy}
          palette={palette}
          onToggle={onProcessing}
          onOpen={() => open("group")}
        />
      )}
      {!retained && processing?.processing_enabled ? (
        <MenuRow
          control="ask"
          label={ASK_THIS_GROUP}
          value={null}
          disabled={false}
          palette={palette}
          onOpen={() => {
            onClose();
            onAsk();
          }}
        />
      ) : null}
      <MenuRow
        control="hide-bots"
        label={HIDE_BOTS.label}
        line={HIDE_BOTS.line}
        value={hideBots}
        disabled={busy}
        palette={palette}
        onToggle={onHideBots}
      />
      <Refusal text={note} palette={palette} />
    </Sheet>
  );
}

const styles = StyleSheet.create({
  robot: {
    width: 34,
    height: 34,
    borderRadius: 17,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingVertical: 8,
  },
  rows: { paddingBottom: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 20,
    paddingVertical: 10,
    minHeight: 52,
  },
  text: { flex: 1, gap: 2 },
  pressed: { opacity: 0.6 },
  more: { width: 18 },
  label: { fontSize: 17 },
  line: { fontSize: 13, lineHeight: 18 },
  note: { fontSize: 13, paddingHorizontal: 20, paddingBottom: 10 },
});
