import { spawn } from "node:child_process";
import { existsSync, watch } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const distEntry = path.join(root, "dist-terminal", "terminal.js");

let app = null;
let lastRestartAt = 0;
let watcher = null;
let shuttingDown = false;
let restartTimer = null;

function log(message) {
  process.stderr.write(`[dev] ${message}
`);
}

function stopApp() {
  if (!app) return;
  app.kill("SIGTERM");
  app = null;
}

function startApp() {
  if (!existsSync(distEntry) || shuttingDown) return;
  stopApp();
  log("starting terminal app");
  app = spawn(process.execPath, [distEntry], {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, NODE_ENV: "development" },
  });

  app.on("exit", (code, signal) => {
    if (shuttingDown) return;
    if (signal === "SIGTERM") return;
    log(`app exited (code=${code ?? "null"}, signal=${signal ?? "null"})`);
  });
}

function scheduleRestart(delay = 220) {
  const now = Date.now();
  if (now - lastRestartAt < 200) return;
  lastRestartAt = now;
  if (restartTimer) clearTimeout(restartTimer);
  restartTimer = setTimeout(() => {
    restartTimer = null;
    startApp();
  }, delay);
}

function startWatcher() {
  const distDir = path.dirname(distEntry);
  watcher = watch(distDir, { persistent: true }, (_eventType, filename) => {
    if (!filename) return;
    const name = String(filename);
    if (name === 'terminal.js' || name.startsWith('path-')) scheduleRestart(260);
  });
}

const vite = spawn("yarn", ["vite", "build", "--watch", "--mode", "terminal"], {
  cwd: root,
  stdio: ["inherit", "pipe", "pipe"],
  env: process.env,
});

vite.stdout.on("data", (chunk) => {
  const text = chunk.toString();
  process.stderr.write(text);
  if (text.includes('built in') || text.includes('built successfully')) {
    scheduleRestart(260);
  }
});

vite.stderr.on("data", (chunk) => {
  process.stderr.write(chunk.toString());
});

vite.on("exit", (code, signal) => {
  if (shuttingDown) return;
  log(`vite watcher exited (code=${code ?? "null"}, signal=${signal ?? "null"})`);
  process.exit(code ?? 1);
});

startWatcher();

function cleanup() {
  if (shuttingDown) return;
  shuttingDown = true;
  watcher?.close();
  if (restartTimer) clearTimeout(restartTimer);
  stopApp();
  vite.kill('SIGTERM');
}

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    cleanup();
    process.exit(0);
  });
}
