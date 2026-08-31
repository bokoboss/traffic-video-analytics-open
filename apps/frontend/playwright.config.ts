import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "on-first-retry",
    screenshot: "only-on-failure"
  },
  webServer: [
    {
      command: "python -u scripts/run_e2e_backend.py",
      cwd: "../..",
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        TVA_DB_PATH: ".local-data/playwright.sqlite",
        TVA_LOCAL_DATA_DIR: ".local-data/playwright-media",
        TVA_E2E_ALLOW_FAKE_MEDIA: "1"
      }
    },
    {
      command: "pnpm exec vite --host 127.0.0.1 --port 5174",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 120_000
    }
  ],
  projects: [
    { name: "chromium-1440-th", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "chromium-1280-th", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }
  ]
});
