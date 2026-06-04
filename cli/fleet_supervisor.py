"""FleetSupervisor — spawn and manage many agent-cli trading subprocesses.

Python port of agent-command-center/server/src/fleet-supervisor.ts.

Distinct from a single trading run: FleetSupervisor wraps many trading agents
(`python -m cli.main run <strategy>`), each with isolated env (per-agent wallet,
builder address, market whitelist via a preset .env). It is GENERIC — it knows
nothing about cfi_hedge or the strategy-load runner. A member whose strategy is
the sentinel "__load__" is spawned via `strategy load <name>` instead of
`run <strategy>`; that subcommand lands in a sibling PR, so until then such a
member simply exits non-zero and is reported as "errored". No import-time or
run-time dependency on those siblings is introduced.

Lifecycle: spawn -> "active"; on process exit the monitor thread transitions to
"exited" (code 0) or "errored" (code != 0), unless the user killed it ("killed").
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from common.fleet_models import FleetAgentState, FleetMemberSpec

# Cap captured stdout/stderr per agent — matches MAX_LOG_LINES_PER_AGENT in TS.
MAX_LOG_LINES_PER_AGENT = 200

# Sentinel strategy name → spawn `strategy load <name>` instead of `run <name>`.
LOAD_SENTINEL = "__load__"


def _repo_root() -> Path:
    """agent-cli repo root (this file lives at <root>/cli/fleet_supervisor.py)."""
    return Path(__file__).resolve().parent.parent


def resolve_python() -> str:
    """Resolve the python interpreter for spawned members.

    Order matches resolvePython() in fleet-supervisor.ts:
      1. AGENT_CLI_PYTHON env override
      2. the repo's .venv/bin/python3 if it exists
      3. system python3
    """
    override = os.environ.get("AGENT_CLI_PYTHON")
    if override:
        return override
    venv_python = _repo_root() / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return "python3"


def parse_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, ignoring comments and blanks.

    Mirrors parseEnvFile() in fleet-supervisor.ts, including quote stripping.
    """
    out: Dict[str, str] = {}
    try:
        raw = path.read_text()
    except OSError:
        return out
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        eq = stripped.find("=")
        if eq < 0:
            continue
        key = stripped[:eq].strip()
        val = stripped[eq + 1:].strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def presets_dir() -> Path:
    """Directory holding preset .env files: <root>/configs/presets."""
    return _repo_root() / "configs" / "presets"


