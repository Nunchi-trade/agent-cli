/**
 * Auto-onboard — configure OpenClaw with AI provider and Telegram on first deploy.
 */
const { execSync } = require("child_process");
const { existsSync, readFileSync, writeFileSync } = require("fs");
const { join } = require("path");
const https = require("https");
const { getNunchiBindingSummary } = require("./nunchi-binding");

const STATE_DIR = process.env.OPENCLAW_STATE_DIR || "/data/.openclaw";
const FINGERPRINT_PATH = join(STATE_DIR, ".env-fingerprint");

async function autoOnboard() {
  const aiProvider = process.env.AI_PROVIDER;
  const aiKey = process.env.AI_API_KEY;

  if (!aiProvider || !aiKey) {
    console.log("[onboard] No AI_PROVIDER or AI_API_KEY set, skipping auto-onboard");
    return;
  }

  // Check if already configured with same env
  const fingerprint = computeFingerprint();
  if (existsSync(FINGERPRINT_PATH)) {
    const stored = readFileSync(FINGERPRINT_PATH, "utf-8").trim();
    if (stored === fingerprint) {
      console.log("[onboard] Environment unchanged, skipping onboard");
      return;
    }
  }

  console.log("[onboard] Running auto-onboard...");

  try {
    // Run OpenClaw onboard
    execSync("openclaw onboard --non-interactive --accept-risk", {
      timeout: 60000,
      stdio: "pipe",
      env: {
        ...process.env,
        OPENCLAW_STATE_DIR: STATE_DIR,
      },
    });
    console.log("[onboard] OpenClaw onboard complete");
  } catch (err) {
    console.warn("[onboard] OpenClaw onboard failed (may already be configured):", err.message);
  }

  // Resolve Telegram chat ID if username provided
  if (process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_USERNAME) {
    try {
      const chatId = await resolveTelegramChatId(
        process.env.TELEGRAM_BOT_TOKEN,
        process.env.TELEGRAM_USERNAME,
      );
      if (chatId) {
        console.log(`[onboard] Resolved Telegram chat ID: ${chatId}`);
        // Write USER.md with chat ID
        const userMd = `# User\n\nTelegram chat ID: ${chatId}\nUsername: ${process.env.TELEGRAM_USERNAME}\n`;
        writeFileSync(
          join(process.env.OPENCLAW_WORKSPACE_DIR || "/data/workspace", "USER.md"),
          userMd,
        );
      }
    } catch (err) {
      console.warn("[onboard] Could not resolve Telegram chat ID:", err.message);
    }
  }

  // Send ready message to Telegram
  if (process.env.TELEGRAM_BOT_TOKEN) {
    try {
      const binding = getNunchiBindingSummary();
      const authLine = binding
        ? `\n\nNunchi auth code: ${binding.authCode}\nConfirm it in Agent Studio to finish binding ${binding.agentId}.`
        : "";
      await sendTelegramMessage(
        process.env.TELEGRAM_BOT_TOKEN,
        `Nunchi trading agent is ready. Say 'hl apex run' to start autonomous trading, or 'hl radar once' to scan for opportunities.${authLine}`,
      );
      console.log("[onboard] Sent ready message to Telegram");
    } catch (err) {
      console.warn("[onboard] Could not send Telegram message:", err.message);
    }
  }

  // Store fingerprint
  writeFileSync(FINGERPRINT_PATH, fingerprint);
  console.log("[onboard] Onboarding complete");
}

function computeFingerprint() {
  const crypto = require("crypto");
  const parts = [
    process.env.AI_PROVIDER || "",
    (process.env.AI_API_KEY || "").slice(-8),
    process.env.TELEGRAM_BOT_TOKEN ? "tg" : "",
    process.env.NUNCHI_BINDING_SESSION_ID || "",
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

async function sendTelegramMessage(botToken, text) {
  // Try to find a chat to send to
  const data = await fetchJson(`https://api.telegram.org/bot${botToken}/getUpdates?limit=1`);
  if (!data.ok || !data.result || data.result.length === 0) return;

  const chatId = data.result[0].message?.chat?.id;
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
