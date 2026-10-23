import { render, screen } from "@testing-library/react-native";
import React from "react";
import { Text } from "react-native";
import { SwipeToReply } from "../src/SwipeToReply";

// ADR 51 Q5: a row swiped from right to left past the threshold replies; a
// short, vertical or rightward drag does nothing, and nothing swipes while
// Reply is unavailable. The gesture is fed to the row's responder as React
// Native's touch history reports it.

type Handlers = Record<string, (event: unknown) => unknown>;

async function rig(enabled = true) {
  const replies: number[] = [];
  await render(
    <SwipeToReply
      enabled={enabled}
      onReply={() => replies.push(1)}
      color="#000"
    >
      <Text>Authored row</Text>
    </SwipeToReply>,
  );
  // The moving view holding the row carries the pan responder's handlers.
  let node = screen.getByText("Authored row").parent;
  while (node && !(node.props as Handlers).onMoveShouldSetResponder)
    node = node.parent;
  return { handlers: node!.props as Handlers, replies };
}

// One finger from (200, 300) to (x, y) at `time` milliseconds.
function event(
  x: number,
  y: number,
  time: number,
  previous = { x: 200, y: 300 },
) {
  const touch = {
    touchActive: true,
    startPageX: 200,
    startPageY: 300,
    startTimeStamp: 0,
    currentPageX: x,
    currentPageY: y,
    currentTimeStamp: time,
    previousPageX: previous.x,
    previousPageY: previous.y,
    previousTimeStamp: time - 10,
  };
  return {
    nativeEvent: { changedTouches: [], touches: [], pageX: x, pageY: y },
    touchHistory: {
      numberActiveTouches: 1,
      indexOfSingleActiveTouch: 0,
      mostRecentTimeStamp: time,
      touchBank: [touch],
    },
  };
}

// Whether the row takes a move: React Native asks the capture phase first,
// which updates the gesture, then the bubbling phase.
function claims(handlers: Handlers, move: unknown) {
  handlers.onMoveShouldSetResponderCapture!(move);
  return handlers.onMoveShouldSetResponder!(move);
}

function drag(handlers: Handlers, path: [number, number][]) {
  handlers.onResponderGrant!(event(200, 300, 1));
  let previous = { x: 200, y: 300 };
  path.forEach(([x, y], index) => {
    handlers.onResponderMove!(event(x, y, 40 + index * 20, previous));
    previous = { x, y };
  });
  handlers.onResponderRelease!(event(previous.x, previous.y, 500, previous));
}

test("a swipe past the threshold replies once", async () => {
  const { handlers, replies } = await rig();
  expect(claims(handlers, event(180, 302, 20))).toBe(true);
  drag(handlers, [
    [180, 301],
    [150, 302],
    [130, 302],
  ]);
  expect(replies).toEqual([1]);
});

test("a short, vertical or rightward drag replies to nothing", async () => {
  const { handlers, replies } = await rig();
  expect(claims(handlers, event(195, 360, 20))).toBe(false);
  expect(claims(handlers, event(240, 300, 20))).toBe(false);
  drag(handlers, [
    [180, 300],
    [170, 300],
  ]);
  expect(replies).toEqual([]);
});

test("nothing swipes while Reply is unavailable", async () => {
  const { handlers } = await rig(false);
  expect(claims(handlers, event(150, 300, 20))).toBe(false);
});
