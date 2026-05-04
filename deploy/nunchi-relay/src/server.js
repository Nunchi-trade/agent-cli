const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const PORT = parseInt(process.env.PORT || "8080", 10);
const HOST = process.env.HOST || "0.0.0.0";
const STATE_PATH = process.env.NUNCHI_RELAY_STATE_PATH || "/data/nunchi-relay-sessions.json";
const SESSION_TTL_MS = parseInt(process.env.NUNCHI_RELAY_SESSION_TTL_MS || "600000", 10);
const CORS_ORIGIN = process.env.CORS_ORIGIN || "*";
const MAX_BODY_BYTES = 128 * 1024;

const sessions = new Map();
const startedAtMs = Date.now();

loadState();

const server = http.createServer(async (req, res) => {
  setCorsHeaders(res);
  if (req.method === "OPTIONS") return sendJson(res, 204, {});

  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "127.0.0.1"}`);

    if (req.method === "GET" && url.pathname === "/health") {
      pruneExpired();
      return sendJson(res, 200, {
        ok: true,
        service: "nunchi-agent-binding-relay",
        uptime_s: Math.floor((Date.now() - startedAtMs) / 1000),
        sessions: sessions.size,
      });
    }

    if (req.method === "POST" && url.pathname === "/agents/bind") {
      const body = await readJsonBody(req);
      return handleBindPost(res, body);
    }

    const sessionMatch = url.pathname.match(/^\/agents\/bind\/([^/]+)$/);
    if (req.method === "GET" && sessionMatch) {
      const query = Object.fromEntries(url.searchParams.entries());
      return handleBindGet(res, sessionMatch[1], query);
    }

    const confirmMatch = url.pathname.match(/^\/agents\/bind\/([^/]+)\/confirm$/);
    if (req.method === "POST" && confirmMatch) {
      const body = await readJsonBody(req);
      return handleBindConfirm(res, confirmMatch[1], body);
    }

    return sendJson(res, 404, { ok: false, error: "not_found" });
  } catch (err) {
    const message = err && typeof err.message === "string" ? err.message : "request failed";
    const status = message === "request body too large" ? 413 : 400;
    return sendJson(res, status, { ok: false, error: message });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[nunchi-relay] listening on ${HOST}:${PORT}`);
});

function handleBindPost(res, body) {
  const event = normalizeBindingEvent(body);
  const issue = validateBindingEvent(event);
  if (issue) return sendJson(res, 400, { ok: false, error: issue });

  const now = Date.now();
  const expiresAtMs = event.bindingExpiresAtMs || now + SESSION_TTL_MS;
  const existing = sessions.get(event.bindingSessionId);
  const codeHash = hashCode(event.bindingCode);

  if (existing) {
    const mismatch =
      existing.bindingCodeHash !== codeHash ||
      existing.accountId !== event.accountId ||
      existing.agentId !== event.agentId;
    if (mismatch) {
      return sendJson(res, 409, { ok: false, error: "binding session identity mismatch" });
    }
    if (isExpired(existing, now)) {
      return sendJson(res, 410, { ok: false, error: "binding session expired" });
    }
  }

  const session = {
    bindingSessionId: event.bindingSessionId,
    bindingCodeHash: codeHash,
    accountId: event.accountId,
    agentId: event.agentId,
    createdAtMs: existing?.createdAtMs || now,
    updatedAtMs: now,
    expiresAtMs,
    eventCount: (existing?.eventCount || 0) + 1,
    latestEvent: stripSecrets({
      ...event,
      bindingExpiresAtMs: expiresAtMs,
      event: event.event || "heartbeat",
      state: event.state || "running",
      phase: event.phase || "running",
    }),
  };

  sessions.set(session.bindingSessionId, session);
  saveState();

  return sendJson(res, 200, {
    ok: true,
    status: event.authCode ? "code-received" : "waiting",
    bindingSessionId: session.bindingSessionId,
    updatedAtMs: session.updatedAtMs,
    expiresAtMs: session.expiresAtMs,
    eventCount: session.eventCount,
  });
}

function handleBindGet(res, sessionId, input) {
  const result = readSession(sessionId, input);
  if (!result.ok) return sendJson(res, result.status, { ok: false, error: result.error });
  return sendJson(res, 200, {
    ok: true,
    status: result.session.latestEvent.authCode ? "code-received" : "waiting",
    bindingSessionId: result.session.bindingSessionId,
    updatedAtMs: result.session.updatedAtMs,
    expiresAtMs: result.session.expiresAtMs,
    eventCount: result.session.eventCount,
    event: {
      ...result.session.latestEvent,
      bindingCode: result.bindingCode,
    },
  });
}

