import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    // jsdom + React Testing Library is slow, and the roster-import test already
    // takes ~4s on an idle machine against vitest's 5s default. A loaded CI
    // runner tips it over and the gate flakes, so give it real headroom -- long
    // enough not to lose races, short enough to still catch a genuine hang.
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
