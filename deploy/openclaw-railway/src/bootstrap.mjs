/**
 * Bootstrap — auto-configure OpenClaw with Nunchi trading MCP server.
 *
 * Creates persistent directories, syncs workspace files, and generates
 * openclaw.json with our MCP server as the primary tool provider.
 */
import { existsSync, mkdirSync, copyFileSync, writeFileSync, readFileSync, readdirSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

const STATE_DIR = process.env.OPENCLAW_STATE_DIR || "/data/.openclaw";
const WORKSPACE_DIR = process.env.OPENCLAW_WORKSPACE_DIR || "/data/workspace";
const WORKSPACE_DEFAULTS = "/opt/workspace-defaults";
const CONFIG_PATH = join(STATE_DIR, "openclaw.json");

// AI provider -> OpenClaw config key mapping
const PROVIDER_MAP = {
  anthropic: { key: "apiKey", provider: "anthropic" },
  openai: { key: "openai-api-key", provider: "openai" },
  gemini: { key: "gemini-api-key", provider: "google" },
  google: { key: "gemini-api-key", provider: "google" },
  openrouter: { key: "apiKey", provider: "openrouter" },
  blockrun: { key: "blockrun-wallet-key", provider: "blockrun" },
};

export async function bootstrap() {
  console.log("[bootstrap] Starting auto-configuration...");

  // 1. Create persistent directories
  for (const dir of [
    STATE_DIR,
    WORKSPACE_DIR,
    join(STATE_DIR, "config"),
    join(WORKSPACE_DIR, "memory"),
    join(WORKSPACE_DIR, "skills"),
  ]) {
    mkdirSync(dir, { recursive: true });
  }

  // 2. Sync workspace files from defaults (don't overwrite existing)
  if (existsSync(WORKSPACE_DEFAULTS)) {
    for (const file of readdirSync(WORKSPACE_DEFAULTS)) {
      const dest = join(WORKSPACE_DIR, file);
      if (!existsSync(dest)) {
        copyFileSync(join(WORKSPACE_DEFAULTS, file), dest);
        console.log(`[bootstrap] Synced ${file} to workspace`);
      }
    }
  }

  // 3. Generate openclaw.json with our MCP server
  const config = buildConfig();
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  console.log("[bootstrap] Generated openclaw.json");

  // 4. Auto-approve builder fee (best-effort)
  if (process.env.HL_PRIVATE_KEY) {
    try {
      const mainnet = (process.env.HL_TESTNET || "true").toLowerCase() === "false";
      const args = mainnet ? ["builder", "approve", "--mainnet"] : ["builder", "approve"];
      execSync(`python3 -m cli.main ${args.join(" ")}`, {
        timeout: 30000,
        cwd: "/agent-cli",
        stdio: "pipe",
      });
      console.log("[bootstrap] Builder fee approval sent");
    } catch {
      // best-effort
    }
  }

  console.log("[bootstrap] Configuration complete");
}

function buildConfig() {
  const aiProvider = (process.env.AI_PROVIDER || "openrouter").toLowerCase();
  const aiKey = process.env.OPENROUTER_API_KEY || process.env.AI_API_KEY || "";
  const providerInfo = PROVIDER_MAP[aiProvider] || PROVIDER_MAP.anthropic;
  const model = process.env.OPENCLAW_MODEL || process.env.AI_MODEL || (aiProvider === "openrouter" ? "openrouter/auto" : "");

  // For blockrun/ClawRouter: use wallet key instead of API key.
  // x402 protocol — payment IS authentication, no API key needed.
  const isBlockrun = aiProvider === "blockrun";
  const credentialValue = isBlockrun
    ? (process.env.BLOCKRUN_WALLET_KEY || "")
    : aiKey;

  const config = {
    // Security (headless deployment)
    deviceAuth: false,
    insecureAuth: true,

    // Agent settings
    agentConcurrency: 10,
    subagentConcurrency: 12,

    // AI provider
    provider: providerInfo.provider,
    [providerInfo.key]: credentialValue,
    ...(model ? { model } : {}),

    // MCP servers — our trading CLI is the primary tool provider
    mcpServers: {
      nunchi_trading: {
        command: "python3",
        args: ["-m", "cli.main", "mcp", "serve"],
        cwd: "/agent-cli",
        env: {
          HL_PRIVATE_KEY: process.env.HL_PRIVATE_KEY || "",
          HL_TESTNET: process.env.HL_TESTNET || "true",
          ...(isBlockrun ? {
            BLOCKRUN_WALLET_KEY: process.env.BLOCKRUN_WALLET_KEY || "",
            BLOCKRUN_PROXY_PORT: process.env.BLOCKRUN_PROXY_PORT || "8402",
          } : {}),
        },
      },
    },

    // Workspace
    workspaceDir: WORKSPACE_DIR,
    stateDir: STATE_DIR,
  };

  // Telegram integration
  if (process.env.TELEGRAM_BOT_TOKEN) {
    config.channels = {
      telegram: {
        botToken: process.env.TELEGRAM_BOT_TOKEN,
        allowedUsers: process.env.TELEGRAM_USERNAME
          ? [process.env.TELEGRAM_USERNAME.replace("@", "")]
          : [],
      },
    };
  }

  return config;
}
