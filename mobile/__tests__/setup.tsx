// Shared module doubles for screen tests: no camera, Keychain, router or
// network leaves the test process. Each test drives `fetch` itself.
export const mockStore = new Map<string, string>();
export const mockRouter = {
  replace: jest.fn(),
  push: jest.fn(),
  navigate: jest.fn(),
  back: jest.fn(),
  canGoBack: jest.fn(() => false),
};
export const mockRedirects: string[] = [];
export const mockParams: Record<string, string> = {};
export const mockClipboard: string[] = [];
// The app's private files by URI, for the offline copy (Q20).
export const mockFiles = new Map<string, string>();
// What the photo picker returns (null: cancelled), whether the camera is
// allowed, and the resizes the image step asked for.
export const mockPhotos: {
  assets:
    { uri: string; width: number; height: number; fileName?: string }[] | null;
  camera: boolean;
  resizes: unknown[];
} = { assets: null, camera: true, resizes: [] };

type MockListener = (event: Record<string, unknown>) => void;
// The app's change stream: tests emit server events into the latest source.
export const mockEventSources: {
  listeners: Map<string, MockListener[]>;
  emit: (type: string, event?: Record<string, unknown>) => void;
}[] = [];

jest.mock(
  "react-native-safe-area-context",
  () => require("react-native-safe-area-context/jest/mock").default,
);

jest.mock("expo-secure-store", () => ({
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: 1,
  getItemAsync: jest.fn(async (key: string) => mockStore.get(key) ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => {
    mockStore.set(key, value);
  }),
  deleteItemAsync: jest.fn(async (key: string) => {
    mockStore.delete(key);
  }),
}));

jest.mock("expo-camera", () => ({
  CameraView: () => null,
  useCameraPermissions: () => [{ granted: true }, jest.fn()],
}));

jest.mock("expo-device", () => ({ modelName: "Pixel 9" }));

// FCM through expo-notifications (ADR 38): the token
// the phone would get from Google, the notices it showed, the token listeners
// and the push task it defined.
export const mockNotifications = {
  token: "eAuthoredFcmToken01:APA91b" + "Q".repeat(40),
  granted: true,
  scheduled: [] as Record<string, unknown>[],
  tokenListeners: [] as ((token: { data: string }) => void)[],
  tasks: new Map<
    string,
    (body: { data: unknown; error: unknown }) => Promise<unknown>
  >(),
};

jest.mock("expo-notifications", () => ({
  AndroidImportance: { HIGH: 4 },
  AndroidNotificationPriority: { HIGH: "high" },
  setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn(async () => null),
  requestPermissionsAsync: jest.fn(async () => ({
    granted: mockNotifications.granted,
  })),
  registerTaskAsync: jest.fn(async () => null),
  getDevicePushTokenAsync: jest.fn(async () => ({
    type: "android",
    data: mockNotifications.token,
  })),
  unregisterForNotificationsAsync: jest.fn(async () => undefined),
  addPushTokenListener: jest.fn(
    (listener: (token: { data: string }) => void) => {
      mockNotifications.tokenListeners.push(listener);
      return { remove: () => undefined };
    },
  ),
  scheduleNotificationAsync: jest.fn(
    async (request: Record<string, unknown>) => {
      mockNotifications.scheduled.push(request);
      return "notice";
    },
  ),
  useLastNotificationResponse: jest.fn(() => undefined),
  clearLastNotificationResponseAsync: jest.fn(async () => undefined),
}));

jest.mock("expo-task-manager", () => ({
  defineTask: jest.fn(
    (
      name: string,
      task: (body: { data: unknown; error: unknown }) => Promise<unknown>,
    ) => {
      mockNotifications.tasks.set(name, task);
    },
  ),
}));

jest.mock("expo-router", () => ({
  router: mockRouter,
  // A screen's own options, such as turning iOS's swipe back off.
  Stack: { Screen: () => null },
  Redirect: ({ href }: { href: string }) => {
    mockRedirects.push(href);
    return null;
  },
  useLocalSearchParams: () => mockParams,
  // Screens under test count as focused.
  useFocusEffect: (effect: () => void | (() => void)) => {
    const { useEffect } = require("react");
    useEffect(effect, [effect]);
  },
}));

jest.mock("expo-clipboard", () => ({
  setStringAsync: jest.fn(async (text: string) => {
    mockClipboard.push(text);
    return true;
  }),
}));

jest.mock("expo-sharing", () => ({
  shareAsync: jest.fn(async () => undefined),
}));

