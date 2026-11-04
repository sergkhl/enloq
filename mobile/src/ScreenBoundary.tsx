import { router } from "expo-router";
import React from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { recordError } from "./logs";
import { usePalette } from "./theme";
import { Button } from "./ui";

// A screen that fails while drawing shows its error here instead of closing
// the app (ADR 61): the error in full, Try again, and a way out to the list.
// Without `navigable` it stands outside the navigator, where there is none.
function ScreenError({
  error,
  stack,
  navigable,
  onRetry,
}: {
  error: Error;
  stack: string;
  navigable: boolean;
  onRetry: () => void;
}) {
  const palette = usePalette();
  return (
    <SafeAreaView
      style={[styles.fill, { backgroundColor: palette.background }]}
    >
      <ScrollView contentContainerStyle={styles.content}>
        <Text
          accessibilityRole="header"
          style={[styles.title, { color: palette.text }]}
        >
          This screen hit an error
        </Text>
        <Text selectable style={{ color: palette.error }}>
          {error.name}: {error.message}
        </Text>
        <Text style={{ color: palette.muted }}>
          It&apos;s in Settings → Logs, and goes to the server&apos;s log.
        </Text>
        {stack ? (
          <Text selectable style={[styles.stack, { color: palette.muted }]}>
            {stack}
          </Text>
        ) : null}
      </ScrollView>
      <View style={styles.actions}>
        <Button label="Try again" primary onPress={onRetry} />
        {navigable ? (
          <Button
            label="Back to chats"
            onPress={() => router.replace("/chats")}
          />
        ) : null}
      </View>
    </SafeAreaView>
  );
}

type Props = { children: React.ReactNode; navigable?: boolean };
type State = { error: Error | null; stack: string; attempt: number };

export class ScreenBoundary extends React.Component<Props, State> {
  state: State = { error: null, stack: "", attempt: 0 };

  static getDerivedStateFromError(thrown: unknown) {
    return {
      error: thrown instanceof Error ? thrown : new Error(String(thrown)),
    };
  }

  componentDidCatch(thrown: unknown, info: React.ErrorInfo) {
    recordError("screen", thrown, info.componentStack ?? undefined);
    this.setState({
      stack: [
        thrown instanceof Error ? thrown.stack : undefined,
        info.componentStack,
      ]
        .filter(Boolean)
        .join("\n"),
    });
  }

  render() {
    const { error, stack, attempt } = this.state;
    if (error)
      return (
        <ScreenError
          error={error}
          stack={stack}
          navigable={!!this.props.navigable}
          onRetry={() =>
            this.setState({ error: null, stack: "", attempt: attempt + 1 })
          }
        />
      );
    return <React.Fragment key={attempt}>{this.props.children}</React.Fragment>;
  }
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { padding: 20, gap: 12 },
  title: { fontSize: 20, fontWeight: "600" },
  stack: { fontFamily: "monospace", fontSize: 11 },
  actions: { padding: 20, gap: 10 },
});
