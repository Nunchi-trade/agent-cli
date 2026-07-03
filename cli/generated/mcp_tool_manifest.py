"""GENERATED FROM packages/nunchi-mcp-manifest/tools.json — DO NOT EDIT.

Run: python packages/nunchi-mcp-manifest/generate.py
"""

from __future__ import annotations

import json

MANIFEST_VERSION = 1

TOOL_BUCKETS: dict[str, list[str]] = {
    "free": [
        "account",
        "agent_memory",
        "apex_status",
        "builder_status",
        "funding_hedge_backtest",
        "funding_hedge_info",
        "funding_hedge_propose",
        "funding_rates",
        "judge_report",
        "money_bridge_status",
        "obsidian_context",
        "order_status",
        "pair_status",
        "setup_check",
        "status",
        "strategies",
        "trade_journal",
        "wallet_list"
    ],
    "paidCompute": [
        "apex_run",
        "hedge_agent_smoke_test",
        "openrouter_chat",
        "radar_run",
        "reflect_run",
        "run_strategy"
    ],
    "safetyGated": [
        "approve_agent",
        "emergency_close_all",
        "funding_hedge_execute",
        "money_deposit",
        "money_transfer_usd",
        "money_withdraw",
        "schedule_cancel",
        "trade",
        "wallet_auto"
    ]
}

FREE_QUOTA_EXECUTION_TOOLS: tuple[str, ...] = ('trade', 'funding_hedge_execute')

READ_ONLY_TOOLS = frozenset(['account', 'agent_memory', 'apex_status', 'builder_status', 'funding_hedge_backtest', 'funding_hedge_info', 'funding_hedge_propose', 'funding_rates', 'judge_report', 'money_bridge_status', 'obsidian_context', 'order_status', 'pair_status', 'setup_check', 'status', 'strategies', 'trade_journal', 'wallet_list'])

DESTRUCTIVE_TOOLS = frozenset(['apex_run', 'approve_agent', 'emergency_close_all', 'funding_hedge_execute', 'money_deposit', 'money_transfer_usd', 'money_withdraw', 'run_strategy', 'schedule_cancel', 'trade', 'wallet_auto'])

RUNNER_TOOLS: tuple[str, ...] = ('account', 'agent_memory', 'apex_run', 'apex_status', 'approve_agent', 'builder_status', 'emergency_close_all', 'funding_hedge_backtest', 'funding_hedge_execute', 'funding_hedge_info', 'funding_hedge_propose', 'funding_rates', 'hedge_agent_smoke_test', 'judge_report', 'money_bridge_status', 'money_deposit', 'money_transfer_usd', 'money_withdraw', 'obsidian_context', 'order_status', 'pair_status', 'radar_run', 'reflect_run', 'run_strategy', 'schedule_cancel', 'setup_check', 'status', 'strategies', 'trade', 'trade_journal', 'wallet_auto', 'wallet_list')

TOOL_METADATA: dict[str, dict] = json.loads('{\n    "strategies": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "builder_status": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "wallet_list": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "setup_check": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "account": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "status": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "apex_status": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "agent_memory": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "trade_journal": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "judge_report": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "obsidian_context": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "order_status": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": false\n    },\n    "funding_rates": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": false\n    },\n    "funding_hedge_info": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": false\n    },\n    "funding_hedge_propose": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "funding_hedge_backtest": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "pair_status": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": false\n    },\n    "money_bridge_status": {\n        "bucket": "free",\n        "readOnly": true,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": false\n    },\n    "radar_run": {\n        "bucket": "paidCompute",\n        "readOnly": false,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "reflect_run": {\n        "bucket": "paidCompute",\n        "readOnly": false,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": true\n    },\n    "run_strategy": {\n        "bucket": "paidCompute",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": true\n    },\n    "apex_run": {\n        "bucket": "paidCompute",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": true\n    },\n    "hedge_agent_smoke_test": {\n        "bucket": "paidCompute",\n        "readOnly": false,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": true,\n        "gateway": false\n    },\n    "openrouter_chat": {\n        "bucket": "paidCompute",\n        "readOnly": false,\n        "destructive": false,\n        "requireConfirmation": false,\n        "runner": false,\n        "gateway": true,\n        "requiresInferencePlan": true\n    },\n    "trade": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": true\n    },\n    "funding_hedge_execute": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": true\n    },\n    "wallet_auto": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": true\n    },\n    "schedule_cancel": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": false\n    },\n    "emergency_close_all": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": false\n    },\n    "money_withdraw": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": false\n    },\n    "money_transfer_usd": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": false\n    },\n    "money_deposit": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": false\n    },\n    "approve_agent": {\n        "bucket": "safetyGated",\n        "readOnly": false,\n        "destructive": true,\n        "requireConfirmation": true,\n        "runner": true,\n        "gateway": false\n    }\n}')
