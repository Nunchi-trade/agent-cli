"""yields — EVM yield aggregation, optimization, and routing for nunchi-cli.

Two-tier discovery: the DeFiLlama API (broad, read-only) plus curated on-chain
adapters (executable). A pure optimizer ranks and allocates; a router builds,
simulates, and executes deposit/withdraw routes. Exposed via the `nunchi yield`
CLI group and auto-discovered by the Hermes / OpenClaw harnesses.

The package is named `yields` (plural) deliberately — `yield` is a Python
keyword, so `import yield` would be a SyntaxError. Import submodules directly,
e.g. `from yields.models import YieldOpportunity`.
"""
