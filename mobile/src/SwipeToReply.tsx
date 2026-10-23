import Ionicons from "@expo/vector-icons/Ionicons";
import React, { useMemo, useRef } from "react";
import {
  Animated,
  PanResponder,
  Platform,
  StyleSheet,
  Vibration,
  View,
} from "react-native";

// How far a row follows the finger, and how far it must go to reply.
const LIMIT = 72;
export const THRESHOLD = 56;

// Swipe to Reply (ADR 51 Q5): a row swiped from right to left past the
// threshold shows a reply arrow with a light vibration, and letting go does
// what its sheet's Reply does. A vertical drag stays the list's scroll, and
// a long press still opens the sheet. Off while `enabled` is false: during
// Message Selection, on rows without Reply and while Reply is unavailable.
export function SwipeToReply({
  enabled,
  onReply,
  color,
  children,
}: {
  enabled: boolean;
  onReply: () => void;
  color: string;
  children: React.ReactNode;
}) {
  const offset = useRef(new Animated.Value(0)).current;
  const armed = useRef(false);
  // The latest props, so the responder made once never acts on stale ones.
  const latest = useRef({ enabled, onReply });
  latest.current = { enabled, onReply };
  const responder = useMemo(() => {
    const settle = () => {
      armed.current = false;
      Animated.spring(offset, {
        toValue: 0,
        useNativeDriver: true,
        bounciness: 0,
      }).start();
    };
    return PanResponder.create({
      onMoveShouldSetPanResponder: (_, gesture) =>
        latest.current.enabled &&
        gesture.dx < -12 &&
        Math.abs(gesture.dx) > Math.abs(gesture.dy) * 2,
      // Once horizontal, the list doesn't take the gesture back.
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: (_, gesture) => {
        const dx = Math.max(Math.min(gesture.dx, 0), -LIMIT);
        offset.setValue(dx);
        const past = dx <= -THRESHOLD;
        if (past && !armed.current) {
          armed.current = true;
          // A short tick; iOS's Vibration API has only a long buzz.
          if (Platform.OS === "android") Vibration.vibrate(12);
        } else if (!past) armed.current = false;
      },
      onPanResponderRelease: () => {
        if (armed.current && latest.current.enabled) latest.current.onReply();
        settle();
      },
      onPanResponderTerminate: settle,
    });
  }, [offset]);
  return (
    <View>
      <Animated.View
        pointerEvents="none"
        style={[
          styles.arrow,
          {
            opacity: offset.interpolate({
              inputRange: [-THRESHOLD, -16, 0],
              outputRange: [1, 0, 0],
              extrapolate: "clamp",
            }),
          },
        ]}
      >
        <Ionicons name="arrow-undo" size={22} color={color} />
      </Animated.View>
      <Animated.View
        {...responder.panHandlers}
        style={{ transform: [{ translateX: offset }] }}
      >
        {children}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  arrow: {
    position: "absolute",
    right: 8,
    top: 0,
    bottom: 0,
    justifyContent: "center",
  },
});
