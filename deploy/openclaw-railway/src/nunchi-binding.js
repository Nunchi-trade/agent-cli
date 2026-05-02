/**
 * Nunchi dashboard binding for Railway deployments.
 *
 * The desktop app creates a short-lived pairing session and passes it into
 * Railway as environment variables. This module emits the first heartbeat to
 * the relay/local bind endpoint and prints the auth code the user must confirm
 * in Agent Studio.
 */
const crypto = require("crypto");
const { existsSync, mkdirSync, readFileSync, writeFileSync } = require("fs");
const { dirname, join } = require("path");

const STATE_DIR = process.env.OPENCLAW_STATE_DIR || "/data/.openclaw";
const AUTH_CODE_PATH = join(STATE_DIR, ".nunchi-auth-code");

function hasNunchiBindingConfig() {
  return Boolean(
    process.env.NUNCHI_ACCOUNT_ID &&
    process.env.NUNCHI_AGENT_ID &&
    process.env.NUNCHI_BINDING_SESSION_ID &&
    process.env.NUNCHI_BINDING_CODE &&
    bindingUrl(),
  );
}

function bindingUrl() {
  if (process.env.NUNCHI_BIND_URL) return process.env.NUNCHI_BIND_URL;
  if (!process.env.NUNCHI_RELAY_URL) return "";
  const base = process.env.NUNCHI_RELAY_URL.replace(/\/+$/, "");
  return `${base}/agents/bind`;
}

function getNunchiAuthCode() {
  if (process.env.NUNCHI_AGENT_AUTH_CODE) return process.env.NUNCHI_AGENT_AUTH_CODE;
  try {
    if (existsSync(AUTH_CODE_PATH)) return readFileSync(AUTH_CODE_PATH, "utf-8").trim();
    const code = `NUN-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
    mkdirSync(dirname(AUTH_CODE_PATH), { recursive: true });
    writeFileSync(AUTH_CODE_PATH, code);
    return code;
  } catch {
    return `NUN-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
  }
}

function getNunchiBindingSummary() {
  if (!hasNunchiBindingConfig()) return null;
  return {
    agentId: process.env.NUNCHI_AGENT_ID,
    accountId: process.env.NUNCHI_ACCOUNT_ID,
    bindingSessionId: process.env.NUNCHI_BINDING_SESSION_ID,
    bindingUrl: bindingUrl(),
    authCode: getNunchiAuthCode(),
    templateVersion: process.env.NUNCHI_TEMPLATE_VERSION || "openclaw-railway-v1",
  };
}

function bindingExpiresAtMs() {
  const raw = process.env.NUNCHI_BINDING_EXPIRES_AT_MS;
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

async function emitNunchiBindingEvent(event = {}) {
  const url = bindingUrl();
  if (!hasNunchiBindingConfig()) {
    console.log("[nunchi] Pairing env not set; skipping dashboard binding heartbeat");
    return null;
  }

  const authCode = getNunchiAuthCode();
  const body = {
    agentId: process.env.NUNCHI_AGENT_ID,
    accountId: process.env.NUNCHI_ACCOUNT_ID,
    bindingSessionId: process.env.NUNCHI_BINDING_SESSION_ID,
    bindingCode: process.env.NUNCHI_BINDING_CODE,
    bindingExpiresAtMs: bindingExpiresAtMs(),
    authCode,
    source: "railway-openclaw",
    event: "heartbeat",
    phase: "starting",
    snapshot: {
      ready: true,
      platform: "railway",
      template: "openclaw-railway",
      templateVersion: process.env.NUNCHI_TEMPLATE_VERSION || "openclaw-railway-v1",
      publicUrl: process.env.RAILWAY_PUBLIC_DOMAIN
        ? `https://${process.env.RAILWAY_PUBLIC_DOMAIN}`
        : null,
    },
    ...event,
  };

  const headers = { "content-type": "application/json" };
  if (process.env.NUNCHI_RELAY_TOKEN) {
    headers.authorization = `Bearer ${process.env.NUNCHI_RELAY_TOKEN}`;
  }

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Nunchi bind failed ${res.status}: ${text}`);
  }
  console.log(`[nunchi] Pairing heartbeat sent for ${body.agentId}`);
  console.log(`[nunchi] Confirm auth code in Agent Studio: ${authCode}`);
  return { status: res.status, body: text, authCode };
}

module.exports = {
  bindingUrl,
  emitNunchiBindingEvent,
  bindingExpiresAtMs,
  getNunchiAuthCode,
  getNunchiBindingSummary,
  hasNunchiBindingConfig,
};
