/**
 * Railway entrypoint — health check + reverse proxy to OpenClaw gateway.
 *
 * Flow:
 *   1. Run bootstrap (auto-configure OpenClaw + MCP + Telegram)
 *   2. Start OpenClaw gateway as child process
 *   3. Serve health checks + proxy all other traffic to gateway
 */
const express = require("express");
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { spawn } = require("child_process");
const httpProxy = require("http-proxy");
const { bootstrap } = require("./bootstrap.mjs");
const { startGateway, waitForGatewayReady, getGatewayProcess } = require("./gateway");
const { autoOnboard } = require("./onboard");
const { readStatus, readStrategies } = require("./status");

const app = express();
const PORT = parseInt(process.env.PORT || "8080", 10);
const GATEWAY_HOST = process.env.INTERNAL_GATEWAY_HOST || "127.0.0.1";
const GATEWAY_PORT = parseInt(process.env.INTERNAL_GATEWAY_PORT || "18789", 10);
const START_TIME = Date.now();
const AGENT_CLI_DIR = "/agent-cli";
const DATA_DIR = process.env.DATA_DIR || "/data";

let mcpProcess = null;

function startMCPServer() {
  if (mcpProcess && !mcpProcess.killed) {
    console.log("[mcp] Already running (pid=%d)", mcpProcess.pid);
    return;
  }

  console.log("[mcp] Starting MCP server...");

  mcpProcess = spawn("python3", ["-m", "cli.main", "mcp", "serve", "--transport", "stdio"], {
    cwd: AGENT_CLI_DIR,
    env: {
      ...process.env,
      PYTHONPATH: AGENT_CLI_DIR,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });

  mcpProcess.stdout.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`[mcp] ${line}`);
  });

  mcpProcess.stderr.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.error(`[mcp] ${line}`);
  });

  mcpProcess.on("exit", (code, signal) => {
    console.log(`[mcp] Exited (code=${code}, signal=${signal})`);
    mcpProcess = null;
  });

  console.log("[mcp] Spawned (pid=%d)", mcpProcess.pid);
}

// Proxy to OpenClaw gateway
const proxy = httpProxy.createProxyServer({
  target: `http://${GATEWAY_HOST}:${GATEWAY_PORT}`,
  ws: true,
  changeOrigin: true,
});

proxy.on("error", (err, req, res) => {
  if (res.writeHead) {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "gateway_unavailable", message: err.message }));
  }
});

// Health check
app.get("/health", (req, res) => {
  const gw = getGatewayProcess();
  res.json({
    status: "ok",
    uptime_s: Math.floor((Date.now() - START_TIME) / 1000),
    gateway_alive: gw ? !gw.killed : false,
    gateway_pid: gw ? gw.pid : null,
    mcp_alive: mcpProcess ? !mcpProcess.killed : false,
    mcp_pid: mcpProcess ? mcpProcess.pid : null,
  });
});

// CORS middleware for /api/* routes
const CORS_ORIGIN = process.env.CORS_ORIGIN || "*";
app.use("/api", (req, res, next) => {
  res.header("Access-Control-Allow-Origin", CORS_ORIGIN);
  res.header("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (req.method === "OPTIONS") return res.sendStatus(204);
  next();
});

// Trading status (human-readable, calls hl CLI directly)
app.get("/status", async (req, res) => {
  const { execSync } = require("child_process");
  try {
    const output = execSync("python3 -m cli.main apex status", {
      timeout: 10000,
      encoding: "utf-8",
      cwd: "/agent-cli",
    });
    res.type("text/plain").send(output);
  } catch (e) {
    res.type("text/plain").send(e.stdout || e.stderr || e.message);
  }
});

// API: Agent status (JSON, for UI)
app.get("/api/status", (req, res) => {
  res.json(readStatus());
});

// API: Strategy catalog
app.get("/api/strategies", (req, res) => {
  res.json(readStrategies());
});

// API: SSE feed — polls status every 2s
app.get("/api/feed", (req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    Connection: "keep-alive",
  });

  let lastTick = -1;
  const interval = setInterval(() => {
    try {
      const status = readStatus();
      const tick = status.tick_count || 0;
      if (tick !== lastTick) {
        lastTick = tick;
        res.write(`data: ${JSON.stringify(status)}\n\n`);
      }
    } catch {
      // ignore read errors
    }
  }, 2000);

  req.on("close", () => clearInterval(interval));
});

// API: Install/update Nunchi trading skill
app.post("/api/skill/install", express.json(), (req, res) => {
  const { execSync } = require("child_process");
  try {
    // Verify agent-cli is available by checking strategies
    const output = execSync("python3 -m cli.api.status_reader strategies", {
      timeout: 10000,
      encoding: "utf-8",
      cwd: "/agent-cli",
    });
    const data = JSON.parse(output.trim());
    const count = Object.keys(data.strategies || {}).length;
    res.json({ installed: true, strategies: count, tools: 13 });
  } catch (e) {
    res.status(500).json({ installed: false, error: e.message });
  }
});