class FleetSupervisor:
    """In-process supervisor for a fleet of agent-cli trading subprocesses."""

    def __init__(self) -> None:
        self._agents: Dict[str, FleetAgentState] = {}
        self._procs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- spawn

    def spawn(self, spec: FleetMemberSpec) -> FleetAgentState:
        """Spawn a single trading agent subprocess."""
        agent_id = str(uuid.uuid4())
        env = self._build_env(spec)
        args = self._build_args(spec)

        state = FleetAgentState(
            id=agent_id,
            name=spec.name or self._fallback_name(spec),
            strategy=spec.strategy,
            market=spec.market,
            status="starting",
            started_at=_now_ms(),
        )
        with self._lock:
            self._agents[agent_id] = state

        # Soft-fail preset: surface a missing preset in the agent's error buffer.
        if spec.preset:
            preset_file = presets_dir() / f"{spec.preset}.env"
            if not preset_file.exists():
                state.error_logs.append(
                    f"[fleet] preset '{spec.preset}' not found at {preset_file}"
                )

        py = resolve_python()
        cmd = [py, "-m", "cli.main", *args]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(_repo_root()),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
            )
        except OSError as err:
            state.status = "errored"
            state.exited_at = _now_ms()
            state.error_logs.append(f"[fleet] spawn failed: {err}")
            return state

        state.status = "active"
        state.pid = proc.pid
        with self._lock:
            self._procs[agent_id] = proc

        self._read_stream(agent_id, proc.stdout, "stdout")
        self._read_stream(agent_id, proc.stderr, "stderr")
        self._watch_exit(agent_id, proc)
        return state

    def spawn_fleet(self, specs: List[FleetMemberSpec]) -> List[FleetAgentState]:
        """Spawn many agents at once (the typical fleet entry point)."""
        return [self.spawn(s) for s in specs]

    # ----------------------------------------------------------------- kill

    def kill(self, agent_id: str) -> bool:
        """Kill one agent (SIGTERM) and mark it 'killed'."""
        with self._lock:
            proc = self._procs.get(agent_id)
            state = self._agents.get(agent_id)
        if proc is None or state is None:
            return False
        # Mark killed BEFORE signalling so the exit watcher does not reclassify.
        state.status = "killed"
        state.exited_at = _now_ms()
        try:
            proc.send_signal(signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        with self._lock:
            self._procs.pop(agent_id, None)
        return True

    def kill_all(self) -> int:
        """Kill the entire fleet. Returns the number of agents signalled."""
        with self._lock:
            ids = list(self._procs.keys())
        return sum(1 for aid in ids if self.kill(aid))

    # --------------------------------------------------------------- reads

    def get(self, agent_id: str) -> Optional[FleetAgentState]:
        with self._lock:
            return self._agents.get(agent_id)

    def get_all(self) -> List[FleetAgentState]:
        """All agent states, sorted by start time (oldest first)."""
        with self._lock:
            states = list(self._agents.values())
        return sorted(states, key=lambda s: s.started_at)

    # ----------------------------------------------------------- internals

    @staticmethod
    def _fallback_name(spec: FleetMemberSpec) -> str:
        parts = [p for p in (spec.strategy, spec.market) if p]
        return "·".join(parts) if parts else spec.strategy

    def _build_env(self, spec: FleetMemberSpec) -> Dict[str, str]:
        """os.environ + preset .env (preset wins over inherited env)."""
        env: Dict[str, str] = dict(os.environ)
        if spec.preset:
            preset_file = presets_dir() / f"{spec.preset}.env"
            if preset_file.exists():
                env.update(parse_env_file(preset_file))
        return env

    def _build_args(self, spec: FleetMemberSpec) -> List[str]:
        """Build the cli.main argv tail (after `-m cli.main`).

        - normal:   run <strategy> [-i <market>] [extra_args...]
        - __load__: strategy load <market-or-name> [extra_args...]

        For a load member the program/name is taken from `market` (the strategy
        artifact name) so `extra_args` stays free-form; the `strategy load`
        subcommand itself is provided by a sibling PR.
        """
        if spec.strategy == LOAD_SENTINEL:
            load_name = spec.market or ""
            args = ["strategy", "load"]
            if load_name:
                args.append(load_name)
            args.extend(spec.extra_args)
            return args

        args = ["run", spec.strategy]
        if spec.market:
            args.extend(["-i", spec.market])
        args.extend(spec.extra_args)
        return args

    def _append_log(self, agent_id: str, line: str, kind: str) -> None:
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None:
                return
            sink = state.error_logs if kind == "stderr" else state.recent_logs
            sink.append(line)
            if len(sink) > MAX_LOG_LINES_PER_AGENT:
                del sink[: len(sink) - MAX_LOG_LINES_PER_AGENT]

    def _read_stream(self, agent_id: str, stream, kind: str) -> None:
        """Drain a child pipe on a daemon thread, appending trimmed lines."""
        if stream is None:
            return

        def _pump() -> None:
            try:
                for raw in iter(stream.readline, ""):
                    trimmed = raw.rstrip("\n").strip()
                    if trimmed:
                        self._append_log(agent_id, trimmed, kind)
            except (ValueError, OSError):
                pass  # pipe closed
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        threading.Thread(
            target=_pump, name=f"fleet-{kind}-{agent_id[:8]}", daemon=True
        ).start()

    def _watch_exit(self, agent_id: str, proc: subprocess.Popen) -> None:
        """Daemon thread: wait for exit, then transition status."""

        def _wait() -> None:
            code = proc.wait()
            with self._lock:
                state = self._agents.get(agent_id)
                self._procs.pop(agent_id, None)
            if state is None or state.status == "killed":
                return  # user-initiated
            state.status = "exited" if code == 0 else "errored"
            state.exited_at = _now_ms()
            state.exit_code = code

        threading.Thread(
            target=_wait, name=f"fleet-wait-{agent_id[:8]}", daemon=True
        ).start()


def _now_ms() -> int:
    return int(time.time() * 1000)
