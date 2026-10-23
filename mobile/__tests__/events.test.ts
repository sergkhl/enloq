import { RETRY_DELAYS, openEvents } from "../src/events";

type Listener = (event: Record<string, unknown>) => void;
const mockSources: MockSource[] = [];

class MockSource {
  listeners = new Map<string, Listener[]>();
  closed = false;
  constructor(
    public url: string,
    public options: { headers: Record<string, string> },
  ) {
    mockSources.push(this);
  }
  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  removeAllEventListeners() {
    this.listeners.clear();
  }
  close() {
    this.closed = true;
  }
  emit(type: string, event: Record<string, unknown> = {}) {
    for (const listener of this.listeners.get(type) ?? [])
      listener({ type, ...event });
  }
}

jest.mock("react-native-sse", () => ({
  __esModule: true,
  default: jest.fn(
    (url: string, options: { headers: Record<string, string> }) =>
      new MockSource(url, options),
  ),
}));

beforeEach(() => {
  mockSources.length = 0;
  jest.useFakeTimers();
});

afterEach(() => jest.useRealTimers());

function open() {
  const handlers = {
    onState: jest.fn(),
    onChange: jest.fn(),
    onRevoked: jest.fn(),
  };
  const stop = openEvents("https://s.example", "t0ken", handlers);
  return { handlers, stop };
}

test("streams changes with the bearer credential", () => {
  const { handlers } = open();
  const source = mockSources[0]!;
  expect(source.url).toBe("https://s.example/api/events");
  expect(source.options.headers).toEqual({ Authorization: "Bearer t0ken" });
  expect(handlers.onState).toHaveBeenLastCalledWith("connecting");
  source.emit("refresh", { data: '{"accounts":[1]}' });
  expect(handlers.onState).toHaveBeenLastCalledWith("open");
  expect(handlers.onChange).toHaveBeenLastCalledWith({ kind: "refresh" });
  source.emit("change", {
    data: '{"shared":["reading_view"],"chats":{"1":["1/user:101"]}}',
  });
  expect(handlers.onChange).toHaveBeenLastCalledWith({
    kind: "change",
    shared: ["reading_view"],
    chats: { "1": ["1/user:101"] },
  });
  // Unreadable or malformed changes fall back to rereading everything.
  for (const data of ["not json", '{"shared":[1]}', '{"chats":{"1":"x"}}']) {
    source.emit("change", { data });
    expect(handlers.onChange).toHaveBeenLastCalledWith({ kind: "refresh" });
  }
});

test("a failed stream reopens after growing delays until it opens", () => {
  const { handlers } = open();
  // A network failure: the library itself would not try again.
  mockSources[0]!.emit("error", { xhrStatus: 0 });
  expect(handlers.onState).toHaveBeenLastCalledWith("reconnecting");
  expect(mockSources[0]!.closed).toBe(true);
  jest.advanceTimersByTime(RETRY_DELAYS[0]! - 1);
  expect(mockSources).toHaveLength(1);
  jest.advanceTimersByTime(1);
  expect(mockSources).toHaveLength(2);
  mockSources[1]!.emit("error", { xhrStatus: 502 });
  mockSources[1]!.emit("error", { xhrStatus: 502 });
  jest.advanceTimersByTime(RETRY_DELAYS[1]!);
  expect(mockSources).toHaveLength(3);
  mockSources[2]!.emit("open");
  expect(handlers.onState).toHaveBeenLastCalledWith("open");
  // An opened stream starts its delays again from the shortest.
  mockSources[2]!.emit("error", { type: "timeout" });
  jest.advanceTimersByTime(RETRY_DELAYS[0]!);
  expect(mockSources).toHaveLength(4);
});

test.each([
  ["revoked event", "revoked", {}],
  ["401 response", "error", { xhrStatus: 401 }],
])("a %s stops the stream for good", (_, type, event) => {
  const { handlers } = open();
  const source = mockSources[0]!;
  source.emit(type, event);
  expect(handlers.onRevoked).toHaveBeenCalledTimes(1);
  expect(source.closed).toBe(true);
  jest.advanceTimersByTime(RETRY_DELAYS.at(-1)! * 2);
  expect(mockSources).toHaveLength(1);
});

test("stopping ends a pending retry too", () => {
  const { stop, handlers } = open();
  mockSources[0]!.emit("error", { xhrStatus: 0 });
  stop();
  stop();
  jest.advanceTimersByTime(RETRY_DELAYS.at(-1)! * 2);
  expect(mockSources).toHaveLength(1);
  expect(handlers.onRevoked).not.toHaveBeenCalled();
});
