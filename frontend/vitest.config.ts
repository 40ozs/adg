import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Two environments in one run: most modules under test are pure and run in Node, but the
 * shell, navigation, and state components need a DOM to assert focus order and keyboard
 * behavior against. `environmentMatchGlobs` is deprecated in newer Vitest, so the DOM is
 * selected per file with a `// @vitest-environment jsdom` docblock instead.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
      // The real `server-only` module throws unless resolved through React's
      // `react-server` condition, which Next sets and Vitest does not.
      "server-only": fileURLToPath(new URL("./tests/stubs/server-only.ts", import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: "node",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
