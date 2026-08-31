import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        url: "http://127.0.0.1:5173/"
      }
    },
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    setupFiles: "./tests/setup.ts"
  }
});
