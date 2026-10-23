import Ionicons from "@expo/vector-icons/Ionicons";
import { Image } from "expo-image";
import React, { useEffect, useRef, useState } from "react";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import type { ReplyQuoteText } from "../../shared/replyQuote";
import {
  resumeWithin,
  reviewFits,
  type ResumeReview,
} from "../../shared/retained";
import { useConnection } from "./connection";
import type { Operation } from "./drafts";
import { ReplyQuote } from "./ReplyQuote";
import type { Palette } from "./theme";

type Review = ResumeReview<Operation>;

// Review and resume for a group send proven unsent after access was lost
// (ADR 34, ADR 46 CU6). Review sends nothing: once the service has checked
// the rights again, its short token binds exactly the reviewed send, and
// Resume sends it within that token's time.
export function SendResumeReview({
  account,
  peer,
  operation,
  reply,
  palette,
  onChange,
  request,
}: {
  account: number;
  peer: string;
  operation: Operation;
  // The quote of the message it replies to (ADR 41).
  reply?: ReplyQuoteText | null;
  palette: Palette;
  onChange: () => Promise<void>;
  // Opened from the pending send's sheet (ADR 51 Q4): each new count opens
  // the review, and no button shows in the bubble.
  request?: number;
}) {
  const { api, connection } = useConnection();
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const alive = useRef(true);
  const sequence = useRef(0);
  const revision = JSON.stringify([
    operation.id,
    operation.state,
    operation.cause,
    operation.text,
    operation.images,
    operation.media,
    operation.reply_to,
    operation.resumable,
  ]);
  const current = useRef(revision);
  current.current = revision;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++sequence.current;
    };
  }, []);
  // A changed send needs a new review.
  useEffect(() => {
    ++sequence.current;
    setReview(null);
    setBusy(false);
  }, [revision]);
  useEffect(() => {
    if (!review) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const timer = setTimeout(
      () => {
        setError("This review expired. Review the paused send again.");
        setReview(null);
      },
      Math.max(0, review.expires * 1000 - Date.now()),
    );
    return () => {
      clearInterval(tick);
      clearTimeout(timer);
    };
  }, [review]);

  async function open() {
    if (busy || !operation.resumable) return;
    const read = ++sequence.current;
    const captured = current.current;
    setBusy(true);
    setError("");
    try {
      const value = await api<Review>(
        `accounts/${account}/operations/${operation.id}/review-resume`,
        { body: {} },
      );
      if (
        !alive.current ||
        read !== sequence.current ||
        captured !== current.current
      )
        return;
      if (!reviewFits(value, account, peer, operation.id))
        throw new Error(
          "The paused send changed. Refresh and review it again.",
        );
      setNow(Date.now());
      setReview(value);
    } catch (cause) {
      if (alive.current && read === sequence.current)
        setError((cause as Error).message);
    } finally {
      if (alive.current && read === sequence.current) setBusy(false);
    }
  }
  async function resume() {
    if (!review || busy || review.expires * 1000 <= Date.now()) return;
    const read = ++sequence.current;
    setBusy(true);
    setError("");
    try {
      await api<Operation>(
        `accounts/${account}/operations/${operation.id}/resume`,
        { body: { token: review.token } },
      );
      if (!alive.current) return;
      setReview(null);
      await onChange();
    } catch (cause) {
      if (alive.current && read === sequence.current) {
        setError((cause as Error).message);
        setReview(null);
        await onChange().catch(() => undefined);
      }
    } finally {
      if (alive.current && read === sequence.current) setBusy(false);
    }
  }
  // Each request from the sheet opens the review once.
  const opened = useRef(request ?? 0);
  useEffect(() => {
    if (!request || request === opened.current) return;
    opened.current = request;
    void open();
  });
  const reviewed = review?.operation;
  const accountName =
    (connection.status === "paired" &&
      connection.accounts?.accounts.find((item) => item.account_id === account)
        ?.name) ||
    `Account ${account}`;
  return (
    <>
      {request === undefined ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Review and resume"
          accessibilityState={{ disabled: busy || !operation.resumable }}
          disabled={busy || !operation.resumable}
          onPress={() => void open()}
          style={[
            styles.action,
            {
              borderColor: palette.line,
              opacity: busy || !operation.resumable ? 0.5 : 1,
            },
          ]}
        >
          <Ionicons name="play" size={14} color={palette.accent} />
          <Text style={[styles.actionLabel, { color: palette.accent }]}>
            Review and resume
          </Text>
        </Pressable>
      ) : null}
      {error ? (
        <Text
          accessibilityRole="alert"
          style={[styles.small, { color: palette.error }]}
        >
          {error}
        </Text>
      ) : null}
      <Modal
        visible={!!reviewed}
        transparent
        animationType="fade"
        onRequestClose={() => !busy && setReview(null)}
      >
        <View style={styles.backdrop}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Close"
            style={StyleSheet.absoluteFill}
            onPress={() => !busy && setReview(null)}
          />
          {review && reviewed ? (
            <SafeAreaView
              edges={["bottom"]}
              style={[styles.sheet, { backgroundColor: palette.background }]}
            >
              <Text
                accessibilityRole="header"
                style={[styles.heading, { color: palette.text }]}
              >
                Resume paused send
              </Text>
              <Text style={{ color: palette.muted }}>
                {accountName} · {review.title}
              </Text>
              <ScrollView
                style={[styles.content, { backgroundColor: palette.panel }]}
                contentContainerStyle={styles.contentInner}
              >
                {reviewed.reply_to && reply ? (
                  <ReplyQuote quote={reply} palette={palette} />
                ) : null}
                {reviewed.images.map((identity, index) =>
                  connection.status === "paired" ? (
                    <Image
                      key={identity}
                      accessibilityLabel={`Reviewed image ${index + 1}`}
                      source={{
                        uri: `${connection.credential.origin}/api/accounts/${account}/chats/${encodeURIComponent(peer)}/images/${identity}`,
                        headers: {
                          Authorization: `Bearer ${connection.credential.token}`,
                        },
                      }}
                      contentFit="cover"
                      style={styles.image}
                    />
                  ) : null,
                )}
                {reviewed.media ? (
                  <Text style={[styles.small, { color: palette.muted }]}>
                    {reviewed.media.kind === "sticker" ? "Sticker" : "GIF"}
                  </Text>
                ) : null}
                {reviewed.media?.kind !== "sticker" && reviewed.text ? (
                  <Text style={{ color: palette.text }}>{reviewed.text}</Text>
                ) : null}
              </ScrollView>
              <Text
                accessibilityLiveRegion="polite"
                style={[styles.small, { color: palette.muted }]}
              >
                {resumeWithin(review.expires, now)}
              </Text>
              <View style={styles.buttons}>
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={() => setReview(null)}
                  style={[
                    styles.button,
                    {
                      borderColor: palette.line,
                      backgroundColor: palette.panel,
                    },
                  ]}
                >
                  <Text style={[styles.buttonLabel, { color: palette.accent }]}>
                    Cancel review
                  </Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={() => void resume()}
                  style={[
                    styles.button,
                    {
                      borderColor: palette.accent,
                      backgroundColor: palette.accent,
                      opacity: busy ? 0.5 : 1,
                    },
                  ]}
                >
                  <Text
                    style={[styles.buttonLabel, { color: palette.onAccent }]}
                  >
                    Resume send
                  </Text>
                </Pressable>
              </View>
            </SafeAreaView>
          ) : null}
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  action: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 14,
  },
  actionLabel: { fontSize: 14, fontWeight: "600" },
  small: { fontSize: 13 },
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    gap: 10,
    maxHeight: "90%",
    paddingHorizontal: 18,
    paddingTop: 16,
    paddingBottom: 12,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
  },
  heading: { fontSize: 19, fontWeight: "700" },
  content: { maxHeight: 280, borderRadius: 10 },
  contentInner: { gap: 8, padding: 12 },
  image: { width: 160, height: 120, borderRadius: 8 },
  buttons: { flexDirection: "row", gap: 8 },
  button: {
    flex: 1,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderRadius: 12,
  },
  buttonLabel: { fontSize: 16, fontWeight: "600" },
});