jest.mock("expo-file-system", () => {
  const join = (parts: unknown[]) =>
    parts
      .map((part) => (typeof part === "string" ? part : (part as File).uri))
      .join("/");
  class Directory {
    uri: string;
    constructor(...parts: unknown[]) {
      this.uri = join(parts);
    }
    get exists() {
      return [...mockFiles.keys()].some((key) =>
        key.startsWith(`${this.uri}/`),
      );
    }
    create() {}
    delete() {
      for (const key of [...mockFiles.keys()])
        if (key.startsWith(`${this.uri}/`)) mockFiles.delete(key);
    }
    // The folder's own files, not those of folders inside it.
    list() {
      return [...mockFiles.keys()]
        .filter(
          (key) =>
            key.startsWith(`${this.uri}/`) &&
            !key.slice(this.uri.length + 1).includes("/"),
        )
        .map((key) => new File(key));
    }
  }
  class File {
    static downloadFileAsync = jest.fn();
    uri: string;
    constructor(...parts: unknown[]) {
      this.uri = join(parts);
    }
    get name() {
      return this.uri.slice(this.uri.lastIndexOf("/") + 1);
    }
    get exists() {
      return mockFiles.has(this.uri);
    }
    textSync() {
      const text = mockFiles.get(this.uri);
      if (text === undefined) throw new Error(`no file ${this.uri}`);
      return text;
    }
    async text() {
      return this.textSync();
    }
    write(content: string) {
      mockFiles.set(this.uri, content);
    }
    delete() {
      mockFiles.delete(this.uri);
    }
  }
  return {
    Directory,
    File,
    Paths: {
      cache: new Directory("file:///cache"),
      document: new Directory("file:///document"),
    },
  };
});

type MockHandler = (payload: never) => void;
// A media player double: its listeners and what the app asked of it.
function mockEmitter() {
  const listeners = new Map<string, Set<MockHandler>>();
  return {
    listeners,
    addListener(name: string, handler: MockHandler) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name)!.add(handler);
      return { remove: () => listeners.get(name)?.delete(handler) };
    },
    emit(name: string, payload: unknown) {
      for (const handler of listeners.get(name) ?? [])
        (handler as (value: unknown) => void)(payload);
    },
  };
}

// Voice notes share one audio player (ADR 46 CU1).
export const mockAudio = {
  players: [] as ReturnType<typeof mockAudioPlayer>[],
  modes: [] as unknown[],
};

function mockAudioPlayer() {
  return {
    ...mockEmitter(),
    source: null as unknown,
    playing: false,
    rate: 1,
    seeks: [] as number[],
    lockScreen: null as unknown,
    replace(source: unknown) {
      this.source = source;
    },
    play() {
      this.playing = true;
    },
    pause() {
      this.playing = false;
    },
    async seekTo(seconds: number) {
      this.seeks.push(seconds);
    },
    setPlaybackRate(rate: number) {
      this.rate = rate;
    },
    setActiveForLockScreen(active: boolean, metadata: unknown) {
      this.lockScreen = active ? metadata : null;
    },
    clearLockScreenControls() {
      this.lockScreen = null;
    },
  };
}

jest.mock("expo-audio", () => ({
  createAudioPlayer: jest.fn(() => {
    const player = mockAudioPlayer();
    mockAudio.players.push(player);
    return player;
  }),
  setAudioModeAsync: jest.fn(async (mode: unknown) => {
    mockAudio.modes.push(mode);
  }),
}));

// Video messages, videos and GIF loops each have their own player.
export const mockVideo = {
  players: [] as ReturnType<typeof mockVideoPlayer>[],
};

function mockVideoPlayer(source: unknown) {
  const player = {
    ...mockEmitter(),
    source,
    playing: false,
    loop: false,
    muted: false,
    currentTime: 0,
    timeUpdateEventInterval: 0,
    play() {
      player.playing = true;
      player.emit("playingChange", { isPlaying: true });
    },
    pause() {
      player.playing = false;
      player.emit("playingChange", { isPlaying: false });
    },
  };
  return player;
}

jest.mock("expo-video", () => {
  const { createElement, useMemo } = require("react");
  const { View } = require("react-native");
  return {
    useVideoPlayer: (
      source: unknown,
      setup?: (player: ReturnType<typeof mockVideoPlayer>) => void,
    ) =>
      useMemo(() => {
        const player = mockVideoPlayer(source);
        setup?.(player);
        mockVideo.players.push(player);
        return player;
      }, [JSON.stringify(source)]),
    VideoView: ({ nativeControls }: { nativeControls?: boolean }) =>
      createElement(View, {
        testID: nativeControls ? "video-controls" : "video-view",
      }),
  };
});

// Images by expo-image: each records its source and whether it animates.
export const mockImages: { uri: string; autoplay?: boolean }[] = [];

jest.mock("expo-image", () => {
  const { createElement } = require("react");
  const { View } = require("react-native");
  return {
    Image: ({
      source,
      autoplay,
    }: {
      source: { uri: string };
      autoplay?: boolean;
    }) => {
      mockImages.push({ uri: source.uri, autoplay });
      return createElement(View, { testID: `image:${source.uri}` });
    },
  };
});

