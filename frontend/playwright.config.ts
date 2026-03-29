import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  use: {
    baseURL: "http://localhost:3000",
    headless: true,
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command:
        "cd ../backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
      port: 8000,
      reuseExistingServer: true,
    },
    {
      command: "npx next dev --port 3000",
      port: 3000,
      reuseExistingServer: true,
    },
  ],
});
