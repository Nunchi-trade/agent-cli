/**
 * Auto-onboard — resolve Telegram chat ID and send the ready message.
 *
 * Hermes loads config.yaml + .env on dashboard startup, so there's no
 * separate onboard CLI step (unlike OpenClaw). This module exists only to
 * (a) detect the operator's Telegram chat ID by username and (b) post the
 * ready message once per fresh credential set.
 */
const { existsSync, readFileSync, writeFileSync } = require("fs");
const { join } = require("path");
const https = require("https");

const HERMES_HOME = process.env.HERMES_HOME || "/data/.hermes";
const FINGERPRINT_PATH = join(HERMES_HOME, ".env-fingerprint");

async function autoOnboard() {
  const aiProvider = process.env.AI_PROVIDER;
  const aiKey = process.env.AI_API_KEY;

  if (!aiProvider || !aiKey) {
    console.log("[onboard] No AI_PROVIDER or AI_API_KEY set, skipping auto-onboard");
    return;
  }

  const fingerprint = computeFingerprint();
  if (existsSync(FINGERPRINT_PATH)) {
    const stored = readFileSync(FINGERPRINT_PATH, "utf-8").trim();
    if (stored === fingerprint) {
      console.log("[onboard] Environment unchanged, skipping onboard");
      return;
    }
  }

  console.log("[onboard] Running auto-onboard...");

  let telegramChatId = null;
  if (process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_USERNAME) {
    try {
      telegramChatId = await resolveTelegramChatId(
        process.env.TELEGRAM_BOT_TOKEN,
        process.env.TELEGRAM_USERNAME,
      );
      if (telegramChatId) {
        console.log(`[onboard] Resolved Telegram chat ID: ${telegramChatId}`);
        const userMd = `# User\n\nTelegram chat ID: ${telegramChatId}\nUsername: ${process.env.TELEGRAM_USERNAME}\n`;
        writeFileSync(join(HERMES_HOME, "workspace", "USER.md"), userMd);
      }
    } catch (err) {
      console.warn("[onboard] Could not resolve Telegram chat ID:", err.message);
    }
  }

  if (process.env.TELEGRAM_BOT_TOKEN) {
    try {
      await sendTelegramMessage(
        process.env.TELEGRAM_BOT_TOKEN,
        "Nunchi Hermes agent is ready. Say 'hl apex run' to start autonomous trading, or 'hl radar once' to scan for opportunities.",
        telegramChatId,
      );
      console.log("[onboard] Sent ready message to Telegram");
    } catch (err) {
      console.warn("[onboard] Could not send Telegram message:", err.message);
    }
  }

  writeFileSync(FINGERPRINT_PATH, fingerprint);
  console.log("[onboard] Onboarding complete");
}

function computeFingerprint() {
  const crypto = require("crypto");
  const parts = [
    process.env.AI_PROVIDER || "",
    (process.env.AI_API_KEY || "").slice(-8),
    process.env.TELEGRAM_BOT_TOKEN ? "tg" : "",
    process.env.HL_TESTNET || "true",
  ];
  return crypto.createHash("sha256").update(parts.join("|")).digest("hex").slice(0, 16);
}

async function resolveTelegramChatId(botToken, username) {
  const cleanUsername = username.replace("@", "").toLowerCase();
  const data = await fetchJson(`https://api.telegram.org/bot${botToken}/getUpdates?limit=50`);
  if (!data.ok || !data.result) return null;

  for (const update of data.result) {
    const msg = update.message || update.my_chat_member;
    if (!msg || !msg.from) continue;
    if ((msg.from.username || "").toLowerCase() === cleanUsername) {
      return msg.chat.id;
    }
  }
  return null;
}

async function sendTelegramMessage(botToken, text, chatId = null) {
  if (!chatId) {
    const data = await fetchJson(`https://api.telegram.org/bot${botToken}/getUpdates?limit=1`);
    if (!data.ok || !data.result || data.result.length === 0) return;
    chatId = data.result[0].message?.chat?.id;
  }
  if (!chatId) return;

  await fetchJson(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}

function fetchJson(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const req = https.request(url, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      timeout: 10000,
    }, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve({ ok: false });
        }
      });
    });
    req.on("error", reject);
    if (opts.body) req.write(opts.body);
    req.end();
  });
}

module.exports = { autoOnboard };
