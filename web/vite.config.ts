import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8765" },
    // Pure modules shared with the Enloq app live in ../shared (ADR 38 Q32).
    fs: { allow: [".."] },
  },
});