// Lottie: what played and paused.
export const mockLottie: string[] = [];

jest.mock("lottie-react-native", () => {
  const { createElement, forwardRef, useImperativeHandle } = require("react");
  const { View } = require("react-native");
  return {
    __esModule: true,
    default: forwardRef((_props: object, ref: unknown) => {
      useImperativeHandle(ref, () => ({
        play: () => mockLottie.push("play"),
        pause: () => mockLottie.push("pause"),
      }));
      return createElement(View, { testID: "lottie" });
    }),
  };
});

jest.mock("expo-image-picker", () => {
  const pick = async () =>
    mockPhotos.assets
      ? { canceled: false, assets: mockPhotos.assets }
      : { canceled: true, assets: null };
  return {
    requestCameraPermissionsAsync: jest.fn(async () => ({
      granted: mockPhotos.camera,
    })),
    launchCameraAsync: jest.fn(pick),
    launchImageLibraryAsync: jest.fn(pick),
  };
});

jest.mock("expo-image-manipulator", () => ({
  SaveFormat: { JPEG: "jpeg" },
  ImageManipulator: {
    manipulate: (uri: string) => {
      const context = {
        resize: (size: unknown) => {
          mockPhotos.resizes.push(size);
          return context;
        },
        renderAsync: async () => ({
          width: 4096,
          height: 3072,
          saveAsync: async () => ({
            uri,
            width: 4096,
            height: 3072,
            base64: "AAAA",
          }),
        }),
      };
      return context;
    },
  },
}));

jest.mock("react-native-sse", () => ({
  __esModule: true,
  default: jest.fn(() => {
    const listeners = new Map<string, MockListener[]>();
    const source = {
      listeners,
      addEventListener: (type: string, listener: MockListener) =>
        listeners.set(type, [...(listeners.get(type) ?? []), listener]),
      removeAllEventListeners: () => listeners.clear(),
      close: jest.fn(),
      emit: (type: string, event: Record<string, unknown> = {}) => {
        for (const listener of listeners.get(type) ?? [])
          listener({ type, ...event });
      },
    };
    mockEventSources.push(source);
    return source;
  }),
}));

export type Route = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string },
) => { status: number; body: unknown } | Promise<never>;

// Requests every conversation makes, answered unless a test's own route
// does: the tone, no forward header to fill in (ADR 46 CU2) and a palette.
export const mockPalette = {
  emoji: ["👍", "👎", "❤️", "🔥", "🤝", "👌", "🙏", "😁"],
  limit: 1,
};
function fallback(url: string) {
  if (url.endsWith("/originals/forwarding")) return { status: 200, body: [] };
  // The owner's People (ADR 48), empty unless a test says.
  if (url.endsWith("/api/people"))
    return { status: 200, body: { requests: [], members: [] } };
  // The phone's member (ADR 48): an approved owner unless a test says.
  if (url.endsWith("/api/members/me"))
    return {
      status: 200,
      body: {
        id: "owner",
        role: "owner",
        state: "active",
        name: "",
        agreement: null,
      },
    };
  // The composer's tone, which few tests answer themselves.
  if (url.endsWith("/api/preferences"))
    return { status: 200, body: { tone: "neutral" } };
  if (url.endsWith("/reactions/options"))
    return { status: 200, body: mockPalette };
  return null;
}

export function serve(route: Route) {
  const calls: {
    url: string;
    method: string;
    headers: Record<string, string>;
    body?: string;
  }[] = [];
  global.fetch = jest.fn(
    async (
      url: string,
      init: { method: string; headers: Record<string, string>; body?: string },
    ) => {
      calls.push({ url, ...init });
      let answer: { status: number; body: unknown };
      try {
        answer = await route(url, init);
      } catch (error) {
        // Only a route's own "unexpected" refusal; a test going offline
        // stays offline.
        const known = /^unexpected/.test(String((error as Error)?.message))
          ? fallback(url)
          : null;
        if (!known) throw error;
        answer = known;
      }
      return {
        status: answer.status,
        ok: answer.status >= 200 && answer.status < 300,
        json: async () => answer.body,
      };
    },
  ) as unknown as typeof fetch;
  return calls;
}

export function reset() {
  mockStore.clear();
  mockRouter.replace.mockReset();
  mockRouter.push.mockReset();
  mockRouter.navigate.mockReset();
  mockNotifications.granted = true;
  mockNotifications.scheduled.length = 0;
  mockNotifications.tokenListeners.length = 0;
  mockRedirects.length = 0;
  mockClipboard.length = 0;
  mockFiles.clear();
  mockPhotos.assets = null;
  mockPhotos.camera = true;
  mockPhotos.resizes.length = 0;
  mockEventSources.length = 0;
  for (const key of Object.keys(mockParams)) delete mockParams[key];
}
