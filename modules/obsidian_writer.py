"""Obsidian writer — syncs REFLECT reports and journal entries to Obsidian vault.

Writes trading reports as Obsidian-compatible markdown notes with YAML
frontmatter for Dataview queries. Appends daily summaries to daily notes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class ObsidianWriter:
    """Writes WOLF output as Obsidian notes with Dataview-friendly frontmatter."""

    def __init__(self, vault_path: str = "~/obsidian-vault"):
        self.vault_path = Path(vault_path).expanduser()
        self._project_dir = self.vault_path / "projects" / "agent-cli"

    @property
    def available(self) -> bool:
        return self.vault_path.exists()

    def write_reflect_report(
        self,
        briefing_md: str,
        date: str,
        win_rate: float = 0.0,
        net_pnl: float = 0.0,
        fdr: float = 0.0,
        round_trips: int = 0,
    ) -> Optional[Path]:
        """Save REFLECT report as Obsidian note with frontmatter."""
        if not self.available:
            return None

        reflect_dir = self._project_dir / "reflect"
        reflect_dir.mkdir(parents=True, exist_ok=True)

        frontmatter = self._frontmatter(
            tags=["reflect", "wolf", "trading-review"],
            extra={
                "date": date,
                "win_rate": round(win_rate, 1),
                "net_pnl": round(net_pnl, 2),
                "fdr": round(fdr, 1),
                "round_trips": round_trips,
            },
        )

        path = reflect_dir / f"{date}.md"
        path.write_text(frontmatter + "\n" + briefing_md)
        return path

    def write_judge_report(
        self,
        report_dict: Dict[str, Any],
        date: str,
    ) -> Optional[Path]:
        """Save Judge report as Obsidian note."""
        if not self.available:
            return None

        judge_dir = self._project_dir / "judge"
        judge_dir.mkdir(parents=True, exist_ok=True)

        fp_rates = report_dict.get("false_positive_rates", {})
        findings = report_dict.get("findings", [])
        recs = report_dict.get("config_recommendations", [])

        frontmatter = self._frontmatter(
            tags=["judge", "wolf", "signal-quality"],
            extra={
                "date": date,
                "round_trips_evaluated": report_dict.get("round_trips_evaluated", 0),
                "findings_count": len(findings),
            },
        )

        lines = [f"# Judge Report — {date}", ""]

        if fp_rates:
            lines.extend(["## False Positive Rates", ""])
            for source, rate in fp_rates.items():
                lines.append(f"- **{source}**: {rate:.1f}%")
            lines.append("")

        if findings:
            lines.extend(["## Findings", ""])
            for f in findings:
                detail = f.get("detail", "") if isinstance(f, dict) else str(f)
                lines.append(f"- {detail}")
            lines.append("")

        if recs:
            lines.extend(["## Recommendations", ""])
            for r in recs:
                summary = r.get("summary", "") if isinstance(r, dict) else str(r)
                lines.append(f"- {summary}")

        path = judge_dir / f"{date}.md"
        path.write_text(frontmatter + "\n" + "\n".join(lines))
        return path

    def write_notable_trade(
        self,
        journal_entry_dict: Dict[str, Any],
    ) -> Optional[Path]:
        """Save notable trade as Obsidian note."""
        if not self.available:
            return None

        trades_dir = self._project_dir / "trades"
        trades_dir.mkdir(parents=True, exist_ok=True)

        entry_id = journal_entry_dict.get("entry_id", "unknown")
        instrument = journal_entry_dict.get("instrument", "")
        pnl = journal_entry_dict.get("pnl", 0)
        roe = journal_entry_dict.get("roe_pct", 0)

        frontmatter = self._frontmatter(
            tags=["trade", "wolf", instrument.lower()],
            extra={
                "instrument": instrument,
                "direction": journal_entry_dict.get("direction", ""),
                "pnl": round(pnl, 4),
                "roe_pct": round(roe, 2),
                "entry_source": journal_entry_dict.get("entry_source", ""),
                "signal_quality": journal_entry_dict.get("signal_quality", ""),
            },
        )

        lines = [
            f"# {instrument} — ${pnl:+.2f} ({roe:+.1f}%)",
            "",
            f"**Entry**: {journal_entry_dict.get('entry_reasoning', '')}",
            f"**Exit**: {journal_entry_dict.get('exit_reasoning', '')}",
            f"**Quality**: {journal_entry_dict.get('signal_quality', '')}",
            "",
            f"## Retrospective",
            "",
            journal_entry_dict.get("retrospective", ""),
        ]

        path = trades_dir / f"{entry_id}.md"
        path.write_text(frontmatter + "\n" + "\n".join(lines))
        return path

    def append_to_daily(self, date: str, summary: str) -> Optional[Path]:
        """Append WOLF summary to the daily note."""
        if not self.available:
            return None

        daily_dir = self.vault_path / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)

        path = daily_dir / f"{date}.md"

        wolf_section = f"\n\n## WOLF\n\n{summary}\n"

        if path.exists():
            content = path.read_text()
            if "## WOLF" in content:
                # Replace existing WOLF section
                import re
                content = re.sub(
                    r"## WOLF\n.*?(?=\n## |\Z)",
                    f"## WOLF\n\n{summary}\n",
                    content,
                    flags=re.DOTALL,
                )
                path.write_text(content)
            else:
                # Append new section
                with open(path, "a") as f:
                    f.write(wolf_section)
        else:
            path.write_text(f"# {date}\n{wolf_section}")

        return path

    @staticmethod
    def _frontmatter(tags: List[str], extra: Dict[str, Any] = None) -> str:
        """Generate YAML frontmatter string."""
        lines = ["---"]
        lines.append(f"tags: [{', '.join(tags)}]")
        if extra:
            for k, v in extra.items():
                if isinstance(v, str):
                    lines.append(f'{k}: "{v}"')
                elif isinstance(v, bool):
                    lines.append(f"{k}: {'true' if v else 'false'}")
                else:
                    lines.append(f"{k}: {v}")
        lines.append("---")
        return "\n".join(lines)
