# Archived legacy strategies

Deprecated **2026-07-02** as part of the funding-rate hedge product focus.

The only supported trading strategy is **`cfi_hedge`** (`../cfi_hedge.py`, `../cfi_hedge_agent.py`).
Use `hl hedge propose|execute|status|auto|backtest` for the primary hedge surface.

These modules are kept for reference and historical tests. They are **not** registered in
`cli/strategy_registry.py` and are not loaded by `hl run` or MCP `run_strategy`.

## Archived modules

- simple_mm, avellaneda_mm, mean_reversion, hedge_agent, rfq_agent, aggressive_taker
- claude_agent, engine_mm, funding_arb, regime_mm, liquidation_mm
- momentum_breakout, grid_mm, basis_arb, simplified_ensemble
- funding_momentum, oi_divergence, trend_follower, risk_multipliers