function handleBindConfirm(res, sessionId, body) {
  const result = readSession(sessionId, body || {});
  if (!result.ok) return sendJson(res, result.status, { ok: false, error: result.error });
  result.session.latestEvent = {
    ...result.session.latestEvent,
    event: "bound",
    state: "bound",
    phase: "confirmed",
    authCode: null,
  };
  result.session.updatedAtMs = Date.now();
  sessions.set(result.session.bindingSessionId, result.session);
  saveState();
  return sendJson(res, 200, { ok: true, status: "bound", bindingSessionId: result.session.bindingSessionId });
}

function readSession(sessionId, input) {
  const session = sessions.get(String(sessionId || "").trim());
  if (!session) return { ok: false, status: 404, error: "binding session not found" };
  if (isExpired(session)) return { ok: false, status: 410, error: "binding session expired" };

  const bindingCode = String(input.bindingCode || input.binding_code || "").trim();
  if (!bindingCode || hashCode(bindingCode) !== session.bindingCodeHash) {
    return { ok: false, status: 401, error: "invalid binding code" };
  }

  const accountId = String(input.accountId || input.account_id || "").trim();
  const agentId = String(input.agentId || input.agent_id || "").trim();
  if (accountId && accountId !== session.accountId) {
    return { ok: false, status: 409, error: "account mismatch" };
  }
  if (agentId && agentId !== session.agentId) {
    return { ok: false, status: 409, error: "agent mismatch" };
  }

  return { ok: true, session, bindingCode };
}

function normalizeBindingEvent(input) {
  const value = input && typeof input === "object" ? input : {};
  return {
    agentId: stringField(value, "agentId", "agent_id"),
    accountId: stringField(value, "accountId", "account_id"),
    bindingSessionId: stringField(value, "bindingSessionId", "binding_session_id"),
    bindingCode: stringField(value, "bindingCode", "binding_code"),
    authCode: stringField(value, "authCode", "auth_code"),
    event: stringField(value, "event"),
    state: stringField(value, "state"),
    phase: stringField(value, "phase"),
    source: stringField(value, "source"),
    pid: numberField(value, "pid"),
    stream: stringField(value, "stream"),
    line: stringField(value, "line"),
    message: stringField(value, "message"),
    bindingExpiresAtMs: numberField(value, "bindingExpiresAtMs", "binding_expires_at_ms"),
    snapshot: value.snapshot && typeof value.snapshot === "object" ? value.snapshot : undefined,
  };
}

function validateBindingEvent(event) {
  if (!event.agentId) return "agentId is required";
  if (!event.accountId) return "accountId is required";
  if (!event.bindingSessionId) return "bindingSessionId is required";
  if (!event.bindingCode) return "bindingCode is required";
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(event.agentId)) {
    return "agentId may only contain letters, numbers, '_' and '-'";
  }
  if (!/^[a-zA-Z0-9_-]{1,160}$/.test(event.bindingSessionId)) {
    return "bindingSessionId may only contain letters, numbers, '_' and '-'";
  }
  return null;
}

function stripSecrets(event) {
  const next = { ...event };
  delete next.bindingCode;
  return next;
}

function hashCode(code) {
  return crypto.createHash("sha256").update(String(code)).digest("hex");
}

function isExpired(session, now = Date.now()) {
  return Number.isFinite(session.expiresAtMs) && now > session.expiresAtMs;
}

function pruneExpired() {
  let changed = false;
  for (const [id, session] of sessions) {
    if (isExpired(session)) {
      sessions.delete(id);
      changed = true;
    }
  }
  if (changed) saveState();
}

function stringField(value, ...keys) {
  for (const key of keys) {
    const raw = value[key];
    if (raw == null) continue;
    const text = String(raw).trim();
    if (text) return text;
  }
  return undefined;
}

function numberField(value, ...keys) {
  for (const key of keys) {
    const raw = value[key];
    if (raw == null || raw === "") continue;
    const n = Number(raw);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

function loadState() {
  try {
    const raw = fs.readFileSync(STATE_PATH, "utf-8");
    const parsed = JSON.parse(raw);
    for (const item of parsed.sessions || []) {
      if (item.bindingSessionId && !isExpired(item)) {
        sessions.set(item.bindingSessionId, item);
      }
    }
    console.log(`[nunchi-relay] loaded ${sessions.size} sessions from ${STATE_PATH}`);
  } catch (err) {
    if (err.code !== "ENOENT") {
      console.warn(`[nunchi-relay] state load skipped: ${err.message}`);
    }
  }
}

function saveState() {
  try {
    fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true });
    const tmp = `${STATE_PATH}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ sessions: Array.from(sessions.values()) }, null, 2));
    fs.renameSync(tmp, STATE_PATH);
  } catch (err) {
    console.warn(`[nunchi-relay] state save failed: ${err.message}`);
  }
}

function setCorsHeaders(res) {
  res.setHeader("Access-Control-Allow-Origin", CORS_ORIGIN);
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
}

function sendJson(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  if (status === 204) return res.end();
  return res.end(JSON.stringify(body));
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", chunk => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      if (!raw.trim()) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error("invalid JSON body"));
      }
    });
    req.on("error", reject);
  });
}
