import { defineConfig, devices } from "@playwright/test";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = dirname(fileURLToPath(import.meta.url));
const backendPort = process.env.E2E_BACKEND_PORT ?? "8010";
const frontendPort = process.env.E2E_FRONTEND_PORT ?? "5174";
const backendOrigin = `http://127.0.0.1:${backendPort}`;
const frontendOrigin = `http://127.0.0.1:${frontendPort}`;
const e2eDatabaseUrl = process.env.E2E_DATABASE_URL;

if (!e2eDatabaseUrl) {
  throw new Error(
    "Set E2E_DATABASE_URL to a disposable postgresql+asyncpg database whose name contains a distinct 'test' segment."
  );
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI
    ? [["line"], ["html", { open: "never" }]]
    : [["list"]],
  use: {
    baseURL: frontendOrigin,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: [
    {
      command: "node e2e/start-backend.mjs",
      cwd: frontendRoot,
      env: {
        E2E_DATABASE_URL: e2eDatabaseUrl,
        E2E_BACKEND_PORT: backendPort,
        E2E_FRONTEND_ORIGIN: frontendOrigin
      },
      reuseExistingServer: false,
      timeout: 120_000,
      url: `${backendOrigin}/api/v1/health/ready`
    },
    {
      command: `npm run dev -- --port ${frontendPort} --strictPort`,
      cwd: frontendRoot,
      env: {
        VITE_WMS_API_BASE_URL: `${backendOrigin}/api/v1`
      },
      reuseExistingServer: false,
      timeout: 120_000,
      url: frontendOrigin
    }
  ]
});
