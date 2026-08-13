import { spawn, spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const workspaceRoot = resolve(frontendRoot, "..");
const databaseUrl = process.env.E2E_DATABASE_URL ?? "";
const python = process.env.PYTHON_BIN ?? "python";

function validateDisposableDatabase(candidate) {
  if (!candidate.startsWith("postgresql+asyncpg://")) {
    throw new Error("E2E_DATABASE_URL must use postgresql+asyncpg://");
  }

  let parsed;
  try {
    parsed = new URL(candidate.replace("postgresql+asyncpg://", "postgresql://"));
  } catch (error) {
    throw new Error("E2E_DATABASE_URL is not a valid PostgreSQL URL", { cause: error });
  }

  const databaseName = decodeURIComponent(parsed.pathname.replace(/^\//, "")).toLowerCase();
  if (!/(?:^|[_-])test(?:$|[_-])/.test(databaseName)) {
    throw new Error(
      "Refusing destructive E2E setup because the database name is not marked with a distinct 'test' segment"
    );
  }
}

function runPython(args, env) {
  const result = spawnSync(python, args, {
    cwd: workspaceRoot,
    env,
    stdio: "inherit"
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${python} ${args.join(" ")} exited with status ${result.status}`);
  }
}

validateDisposableDatabase(databaseUrl);

const backendPort = process.env.E2E_BACKEND_PORT ?? "8010";
const frontendOrigin = process.env.E2E_FRONTEND_ORIGIN ?? "http://127.0.0.1:5174";
const backendEnv = {
  ...process.env,
  CARRIER_PROVIDER: "fake",
  CORS_ORIGINS: frontendOrigin,
  DATABASE_URL: databaseUrl,
  ENVIRONMENT: "test",
  FAKE_CARRIER_BASE_URL: `http://127.0.0.1:${backendPort}/dev-labels`,
  JWT_SECRET_KEY: "whitfield-wms-e2e-secret-key-only-do-not-deploy",
  SEED_OWNER_EMAIL: "e2e.owner@example.com",
  SEED_OWNER_PASSWORD: "E2e-Test-Password-2026!",
  TEST_DATABASE_URL: databaseUrl
};

// Rebuild only the explicitly test-marked database so every run starts from
// the same users, products, balances, and empty operational queues.
runPython(["-m", "alembic", "-c", "backend/alembic.ini", "downgrade", "base"], backendEnv);
runPython(["-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"], backendEnv);
runPython(["-m", "backend.seed", "--environment", "test", "--demo"], backendEnv);

const backend = spawn(
  python,
  ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", backendPort],
  {
    cwd: workspaceRoot,
    env: backendEnv,
    stdio: "inherit"
  }
);

let stopping = false;
function stop(signal) {
  if (stopping) return;
  stopping = true;
  backend.kill(signal);
}

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));
backend.on("error", (error) => {
  throw error;
});
backend.on("exit", (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});