// API: Pause agent
app.post("/api/pause", (req, res) => {
  const gw = getGatewayProcess();
  if (gw && !gw.killed) {
    try {
      process.kill(gw.pid, "SIGSTOP");
      res.json({ status: "paused" });
    } catch (e) {
      res.status(500).json({ error: e.message });
    }
  } else {
    res.status(409).json({ error: "No running agent to pause" });
  }
});

// API: Resume agent
app.post("/api/resume", (req, res) => {
  const gw = getGatewayProcess();
  if (gw && !gw.killed) {
    try {
      process.kill(gw.pid, "SIGCONT");
      res.json({ status: "resumed" });
    } catch (e) {
      res.status(500).json({ error: e.message });
    }
  } else {
    res.status(409).json({ error: "No paused agent to resume" });
  }
});

// API: Configure agent (write config override)
app.post("/api/configure", express.json(), (req, res) => {
  const configPath = path.join(DATA_DIR, "apex", "config-override.json");
  try {
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.writeFileSync(configPath, JSON.stringify(req.body, null, 2));
    res.json({ status: "ok", applied_at: "next_tick" });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// API: Trade history
app.get("/api/trades", (req, res) => {
  const limit = parseInt(req.query.limit || "50", 10);
  try {
    const output = execSync(
      `python3 -m cli.api.status_reader trades --data-dir ${DATA_DIR} --limit ${limit}`,
      { timeout: 10000, encoding: "utf-8", cwd: AGENT_CLI_DIR, stdio: ["pipe", "pipe", "pipe"] }
    );
    res.json(JSON.parse(output.trim()));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// API: REFLECT reports
app.get("/api/reflect", (req, res) => {
  try {
    const output = execSync(
      `python3 -m cli.api.status_reader reflect --data-dir ${DATA_DIR}`,
      { timeout: 10000, encoding: "utf-8", cwd: AGENT_CLI_DIR, stdio: ["pipe", "pipe", "pipe"] }
    );
    res.json(JSON.parse(output.trim()));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// API: RADAR (scanner) history
app.get("/api/scanner", (req, res) => {
  try {
    const output = execSync(
      `python3 -m cli.api.status_reader radar --data-dir ${DATA_DIR}`,
      { timeout: 10000, encoding: "utf-8", cwd: AGENT_CLI_DIR, stdio: ["pipe", "pipe", "pipe"] }
    );
    res.json(JSON.parse(output.trim()));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// API: Journal entries
app.get("/api/journal", (req, res) => {
  const limit = parseInt(req.query.limit || "50", 10);
  try {
    const output = execSync(
      `python3 -m cli.api.status_reader journal --data-dir ${DATA_DIR} --limit ${limit}`,
      { timeout: 10000, encoding: "utf-8", cwd: AGENT_CLI_DIR, stdio: ["pipe", "pipe", "pipe"] }
    );
    res.json(JSON.parse(output.trim()));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Everything else proxies to OpenClaw gateway
app.use((req, res) => {
  proxy.web(req, res);
});

// WebSocket upgrade
const server = app.listen(PORT, async () => {
  console.log(`[server] Listening on :${PORT}`);

  try {
    // Step 0: Wipe stale openclaw config so bootstrap regenerates it cleanly
    const configPath = `${process.env.OPENCLAW_STATE_DIR || "/data/.openclaw"}/openclaw.json`;
    try {
      fs.unlinkSync(configPath);
      console.log("[server] Cleared stale openclaw.json");
    } catch {
      // file may not exist yet
    }

    // Step 1: Start MCP server first (so it's available when OpenClaw starts)
    startMCPServer();
    await new Promise(resolve => setTimeout(resolve, 2000)); // Wait for MCP server to start

    // Step 2: Auto-onboard first (it may overwrite config)
    await autoOnboard();

    // Step 3: Bootstrap after onboard so our config is the final one
    await bootstrap();

    // Step 4: Run doctor fix to clean up any invalid config keys
    try {
      execSync("openclaw doctor --fix", {
        timeout: 30000,
        stdio: "pipe",
        env: { ...process.env, OPENCLAW_STATE_DIR: process.env.OPENCLAW_STATE_DIR || "/data/.openclaw" },
      });
      console.log("[server] OpenClaw doctor fix applied");
    } catch {
      // best-effort
    }

    // Step 5: Start OpenClaw gateway
    startGateway();
    await waitForGatewayReady();
    console.log("[server] OpenClaw gateway is ready");
  } catch (err) {
    console.error("[server] Startup error:", err.message);
    // Keep server running for health checks even if gateway fails
  }
});

server.on("upgrade", (req, socket, head) => {
  proxy.ws(req, socket, head);
});

// Graceful shutdown
function shutdown(signal) {
  console.log(`[server] ${signal} received, shutting down`);
  const gw = getGatewayProcess();
  if (gw && !gw.killed) {
    gw.kill("SIGTERM");
    setTimeout(() => {
      if (!gw.killed) gw.kill("SIGKILL");
    }, 10000);
  }
  if (mcpProcess && !mcpProcess.killed) {
    mcpProcess.kill("SIGTERM");
    setTimeout(() => {
      if (!mcpProcess.killed) mcpProcess.kill("SIGKILL");
    }, 5000);
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 15000);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
