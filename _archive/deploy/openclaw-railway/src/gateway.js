/**
 * Gateway lifecycle management — spawn, monitor, and restart the OpenClaw gateway.
 */
const { spawn } = require("child_process");
const http = require("http");

const GATEWAY_HOST = process.env.INTERNAL_GATEWAY_HOST || "127.0.0.1";
const GATEWAY_PORT = parseInt(process.env.INTERNAL_GATEWAY_PORT || "18789", 10);
const OPENCLAW_ENTRY = process.env.OPENCLAW_ENTRY || "/openclaw/dist/entry.js";
const STATE_DIR = process.env.OPENCLAW_STATE_DIR || "/data/.openclaw";

let gatewayProcess = null;

function startGateway() {
  if (gatewayProcess && !gatewayProcess.killed) {
    console.log("[gateway] Already running (pid=%d)", gatewayProcess.pid);
    return;
  }

  console.log("[gateway] Starting OpenClaw gateway...");

  gatewayProcess = spawn("node", [OPENCLAW_ENTRY, "gateway", "--port", String(GATEWAY_PORT)], {
    env: {
      ...process.env,
      OPENCLAW_STATE_DIR: STATE_DIR,
      OPENCLAW_WORKSPACE_DIR: process.env.OPENCLAW_WORKSPACE_DIR || "/data/workspace",
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
    // Redact tokens in logs
    if (line) console.error(`[gateway] ${redactTokens(line)}`);
  });

  gatewayProcess.on("exit", (code, signal) => {
    console.log(`[gateway] Exited (code=${code}, signal=${signal})`);
    gatewayProcess = null;
  });

  console.log("[gateway] Spawned (pid=%d)", gatewayProcess.pid);
}

async function waitForGatewayReady(timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      await httpGet(`http://${GATEWAY_HOST}:${GATEWAY_PORT}/health`);
      return true;
    } catch {
      await sleep(500);
    }
  }
  throw new Error(`Gateway did not become ready within ${timeoutMs}ms`);
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

// Helpers

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
