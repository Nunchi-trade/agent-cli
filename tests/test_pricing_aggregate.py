from __future__ import annotations

import argparse
import json

from scripts import pricing_aggregate


def test_pricing_aggregate_outputs_mode_specific_summary(tmp_path):
    (tmp_path / "cost_ledger.jsonl").write_text(
        json.dumps({
            "job_type": "heartbeat",
            "agent_id": "agent-1",
            "user_id": "user-1",
            "account_id": "acct-1",
            "subscription_id": "sub-1",
            "usd_cost": "0.01",
            "input_tokens": 100,
            "cached_tokens": 80,
            "ts": 1_000,
        }) + "\n"
    )
    (tmp_path / "agent_runtime_ledger.jsonl").write_text(
        json.dumps({"job_type": "heartbeat", "agent_id": "agent-1", "event_type": "heartbeat", "ts": 1_000}) + "\n"
        + json.dumps({"job_type": "heartbeat", "agent_id": "agent-1", "event_type": "heartbeat", "ts": 3_601_000}) + "\n"
    )
    output = tmp_path / "report.md"
    args = argparse.Namespace(
        input_dir=str(tmp_path),
        output=str(output),
        infra_usd_per_agent_hour=0.01,
        railway_vcpu_per_agent=1.0,
        railway_ram_gb_per_agent=1.0,
        railway_volume_gb_per_agent=0.0,
        railway_egress_gb_per_agent_month=0.0,
        observability_usd_per_agent_hour=0.0,
        hosted_mcp_seats=5,
        target_margin="80",
    )

    assert pricing_aggregate.aggregate(args) == 0

    report = output.read_text()
    assert "## Mode-Specific Pricing Inputs" in report
    assert "`mode_1_hosted_mcp_tools`" in report
    assert "`C_seat`" in report
    assert "`mode_2_hosted_mcp_tools_inference`" in report
    assert "`mode_3_clone_local`" in report
    assert "gpt-4.1-mini" in report
