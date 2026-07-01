#!/usr/bin/env python3
"""Validate that cost, route, and trade ledgers join on decision_call_id."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def validate(input_dir: Path) -> int:
    cost_rows = list(_read_jsonl(input_dir / "cost_ledger.jsonl"))
    route_rows = list(_read_jsonl(input_dir / "route_ledger.jsonl"))
    trade_rows = list(_read_jsonl(input_dir / "trades.jsonl"))

    cost_by_decision: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in cost_rows:
        decision_call_id = row.get("decision_call_id")
        if decision_call_id:
            cost_by_decision[str(decision_call_id)] += _decimal(row.get("usd_cost"))

    linked_trades: List[dict] = []
    orphan_trades: List[dict] = []
    for row in trade_rows:
        decision_call_id = row.get("decision_call_id")
        if decision_call_id and str(decision_call_id) in cost_by_decision:
            linked_trades.append(row)
        else:
            orphan_trades.append(row)

    orphan_costs = [
        row for row in cost_rows
        if row.get("decision_call_id") and str(row.get("decision_call_id")) not in {
            str(t.get("decision_call_id")) for t in trade_rows if t.get("decision_call_id")
        }
    ]

    print(f"input_dir={input_dir}")
    print(f"cost_rows={len(cost_rows)} route_rows={len(route_rows)} trade_rows={len(trade_rows)}")
    print(f"linked_trades={len(linked_trades)} orphan_trades={len(orphan_trades)} orphan_cost_rows={len(orphan_costs)}")

    if linked_trades:
        total_cost = sum(cost_by_decision[str(row.get('decision_call_id'))] for row in linked_trades)
        avg_cost = total_cost / Decimal(len(linked_trades))
        print(f"avg_llm_cost_per_linked_trade={avg_cost}")

    cache_rows = [row for row in cost_rows if row.get("cached_tokens") or row.get("cache_read_input_tokens")]
    if cache_rows:
        cached = sum(int(row.get("cached_tokens") or 0) for row in cache_rows)
        inputs = sum(int(row.get("input_tokens") or 0) for row in cache_rows)
        print(f"cache_measured_rows={len(cache_rows)} aggregate_hit_rate={(cached/inputs if inputs else 0):.2%}")

    ok = bool(cost_rows) and (bool(linked_trades) or not trade_rows)
    if trade_rows and not linked_trades:
        print("FAIL: trades exist but none join to cost rows via decision_call_id")
        return 1
    if not cost_rows:
        print("FAIL: no cost rows found")
        return 1
    print("PASS")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate combined pricing ledgers")
    parser.add_argument("--input-dir", required=True)
    args = parser.parse_args()
    return validate(Path(args.input_dir))


if __name__ == "__main__":
    raise SystemExit(main())
