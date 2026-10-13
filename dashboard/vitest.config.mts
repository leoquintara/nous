import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

// Sin @vitejs/plugin-react: vitest 3 usa su propio vite (rollup) y
// plugin-react 6 exige vite 8 (rolldown) — conflicto de tipos Plugin.
// Para tests JSX futuros vitest transforma tsx vía esbuild sin plugin.
export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});

