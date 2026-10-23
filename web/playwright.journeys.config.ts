import { defineConfig } from "@playwright/test";

// ADR 44 Q8: the rebuilt web's journeys at phone size (390×844, touch) in
// Chromium against the authored fixture server, mirroring the Enloq app's
// Maestro journeys. `./scripts/check` runs them; they replaced the desktop
// specs with MW5. Their fixture listens on 8777 with its own build, so it
// never meets the app journeys' fixture on 8776.
const PORT = 8777;

export default defineConfig({
  testDir: "./journeys",
  workers: 1,
  retries: 0,
  reporter: "list",
  timeout: 60000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    browserName: "chromium",
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 3,
  },
  webServer: {
    command: `npx vite build --outDir ../.local/web-journeys-dist --emptyOutDir --logLevel warn && FIXTURE_PORT=${PORT} FIXTURE_WEB=.local/web-journeys-dist uv run --project .. python ../scripts/test_server.py`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120000,
    // Without this Playwright SIGKILLs the server, skipping its data cleanup.
    gracefulShutdown: { signal: "SIGTERM", timeout: 10000 },
  },
  outputDir: "../.local/journey-results",
});
