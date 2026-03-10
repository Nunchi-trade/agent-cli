"""REFLECT reporter — generates markdown reports from ReflectMetrics.

Two output modes:
  - generate(): Full detailed markdown report
  - distill():  3-5 line summary for memory/logging
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from modules.reflect_engine import ReflectMetrics


def _ms_to_human(ms: float) -> str:
    """Convert milliseconds to human-readable duration."""
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.0f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.0f}m"
    hours = mins / 60
    if hours < 24:
        return f"{hours:.1f}h"
    days = hours / 24
    return f"{days:.1f}d"


def _pf_str(pf: float) -> str:
    if pf == float('inf'):
        return "∞"
    return f"{pf:.2f}"


class ReflectReporter:
    """Generate markdown reports from REFLECT metrics."""

    def generate(self, metrics: ReflectMetrics, date: Optional[str] = None) -> str:
        """Full markdown report."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        m = metrics
        lines = []

        lines.append(f"# REFLECT Report — {date}")
        lines.append("")

        # ── Core Stats ──
        lines.append("## Core Stats")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Trades | {m.total_trades} |")
        lines.append(f"| Round Trips | {m.total_round_trips} |")
        lines.append(f"| Win Rate | {m.win_rate:.1f}% ({m.win_count}W / {m.loss_count}L) |")
        lines.append(f"| Gross PnL | ${m.gross_pnl:+.2f} |")
        lines.append(f"| Fees | ${m.total_fees:.2f} |")
        lines.append(f"| **Net PnL** | **${m.net_pnl:+.2f}** |")
        lines.append(f"| Gross PF | {_pf_str(m.gross_profit_factor)} |")
        lines.append(f"| Net PF | {_pf_str(m.net_profit_factor)} |")
        lines.append("")

        # ── Fee Analysis ──
        lines.append("## Fee Analysis")
        lines.append("")
        fdr_flag = ""
        if m.fdr > 30:
            fdr_flag = " **CRITICAL**"
        elif m.fdr > 20:
            fdr_flag = " **WARNING**"
        lines.append(f"- FDR (Fee Drag Ratio): {m.fdr:.1f}%{fdr_flag}")
        lines.append(f"- Total fees: ${m.total_fees:.2f}")
        if m.total_round_trips > 0:
            lines.append(f"- Avg fee per round trip: ${m.total_fees / m.total_round_trips:.2f}")
        if m.total_fees > abs(m.gross_pnl) and m.total_round_trips >= 3:
            lines.append(f"- **Fees exceed gross PnL — net negative on every trade**")
        lines.append("")

        # ── Direction ──
        lines.append("## Direction Analysis")
        lines.append("")
        lines.append(f"| Direction | Count | Win Rate | Net PnL |")
        lines.append(f"|-----------|-------|----------|---------|")
        long_wr = (m.long_wins / m.long_count * 100) if m.long_count else 0
        short_wr = (m.short_wins / m.short_count * 100) if m.short_count else 0
        lines.append(f"| Long | {m.long_count} | {long_wr:.0f}% | ${m.long_pnl:+.2f} |")
        lines.append(f"| Short | {m.short_count} | {short_wr:.0f}% | ${m.short_pnl:+.2f} |")
        lines.append("")

        # ── Holding Periods ──
        if m.holding_buckets:
            lines.append("## Holding Periods")
            lines.append("")
            lines.append(f"- Average: {_ms_to_human(m.avg_holding_ms)}")
            for bucket in ["<5m", "5-15m", "15-60m", "1-4h", "4h+"]:
                count = m.holding_buckets.get(bucket, 0)
                if count > 0:
                    lines.append(f"- {bucket}: {count} trades")
            lines.append("")

        # ── Streaks ──
        lines.append("## Streaks")
        lines.append("")
        lines.append(f"- Max consecutive wins: {m.max_consecutive_wins}")
        lines.append(f"- Max consecutive losses: {m.max_consecutive_losses}")
        lines.append("")

        # ── Monster Dependency ──
        lines.append("## Monster Trade Dependency")
        lines.append("")
        lines.append(f"- Best trade: ${m.best_trade_pnl:+.2f}")
        lines.append(f"- Worst trade: ${m.worst_trade_pnl:+.2f}")
        dep_flag = " **HIGH**" if m.monster_dependency_pct > 60 else ""
        lines.append(f"- Dependency: {m.monster_dependency_pct:.0f}%{dep_flag}")
        lines.append("")

        # ── Strategy Breakdown ──
        if m.strategy_stats:
            lines.append("## Strategy Breakdown")
            lines.append("")
            lines.append(f"| Strategy | Trades | Win Rate | Net PnL | Fees |")
            lines.append(f"|----------|--------|----------|---------|------|")
            for name, s in sorted(m.strategy_stats.items()):
                lines.append(
                    f"| {name} | {s['count']} | {s['win_rate']:.0f}% "
                    f"| ${s['net_pnl']:+.2f} | ${s['total_fees']:.2f} |"
                )
            lines.append("")

        # ── Exit Types ──
        if m.exit_type_counts:
            lines.append("## Exit Types")
            lines.append("")
            for exit_type, count in sorted(m.exit_type_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- {exit_type}: {count}")
            lines.append("")

        # ── Recommendations ──
        lines.append("## Recommendations")
        lines.append("")
        if m.recommendations:
            for rec in m.recommendations:
                lines.append(f"- {rec}")
        else:
            lines.append("- No data to generate recommendations.")
        lines.append("")

        return "\n".join(lines)

    def distill(self, metrics: ReflectMetrics) -> str:
        """3-5 line summary for memory/logging."""
        m = metrics
        lines = []
        lines.append(
            f"REFLECT: {m.total_round_trips} round trips, "
            f"{m.win_rate:.0f}% WR, "
            f"net ${m.net_pnl:+.2f}"
        )
        lines.append(
            f"  Fees: ${m.total_fees:.2f} (FDR {m.fdr:.0f}%) | "
            f"PF: {_pf_str(m.net_profit_factor)}"
        )
        if m.long_count or m.short_count:
            lines.append(
                f"  Long: {m.long_count} (${m.long_pnl:+.2f}) | "
                f"Short: {m.short_count} (${m.short_pnl:+.2f})"
            )
        if m.recommendations:
            lines.append(f"  Top issue: {m.recommendations[0][:80]}")
        return "\n".join(lines)
