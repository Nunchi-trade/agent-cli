/**
 * Status reader — shells out to cli.api.status_reader for agent state.
 */
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const AGENT_CLI_DIR = "/agent-cli";
const DATA_DIR = process.env.DATA_DIR || "/data";

function readStatus() {
  try {
    const output = execSync(
      `python3 -m cli.api.status_reader status --data-dir ${DATA_DIR}`,
      { timeout: 10000, encoding: "utf-8", cwd: AGENT_CLI_DIR, stdio: ["pipe", "pipe", "pipe"] }
    );
    return JSON.parse(output.trim());
  } catch (err) {
    // Fallback: try reading apex state.json directly
    const apexState = path.join(DATA_DIR, "apex", "state.json");
    if (fs.existsSync(apexState)) {
      try {
        const state = JSON.parse(fs.readFileSync(apexState, "utf-8"));
        const active = (state.slots || []).filter((s) => s.status === "active");
        return {
          status: "running",
          engine: "apex",
          tick_count: state.tick_count || 0,
          daily_pnl: state.daily_pnl || 0,
          total_pnl: state.total_pnl || 0,
          active_slots: active,
          positions: active.map((s) => ({
            slot: s.slot_id,
            market: s.instrument || "",
            side: s.side || "",
            size: s.entry_size || 0,
            entry: s.entry_price || 0,
            roe: s.roe_pct || 0,
            phase: s.dsl_phase || 0,
          })),
        };
      } catch {
        // fall through
      }
    }
    return { status: "stopped", error: err.message };
  }
}

function readStrategies() {
  try {
    const output = execSync(
      `python3 -m cli.api.status_reader strategies`,
      { timeout: 10000, encoding: "utf-8", cwd: AGENT_CLI_DIR, stdio: ["pipe", "pipe", "pipe"] }
    );
    return JSON.parse(output.trim());
  } catch (err) {
    return { error: err.message };
  }
}

module.exports = { readStatus, readStrategies };
