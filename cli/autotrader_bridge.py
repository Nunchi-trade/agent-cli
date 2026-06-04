"""Read-only bridge to an autoresearch strategy project.

Python port of ACC's ``server/src/autotrader-bridge.ts``. Parses results.tsv,
reads strategy.py, queries git log, and checks data readiness — without ever
mutating the project. Used by ``hl autoresearch status``.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class AutoresearchResult:
    commit: str
    score: float
    sharpe: float
    max_dd: float
    status: str        # "kept" | "discarded" | "baseline"
    description: str


@dataclass
class GitEntry:
    hash: str
    message: str
    date: str


@dataclass
class LabStatus:
    name: str
    path: str
    branch: str
    data_ready: bool
    best_score: Optional[float]
    best_commit: str
    total_experiments: int
    results: List[AutoresearchResult] = field(default_factory=list)
    strategy_preview: str = ""
    git_log: List[GitEntry] = field(default_factory=list)


def _autotrader_cache_dir() -> Path:
    return Path.home() / ".cache" / "autotrader" / "data"


def get_results(project_dir: Path) -> List[AutoresearchResult]:
    """Parse results.tsv (commit, score, sharpe, max_dd, status, description)."""
    tsv = project_dir / "results.tsv"
    if not tsv.exists():
        return []
    out: List[AutoresearchResult] = []
    lines = tsv.read_text().strip().splitlines()
    for line in lines[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split("\t")

        def _f(idx: int) -> float:
            try:
                return float(parts[idx])
            except (IndexError, ValueError):
                return 0.0

        out.append(
            AutoresearchResult(
                commit=parts[0] if len(parts) > 0 else "",
                score=_f(1),
                sharpe=_f(2),
                max_dd=_f(3),
                status=parts[4] if len(parts) > 4 else "discarded",
                description=parts[5] if len(parts) > 5 else "",
            )
        )
    return out


def get_strategy_preview(project_dir: Path, limit: int = 2000) -> str:
    p = project_dir / "strategy.py"
    if not p.exists():
        return ""
    try:
        return p.read_text()[:limit]
    except OSError:
        return ""


def get_branch(project_dir: Path) -> str:
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_dir, text=True, capture_output=True, timeout=3,
        ).stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def get_git_log(project_dir: Path, count: int = 10) -> List[GitEntry]:
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", "--format=%h\t%s\t%ci", f"-{count}"],
            cwd=project_dir, text=True, capture_output=True, timeout=5,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return []
    if not out:
        return []
    entries: List[GitEntry] = []
    for line in out.splitlines():
        parts = line.split("\t")
        entries.append(GitEntry(
            hash=parts[0] if len(parts) > 0 else "",
            message=parts[1] if len(parts) > 1 else "",
            date=parts[2] if len(parts) > 2 else "",
        ))
    return entries


def check_data_ready(project_dir: Path) -> bool:
    """Crypto majors are the minimum bar for a runnable backtest."""
    cache = _autotrader_cache_dir()
    return all(
        (cache / f"{sym}_1h.parquet").exists() for sym in ("BTC", "ETH", "SOL")
    )


def best_result(results: List[AutoresearchResult]) -> Optional[AutoresearchResult]:
    best: Optional[AutoresearchResult] = None
    for r in results:
        if best is None or r.score > best.score:
            best = r
    return best


def get_status(project_dir: Path) -> LabStatus:
    """Full read-only status for an autoresearch project directory."""
    project_dir = Path(project_dir).expanduser().resolve()
    results = get_results(project_dir)
    best = best_result(results)
    return LabStatus(
        name=project_dir.name,
        path=str(project_dir),
        branch=get_branch(project_dir),
        data_ready=check_data_ready(project_dir),
        best_score=best.score if best else None,
        best_commit=best.commit if best else "",
        total_experiments=len(results),
        results=results,
        strategy_preview=get_strategy_preview(project_dir),
        git_log=get_git_log(project_dir),
    )
