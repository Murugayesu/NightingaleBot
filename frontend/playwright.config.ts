import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests against the real API (with fake AI providers, see
 * tests/e2e_server.py) and the production build served by `vite preview`.
 * One shared database, so tests run one at a time and use unique accounts.
 */
const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:4173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
      testIgnore: /mobile\.spec\.ts/,
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 7"] },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: "uv run python -m tests.e2e_server",
      cwd: "..",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: !CI,
      timeout: 180_000,
    },
    {
      command: "npm run build && npm run preview",
      url: "http://localhost:4173",
      reuseExistingServer: !CI,
      timeout: 180_000,
    },
  ],
});
