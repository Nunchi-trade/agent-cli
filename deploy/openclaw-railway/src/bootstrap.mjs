/**
 * Bootstrap — auto-configure OpenClaw (v2026.2.22) with the Nunchi trading MCP server.
 *
 * Creates persistent directories, syncs workspace files, writes a v2026.2.22-VALID
 * openclaw.json, and registers our stdio MCP server with mcporter (the MCP runtime
 * OpenClaw v2026.2.22 ships with).
 *
 * Schema source: openclaw v2026.2.22 (pinned tag), src/config/zod-schema*.ts.
 * The root config schema is `.strict()` (rejects unknown keys), composed in
 * src/config/zod-schema.ts -> OpenClawSchema. Every key emitted below maps to a
 * real schema field; see the inline citations.
 */
import { existsSync, mkdirSync, copyFileSync, writeFileSync, readdirSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

const STATE_DIR = process.env.OPENCLAW_STATE_DIR || "/data/.openclaw";
const WORKSPACE_DIR = process.env.OPENCLAW_WORKSPACE_DIR || "/data/workspace";
const WORKSPACE_DEFAULTS = "/opt/workspace-defaults";
const CONFIG_PATH = join(STATE_DIR, "openclaw.json");

// mcporter reads exactly one config file when MCPORTER_CONFIG is set (no home/project
// merge), so we pin an absolute path under the persistent state dir. This makes the
// `nunchi_trading` server reachable from `mcporter` regardless of the agent's cwd.
// Source: mcporter@0.7.3 dist/config.js resolveConfigPath() — explicit --config, then
// process.env.MCPORTER_CONFIG, then <root>/config/mcporter.json. docs/config.md
// "Config Resolution Order" #2: "If MCPORTER_CONFIG is set, only that file is used."
const MCPORTER_CONFIG_PATH = join(STATE_DIR, "mcporter.json");

// AI_PROVIDER (our deploy env) -> { envVar, modelRef } for OpenClaw v2026.2.22.
//
// provider key home: OpenClaw resolves provider API keys from provider-native ENV vars
// at runtime (src/agents/live-auth-keys.ts PROVIDER_API_KEY_CONFIG: anthropic ->
// ANTHROPIC_API_KEY, google -> GEMINI_API_KEY (+GOOGLE_API_KEY fallback), openai ->
// OPENAI_API_KEY; derived `${BASE}_API_KEY` for the rest; cf. src/config/io.ts which
// allowlists OPENAI_API_KEY/ANTHROPIC_API_KEY/GEMINI_API_KEY/OPENROUTER_API_KEY). There
// is NO top-level `apiKey`/`provider` key in OpenClawSchema, so the credential is passed
// via env (gateway.js forwards `...process.env` to the gateway child), NOT written to
// openclaw.json.
//
// model home: agents.defaults.model.primary (src/config/zod-schema.agent-defaults.ts
// AgentDefaultsSchema.model.primary: z.string()). Values are OpenClaw's own catalog
// defaults so the active provider matches the supplied key:
//   - anthropic alias "sonnet" -> "anthropic/claude-sonnet-4-6" (defaults.ts DEFAULT_MODEL_ALIASES)
//   - openai "gpt"             -> "openai/gpt-5.2"
//   - google/gemini            -> "google/gemini-3-pro-preview"
//   - openrouter               -> "openrouter/auto" (src/commands/onboard-auth.credentials.ts OPENROUTER_DEFAULT_MODEL_REF)
// If model.primary is omitted, OpenClaw falls back to DEFAULT_MODEL="claude-opus-4-6"
// (src/agents/defaults.ts) which is anthropic-only — wrong for non-anthropic keys —
// hence we always set it.
const PROVIDER_MAP = {
  anthropic: { envVar: "ANTHROPIC_API_KEY", modelRef: "anthropic/claude-sonnet-4-6" },
  openai: { envVar: "OPENAI_API_KEY", modelRef: "openai/gpt-5.2" },
  gemini: { envVar: "GEMINI_API_KEY", modelRef: "google/gemini-3-pro-preview" },
  google: { envVar: "GEMINI_API_KEY", modelRef: "google/gemini-3-pro-preview" },
  openrouter: { envVar: "OPENROUTER_API_KEY", modelRef: "openrouter/auto" },
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

  // 3. Make the AI provider key visible to OpenClaw under the env var its runtime
  //    resolver expects. Our deploy passes the generic AI_API_KEY; OpenClaw looks for
  //    the provider-native var (ANTHROPIC_API_KEY, etc.). Set it on this process so it
  //    propagates to the gateway child (gateway.js spawns with `...process.env`).
  //    Source: src/agents/live-auth-keys.ts collectProviderApiKeys().
  applyProviderKeyEnv();

  // 4. Register the nunchi_trading stdio MCP server with mcporter, and pin
  //    MCPORTER_CONFIG so the agent's `mcporter` calls resolve it.
  writeMcporterConfig();

  // 5. Generate a v2026.2.22-valid openclaw.json
  const config = buildConfig();
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  console.log("[bootstrap] Generated openclaw.json");

  // 6. Auto-approve builder fee (best-effort)
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

/** Resolve the deploy's AI_PROVIDER -> provider entry (defaults to anthropic). */
function resolveProvider() {
  const aiProvider = (process.env.AI_PROVIDER || "anthropic").toLowerCase();
  const providerInfo = PROVIDER_MAP[aiProvider];
  if (!providerInfo) {
    // blockrun/x402/ClawRouter is NOT a provider in OpenClaw v2026.2.22 (no source
    // support: grep of the pinned tree for "blockrun"/"x402"/"ClawRouter" is empty).
    // Do not fabricate config for it; fall back to anthropic so the gateway still boots.
    console.warn(
      `[bootstrap] AI_PROVIDER="${aiProvider}" is not supported by OpenClaw v2026.2.22; ` +
        "falling back to anthropic. (blockrun/x402 has no native OpenClaw provider.)",
    );
    return { aiProvider: "anthropic", providerInfo: PROVIDER_MAP.anthropic };
  }
  return { aiProvider, providerInfo };
}

/** Export the provider-native API-key env var (from generic AI_API_KEY) for the gateway. */
function applyProviderKeyEnv() {
  const { providerInfo } = resolveProvider();
  const aiKey = process.env.AI_API_KEY || "";
  if (!aiKey) {
    console.warn("[bootstrap] AI_API_KEY not set; OpenClaw will have no provider credential");
    return;
  }
  // Only set if the provider-native var isn't already provided explicitly.
  if (!process.env[providerInfo.envVar]?.trim()) {
    process.env[providerInfo.envVar] = aiKey;
    console.log(`[bootstrap] Exported ${providerInfo.envVar} for OpenClaw provider auth`);
  }
}

/**
 * Write mcporter's config registering the nunchi_trading stdio server, and pin
 * MCPORTER_CONFIG to it. mcporter config shape (mcporter@0.7.3 dist/config-schema.js
 * RawConfigSchema): { mcpServers: Record<name, { command: string|string[], args?: string[],
 * env?: Record<string,string> }> }. `mcpServers` is required even if empty.
 */
function writeMcporterConfig() {
  const isMainnet = (process.env.HL_TESTNET || "true").toLowerCase() === "false";
  const mcporterConfig = {
    mcpServers: {
      nunchi_trading: {
        // stdio server: our trading CLI's MCP entrypoint. Array args avoid shell quoting.
        command: "python3",
        args: ["-m", "cli.main", "mcp", "serve"],
        env: {
          // Static env for the spawned server. mcporter supports ${VAR} interpolation,
          // but we resolve at write-time from the deploy env for determinism.
          HL_PRIVATE_KEY: process.env.HL_PRIVATE_KEY || "",
          HL_TESTNET: isMainnet ? "false" : "true",
          // The MCP server runs from the trading CLI package dir.
          PYTHONPATH: "/agent-cli",
        },
      },
    },
  };
  writeFileSync(MCPORTER_CONFIG_PATH, JSON.stringify(mcporterConfig, null, 2));
  // Pin for every downstream `mcporter` invocation (forwarded to the gateway child via
  // `...process.env` in gateway.js). docs/config.md: MCPORTER_CONFIG => single-file mode.
  process.env.MCPORTER_CONFIG = MCPORTER_CONFIG_PATH;
  console.log(`[bootstrap] Registered nunchi_trading MCP server (${MCPORTER_CONFIG_PATH})`);
}

/**
 * Build a v2026.2.22-valid openclaw.json.
 *
 * Root keys used (all present in OpenClawSchema, src/config/zod-schema.ts):
 *   - gateway.controlUi.{allowInsecureAuth,dangerouslyDisableDeviceAuth}  (zod-schema.ts:423-424)
 *   - agents.defaults.{maxConcurrent,subagents.maxConcurrent,workspace,model.primary}
 *       (zod-schema.agent-defaults.ts: AgentDefaultsSchema)
 *   - channels.telegram.{botToken,dmPolicy,allowFrom}                     (zod-schema.providers-core.ts)
 *
 * Removed dead keys (rejected by the strict schema) and where they went:
 *   deviceAuth          -> gateway.controlUi.dangerouslyDisableDeviceAuth
 *   insecureAuth        -> gateway.controlUi.allowInsecureAuth
 *   agentConcurrency    -> agents.defaults.maxConcurrent
 *   subagentConcurrency -> agents.defaults.subagents.maxConcurrent
 *   provider, apiKey    -> dropped (provider-native ENV var + agents.defaults.model.primary)
 *   mcpServers          -> dropped (mcporter.json + MCPORTER_CONFIG, see writeMcporterConfig)
 *   workspaceDir        -> agents.defaults.workspace
 *   stateDir            -> dropped (OPENCLAW_STATE_DIR env, set by gateway.js)
 */
function buildConfig() {
  const { providerInfo } = resolveProvider();

  // Gateway port (deploy passes --port too via gateway.js; keep config consistent).
  const gatewayPort = Number.parseInt(process.env.INTERNAL_GATEWAY_PORT || "18789", 10);

  const config = {
    gateway: {
      // mode=local is REQUIRED to start the gateway: src/cli/gateway-cli/run.ts:212
      // blocks start unless gateway.mode==="local" (or --allow-unconfigured). We set it
      // here so the gateway boots from bootstrap config alone, independent of the
      // (best-effort, may-fail) `openclaw onboard` step in onboard.js.
      mode: "local",
      // Loopback bind — the wrapper (server.js) reverse-proxies to it; not exposed directly.
      bind: "loopback",
      port: gatewayPort,
      // Headless deployment: skip the Control UI device-identity + secure-context auth.
      // dangerouslyDisableDeviceAuth is the flag that actually disables device-identity
      // checks (src/security/audit.ts:405); allowInsecureAuth permits non-HTTPS control
      // contexts. Both are under gateway.controlUi (OpenClawSchema gateway.controlUi).
      controlUi: {
        allowInsecureAuth: true,
        dangerouslyDisableDeviceAuth: true,
      },
    },

    agents: {
      defaults: {
        // Concurrency (was agentConcurrency / subagentConcurrency).
        // src/config/agent-limits.ts reads agents.defaults.maxConcurrent and
        // agents.defaults.subagents.maxConcurrent.
        maxConcurrent: 10,
        subagents: {
          maxConcurrent: 12,
        },
        // Workspace (was workspaceDir). AgentDefaultsSchema.workspace: z.string().
        workspace: WORKSPACE_DIR,
        // Provider/model selection. AgentDefaultsSchema.model.primary: z.string().
        model: {
          primary: providerInfo.modelRef,
        },
      },
    },
  };
  // NOTE on the mcporter skill: we intentionally write NO top-level `skills` config.
  // The mcporter skill is BUNDLED with OpenClaw (/openclaw/skills/mcporter/SKILL.md) and
  // bundled skills are auto-included by default — src/agents/skills/config.ts
  // shouldIncludeSkill() only excludes a skill if skills.entries.<key>.enabled===false or
  // a non-empty skills.allowBundled allowlist omits it. With `skills` unset, the mcporter
  // skill loads automatically as long as its `mcporter` binary exists (it does: Dockerfile
  // `npm install -g mcporter@0.7.3`), per the skill's `requires.bins:["mcporter"]`
  // eligibility check. Adding `skills.allowBundled:["mcporter"]` would instead DISABLE every
  // other bundled skill, so we leave it unset.

  // Telegram integration. Channel config lives under channels.telegram
  // (TelegramConfigSchema, zod-schema.providers-core.ts):
  //   - botToken: z.string()                                    (line 138)
  //   - allowFrom: (string|number)[]  (NOT "allowedUsers")      (line 142)
  //   - dmPolicy: "pairing"|"allowlist"|"open"|"disabled"       (DmPolicySchema, default "pairing")
  //
  // IMPORTANT: Telegram authorization matches on NUMERIC sender/chat IDs, not @usernames
  // (confirmed by OpenClaw's own doctor: "Telegram allowFrom contains non-numeric entries
  // ...requires numeric sender IDs"). TELEGRAM_USERNAME is a @handle, which is NOT a valid
  // allowFrom entry. So:
  //   - If TELEGRAM_USERNAME is numeric (a chat ID), allowlist it directly.
  //   - Otherwise leave dmPolicy at its schema default ("pairing") and omit allowFrom; the
  //     user authorizes via the Telegram pairing flow. (onboard.js separately resolves the
  //     @handle -> numeric chat ID via getUpdates and records it in USER.md.)
  if (process.env.TELEGRAM_BOT_TOKEN) {
    const telegram = {
      botToken: process.env.TELEGRAM_BOT_TOKEN,
    };
    const rawUser = (process.env.TELEGRAM_USERNAME || "").replace("@", "").trim();
    if (rawUser && /^\d+$/.test(rawUser)) {
      // Numeric chat ID -> safe to allowlist.
      telegram.dmPolicy = "allowlist";
      telegram.allowFrom = [Number(rawUser)];
    }
    // else: dmPolicy defaults to "pairing" (omit allowFrom) — do not inject a non-numeric
    // @username that would authorize nobody.
    config.channels = { telegram };
  }

  return config;
}
