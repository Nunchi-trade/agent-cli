/**
 * Bootstrap — auto-configure Hermes Agent with the Nunchi trading MCP server.
 *
 * Hermes splits config across two files in HERMES_HOME:
 *   - .env         — provider API keys, TELEGRAM_BOT_TOKEN, etc.
 *   - config.yaml  — model.provider, mcp_servers, platform_toolsets
 *
 * We seed both from Railway env vars on startup. User edits to either file
 * are preserved across restarts (we only write keys we own).
 */
import { existsSync, mkdirSync, copyFileSync, writeFileSync, readFileSync, readdirSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";
import yaml from "js-yaml";

const HERMES_HOME = process.env.HERMES_HOME || "/data/.hermes";
const WORKSPACE_DIR = join(HERMES_HOME, "workspace");
const WORKSPACE_DEFAULTS = "/opt/workspace-defaults";
const CONFIG_PATH = join(HERMES_HOME, "config.yaml");
const ENV_PATH = join(HERMES_HOME, ".env");

// AI_PROVIDER → (Hermes provider name, env var Hermes reads the key from)
const PROVIDER_MAP = {
  anthropic:  { provider: "anthropic",  envKey: "ANTHROPIC_API_KEY" },
  openai:     { provider: "openrouter", envKey: "OPENAI_API_KEY" },
  openrouter: { provider: "openrouter", envKey: "OPENROUTER_API_KEY" },
  gemini:     { provider: "gemini",     envKey: "GEMINI_API_KEY" },
  google:     { provider: "gemini",     envKey: "GOOGLE_API_KEY" },
  nous:       { provider: "nous-api",   envKey: "NOUS_API_KEY" },
  zai:        { provider: "zai",        envKey: "GLM_API_KEY" },
  kimi:       { provider: "kimi-coding", envKey: "KIMI_API_KEY" },
  huggingface: { provider: "huggingface", envKey: "HF_TOKEN" },
};

export async function bootstrap() {
  console.log("[bootstrap] Starting auto-configuration...");

  for (const dir of [
    HERMES_HOME,
    WORKSPACE_DIR,
    join(HERMES_HOME, "skills"),
    join(HERMES_HOME, "memories"),
    join(HERMES_HOME, "logs"),
    join(HERMES_HOME, "sessions"),
  ]) {
    mkdirSync(dir, { recursive: true });
  }

  if (existsSync(WORKSPACE_DEFAULTS)) {
    for (const file of readdirSync(WORKSPACE_DEFAULTS)) {
      const dest = join(WORKSPACE_DIR, file);
      if (!existsSync(dest)) {
        copyFileSync(join(WORKSPACE_DEFAULTS, file), dest);
        console.log(`[bootstrap] Synced ${file} to workspace`);
      }
    }
  }

  writeEnvFile();
  writeConfigYaml();

  // Best-effort Hyperliquid builder fee approval (idempotent)
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

function writeEnvFile() {
  const aiProvider = (process.env.AI_PROVIDER || "anthropic").toLowerCase();
  const aiKey = process.env.AI_API_KEY || "";
  const providerInfo = PROVIDER_MAP[aiProvider] || PROVIDER_MAP.anthropic;

  const lines = [];
  if (aiKey) lines.push(`${providerInfo.envKey}=${aiKey}`);
  if (process.env.TELEGRAM_BOT_TOKEN) lines.push(`TELEGRAM_BOT_TOKEN=${process.env.TELEGRAM_BOT_TOKEN}`);
  if (process.env.DISCORD_BOT_TOKEN) lines.push(`DISCORD_BOT_TOKEN=${process.env.DISCORD_BOT_TOKEN}`);
  if (process.env.SLACK_BOT_TOKEN) lines.push(`SLACK_BOT_TOKEN=${process.env.SLACK_BOT_TOKEN}`);

  if (lines.length === 0) {
    console.log("[bootstrap] No credentials to write to .env");
    return;
  }

  writeFileSync(ENV_PATH, lines.join("\n") + "\n", { mode: 0o600 });
  console.log(`[bootstrap] Wrote ${lines.length} entries to .env`);
}

function writeConfigYaml() {
  const aiProvider = (process.env.AI_PROVIDER || "anthropic").toLowerCase();
  const providerInfo = PROVIDER_MAP[aiProvider] || PROVIDER_MAP.anthropic;

  const existing = existsSync(CONFIG_PATH)
    ? (yaml.load(readFileSync(CONFIG_PATH, "utf-8")) || {})
    : {};

  const config = {
    ...existing,
    model: {
      ...(existing.model || {}),
      provider: providerInfo.provider,
      ...(process.env.HERMES_MODEL ? { default: process.env.HERMES_MODEL } : {}),
    },
    mcp_servers: {
      ...(existing.mcp_servers || {}),
      nunchi_trading: {
        command: "python3",
        args: ["-m", "cli.main", "mcp", "serve"],
        cwd: "/agent-cli",
        env: {
          HL_PRIVATE_KEY: process.env.HL_PRIVATE_KEY || "",
          HL_TESTNET: process.env.HL_TESTNET || "true",
        },
      },
    },
  };

  if (process.env.TELEGRAM_BOT_TOKEN) {
    config.platform_toolsets = {
      ...(existing.platform_toolsets || {}),
      telegram: ["hermes-telegram"],
    };
  }

  writeFileSync(CONFIG_PATH, yaml.dump(config, { lineWidth: 120 }));
  console.log("[bootstrap] Wrote config.yaml");
}
