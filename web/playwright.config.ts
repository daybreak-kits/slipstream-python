import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      // Chinese glyphs and antialiasing differ across Chromium builds by a few
      // pixels; anything above 0.2% of the image is a real visual change.
      maxDiffPixelRatio: 0.002,
      animations: "disabled",
    },
  },
  reporter: [["list"]],
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{ext}",
  use: {
    // 3680 is the gateway that serves the production build; the dev server for
    // the visual suite must not hijack it, or every run would silently test a
    // stale dist instead of the working tree.
    baseURL: "http://127.0.0.1:3690",
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: "npm run dev -- --port 3690 --strictPort",
    url: "http://127.0.0.1:3690",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
