/**
 * Gateway lifecycle — spawn, monitor, and restart the Hermes dashboard server.
 *
 * `hermes dashboard` runs a FastAPI HTTP server (default :9119). With
 * --insecure --no-open it's safe to bind to a non-localhost interface inside
 * the container; the Express front door at $PORT is what's actually exposed
 * to the public network.
 */
const { spawn } = require("child_process");
const http = require("http");

const GATEWAY_HOST = process.env.INTERNAL_GATEWAY_HOST || "127.0.0.1";
const GATEWAY_PORT = parseInt(process.env.INTERNAL_GATEWAY_PORT || "9119", 10);
const HERMES_BIN = process.env.HERMES_BIN || "/usr/local/bin/hermes";
const HERMES_HOME = process.env.HERMES_HOME || "/data/.hermes";

let gatewayProcess = null;

function startGateway() {
  if (gatewayProcess && !gatewayProcess.killed) {
    console.log("[gateway] Already running (pid=%d)", gatewayProcess.pid);
    return;
  }

  console.log("[gateway] Starting hermes dashboard...");

  const args = [
    "dashboard",
    "--host", GATEWAY_HOST,
    "--port", String(GATEWAY_PORT),
    "--no-open",
  ];
  // Hermes refuses non-localhost binds without --insecure (it exposes API
  // keys). Inside a container that's the expected deployment shape — the
  // Express wrapper is the public surface, not the dashboard directly.
  if (GATEWAY_HOST !== "127.0.0.1" && GATEWAY_HOST !== "localhost") {
    args.push("--insecure");
  }

  gatewayProcess = spawn(HERMES_BIN, args, {
    env: {
      ...process.env,
      HERMES_HOME,
      NODE_ENV: "production",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  gatewayProcess.stdout.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`[gateway] ${line}`);
  });

  gatewayProcess.stderr.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.error(`[gateway] ${redactTokens(line)}`);
  });

  gatewayProcess.on("exit", (code, signal) => {
    console.log(`[gateway] Exited (code=${code}, signal=${signal})`);
    gatewayProcess = null;
  });

  console.log("[gateway] Spawned (pid=%d)", gatewayProcess.pid);
}

async function waitForGatewayReady(timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      await httpGet(`http://${GATEWAY_HOST}:${GATEWAY_PORT}/`);
      return true;
    } catch {
      await sleep(500);
    }
  }
  throw new Error(`Hermes dashboard did not become ready within ${timeoutMs}ms`);
}

function getGatewayProcess() {
  return gatewayProcess;
}

function restartGateway() {
  if (gatewayProcess && !gatewayProcess.killed) {
    gatewayProcess.kill("SIGTERM");
    setTimeout(() => {
      if (gatewayProcess && !gatewayProcess.killed) {
        gatewayProcess.kill("SIGKILL");
      }
    }, 5000);
  }
  setTimeout(() => startGateway(), 1500);
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    http.get(url, { timeout: 3000 }, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => resolve(body));
    }).on("error", reject);
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function redactTokens(str) {
  return str.replace(/(?:sk-[a-zA-Z0-9-]{10,}|[a-f0-9]{64})/g, "[REDACTED]");
}

module.exports = { startGateway, waitForGatewayReady, getGatewayProcess, restartGateway };
