# Paradex Venue Integration Implementation Plan

> For Hermes: Use subagent-driven-development skill to implement this plan task-by-task.

Goal: Add Paradex testnet/mainnet trading support to Nunchi without breaking the existing Hyperliquid flow.

Architecture: Keep strategies and the order manager venue-agnostic, and add a new Paradex adapter backed by the official paradex_py SDK. Refactor the CLI/config/bootstrap layer so the runtime chooses a venue-specific adapter factory instead of instantiating Hyperliquid directly.

Tech Stack: Python 3.10+, Typer CLI, pytest, paradex_py SDK, existing VenueAdapter abstraction, existing Nunchi strategy engine.

---

## Constraints and ground truth

- Existing generic adapter interface lives in `common/venue_adapter.py`.
- Existing live execution is still Hyperliquid-first in:
  - `cli/config.py`
  - `cli/commands/run.py`
  - `cli/commands/trade.py`
  - `cli/commands/account.py`
  - `cli/commands/apex.py`
  - `cli/main.py`
  - `pyproject.toml`
- Existing engine still imports `APICircuitBreakerOpen` directly from `cli.hl_adapter` in `cli/engine.py`.
- Paradex private API uses JWT bearer auth.
- Paradex private order submission also requires signing with the Paradex L2 key.
- Preferred operational model for bots: Paradex subkey, not the main withdrawal-capable key.
- Official Python SDK package: `paradex_py`.

## Credential model to implement

Use this explicit environment model for Paradex:

- `NUNCHI_VENUE=hl|paradex`
- `PARADEX_ENV=testnet|prod`
- `PARADEX_ACCOUNT_ADDRESS=0x...`  # parent/main Paradex account L2 address
- `PARADEX_PRIVATE_KEY=0x...`      # Paradex subkey L2 private key
- optional later:
  - `PARADEX_TOKEN_USAGE=interactive`
  - `PARADEX_WS_ENABLED=true`

Do not reuse `HL_PRIVATE_KEY` for Paradex.

## Acceptance criteria

1. `hl run ... --venue paradex` or equivalent config-based venue selection boots successfully.
2. Paradex adapter can:
   - fetch markets
   - fetch mids or BBO/snapshot
   - fetch account state
   - place one limit order
   - cancel it
3. Existing Hyperliquid behavior remains unchanged when venue is `hl`.
4. Unit tests cover adapter contract, credential/config selection, and venue bootstrap.
5. Smoke test instructions exist for Paradex testnet using a subkey.

---

### Task 1: Add the Paradex dependency to packaging

Objective: Make the repo installable with the official Paradex SDK.

Files:
- Modify: `pyproject.toml`
- Test: install/import verification via shell

Step 1: Add the dependency.

Add `paradex_py` to `[project].dependencies`.

Suggested edit:

```toml
[project]
dependencies = [
    "typer>=0.9.0",
    "pydantic>=2.0.0",
    "pyyaml>=6.0",
    "hyperliquid-python-sdk>=0.4.0",
    "eth-account>=0.10.0",
    "paradex-py>=0.5.5",
]
```

Step 2: Verify the package name if installation fails.

Run:

```bash
python3 -m pip install -e .
python3 - <<'PY'
import paradex_py
print('ok', paradex_py.__file__)
PY
```

Expected: editable install succeeds and the import prints a file path.

Step 3: Commit.

```bash
git add pyproject.toml
git commit -m "build: add paradex python sdk dependency"
```

---

### Task 2: Introduce explicit venue selection in config

Objective: Stop hardcoding Hyperliquid as the only venue.

Files:
- Modify: `cli/config.py`
- Test: `tests/test_config_venue.py` (new)

Step 1: Write the failing test.

Create `tests/test_config_venue.py`:

```python
from cli.config import TradingConfig


def test_default_venue_is_hl():
    cfg = TradingConfig()
    assert cfg.venue == "hl"


def test_get_private_key_passes_selected_venue(monkeypatch):
    captured = {}

    def fake_resolve_private_key(venue="hl", address=None):
        captured["venue"] = venue
        return "secret"

    monkeypatch.setattr("common.credentials.resolve_private_key", fake_resolve_private_key)
    cfg = TradingConfig(venue="paradex")
    assert cfg.get_private_key() == "secret"
    assert captured["venue"] == "paradex"
```

Step 2: Run the test to verify failure.

Run:

```bash
pytest tests/test_config_venue.py -v
```

Expected: FAIL because `TradingConfig` has no `venue` field and/or `get_private_key()` always uses `hl`.

Step 3: Implement the minimal config change.

Update `cli/config.py`:

```python
@dataclass
class TradingConfig:
    venue: str = "hl"
    ...

    def get_private_key(self) -> str:
        from common.credentials import resolve_private_key
        return resolve_private_key(venue=self.venue)
```

Also make sure `from_yaml()` keeps the new field by leaving it in dataclass fields.

Step 4: Run the test to verify pass.

Run:

```bash
pytest tests/test_config_venue.py -v
```

Expected: PASS.

Step 5: Commit.

```bash
git add cli/config.py tests/test_config_venue.py
git commit -m "feat: add venue selection to trading config"
```

---

### Task 3: Add a venue-neutral runtime exception for API safe mode

Objective: Remove the direct engine dependency on `cli.hl_adapter.APICircuitBreakerOpen`.

Files:
- Create: `common/exceptions.py`
- Modify: `cli/engine.py`
- Modify: `cli/hl_adapter.py`
- Test: `tests/test_engine_exceptions.py` (new)

Step 1: Write the failing test.

Create `tests/test_engine_exceptions.py`:

```python
from common.exceptions import VenueCircuitBreakerOpen


def test_exception_is_importable():
    err = VenueCircuitBreakerOpen("boom")
    assert str(err) == "boom"
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_engine_exceptions.py -v
```

Expected: FAIL because the module does not exist.

Step 3: Implement the shared exception.

Create `common/exceptions.py`:

```python
class VenueCircuitBreakerOpen(Exception):
    """Raised when a venue adapter decides trading must halt due to repeated API failures."""
```

Then update:

- `cli/engine.py`
  - replace `from cli.hl_adapter import APICircuitBreakerOpen`
  - with `from common.exceptions import VenueCircuitBreakerOpen`
  - catch `VenueCircuitBreakerOpen`
- `cli/hl_adapter.py`
  - either alias or replace `APICircuitBreakerOpen` with subclass/alias:

```python
from common.exceptions import VenueCircuitBreakerOpen

class APICircuitBreakerOpen(VenueCircuitBreakerOpen):
    pass
```

Step 4: Run targeted tests.

Run:

```bash
pytest tests/test_engine_exceptions.py tests/test_hl_adapter.py -q
```

Expected: PASS.

Step 5: Commit.

```bash
git add common/exceptions.py cli/engine.py cli/hl_adapter.py tests/test_engine_exceptions.py
git commit -m "refactor: share venue circuit breaker exception"
```

---

### Task 4: Add a venue bootstrap/factory module

Objective: Centralize live/mock venue construction so commands stop instantiating Hyperliquid directly.

Files:
- Create: `cli/venue_factory.py`
- Test: `tests/test_venue_factory.py` (new)

Step 1: Write the failing test.

Create `tests/test_venue_factory.py`:

```python
from cli.venue_factory import normalize_venue


def test_normalize_venue_defaults_to_hl():
    assert normalize_venue(None) == "hl"


def test_normalize_venue_accepts_paradex():
    assert normalize_venue("paradex") == "paradex"
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_venue_factory.py -v
```

Expected: FAIL because the module does not exist.

Step 3: Implement the factory shell.

Create `cli/venue_factory.py` with these functions:

```python
from __future__ import annotations

import os
from typing import Optional


def normalize_venue(value: Optional[str]) -> str:
    venue = (value or os.getenv("NUNCHI_VENUE") or "hl").strip().lower()
    if venue not in {"hl", "paradex"}:
        raise ValueError(f"Unsupported venue: {venue}")
    return venue
```

Also add placeholders you will fill in later:

```python
def build_live_venue_adapter(*, venue: str, mainnet: bool, private_key: str, account_address: Optional[str] = None):
    ...


def build_mock_venue_adapter():
    from adapters.mock_adapter import MockVenueAdapter
    return MockVenueAdapter()
```

Step 4: Run the test.

Run:

```bash
pytest tests/test_venue_factory.py -v
```

Expected: PASS.

Step 5: Commit.

```bash
git add cli/venue_factory.py tests/test_venue_factory.py
git commit -m "feat: add venue bootstrap factory"
```

---

### Task 5: Add Paradex config helpers

Objective: Formalize the env/config inputs Paradex needs beyond a single private key.

Files:
- Create: `common/paradex_config.py`
- Test: `tests/test_paradex_config.py` (new)

Step 1: Write the failing test.

Create `tests/test_paradex_config.py`:

```python
import pytest
from common.paradex_config import ParadexConfig


def test_paradex_config_reads_env(monkeypatch):
    monkeypatch.setenv("PARADEX_ACCOUNT_ADDRESS", "0xabc")
    monkeypatch.setenv("PARADEX_PRIVATE_KEY", "0xdef")
    cfg = ParadexConfig.from_env(mainnet=False)
    assert cfg.account_address == "0xabc"
    assert cfg.private_key == "0xdef"
    assert cfg.env == "testnet"


def test_paradex_config_requires_account_address(monkeypatch):
    monkeypatch.delenv("PARADEX_ACCOUNT_ADDRESS", raising=False)
    monkeypatch.setenv("PARADEX_PRIVATE_KEY", "0xdef")
    with pytest.raises(RuntimeError):
        ParadexConfig.from_env(mainnet=False)
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_paradex_config.py -v
```

Expected: FAIL.

Step 3: Implement the config object.

Create `common/paradex_config.py`:

```python
from dataclasses import dataclass
import os


@dataclass
class ParadexConfig:
    env: str
    account_address: str
    private_key: str
    token_usage: str | None = None

    @classmethod
    def from_env(cls, mainnet: bool) -> "ParadexConfig":
        env = "prod" if mainnet else "testnet"
        account_address = os.getenv("PARADEX_ACCOUNT_ADDRESS", "").strip()
        private_key = os.getenv("PARADEX_PRIVATE_KEY", "").strip()
        token_usage = os.getenv("PARADEX_TOKEN_USAGE", "").strip() or None
        if not account_address:
            raise RuntimeError("Missing PARADEX_ACCOUNT_ADDRESS")
        if not private_key:
            raise RuntimeError("Missing PARADEX_PRIVATE_KEY")
        return cls(env=env, account_address=account_address, private_key=private_key, token_usage=token_usage)
```

Step 4: Run the test.

Run:

```bash
pytest tests/test_paradex_config.py -v
```

Expected: PASS.

Step 5: Commit.

```bash
git add common/paradex_config.py tests/test_paradex_config.py
git commit -m "feat: add paradex environment config loader"
```

---

### Task 6: Build a minimal Paradex SDK wrapper

Objective: Hide SDK-specific auth and transport details behind a small internal client.

Files:
- Create: `parent/paradex_proxy.py`
- Test: `tests/test_paradex_proxy.py` (new)

Step 1: Write the failing test.

Create `tests/test_paradex_proxy.py`:

```python
from parent.paradex_proxy import ParadexProxy


def test_proxy_requires_client_methods(monkeypatch):
    proxy = ParadexProxy.__new__(ParadexProxy)
    assert hasattr(proxy, '__class__')
```
```

Then add a more useful mock-driven test:

```python
from unittest.mock import MagicMock
from parent.paradex_proxy import ParadexProxy


def test_fetch_markets_delegates_to_sdk():
    proxy = ParadexProxy.__new__(ParadexProxy)
    proxy.api_client = MagicMock()
    proxy.api_client.fetch_markets.return_value = {"results": [{"symbol": "ETH-USD-PERP"}]}
    assert proxy.fetch_markets()["results"][0]["symbol"] == "ETH-USD-PERP"
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_paradex_proxy.py -v
```

Expected: FAIL.

Step 3: Implement the wrapper.

Create `parent/paradex_proxy.py` with a narrow interface:

```python
from __future__ import annotations

from paradex_py import ParadexSubkey
from paradex_py.environment import PROD, TESTNET


class ParadexProxy:
    def __init__(self, *, account_address: str, private_key: str, mainnet: bool = False, token_usage: str | None = None):
        env = PROD if mainnet else TESTNET
        auth_params = {"token_usage": token_usage} if token_usage else None
        self.client = ParadexSubkey(
            env=env,
            l2_private_key=private_key,
            l2_address=account_address,
        )
        if auth_params:
            self.client.api_client.auth_params = auth_params
        self.api_client = self.client.api_client
        self.ws_client = self.client.ws_client

    def fetch_markets(self):
        return self.api_client.fetch_markets()

    def fetch_orders(self, params=None):
        return self.api_client.fetch_orders(params=params)

    def fetch_positions(self):
        return self.api_client.fetch_positions()

    def fetch_balances(self):
        return self.api_client.fetch_balances()

    def submit_order(self, order):
        return self.api_client.submit_order(order=order)

    def cancel_order(self, order_id: str):
        return self.api_client.cancel_order(order_id)

    def cancel_all_orders(self, params=None):
        return self.api_client.cancel_all_orders(params)
```

Keep it narrow. Do not add withdrawals or transfer support.

Step 4: Run the test.

Run:

```bash
pytest tests/test_paradex_proxy.py -v
```

Expected: PASS.

Step 5: Commit.

```bash
git add parent/paradex_proxy.py tests/test_paradex_proxy.py
git commit -m "feat: add paradex sdk proxy wrapper"
```

---

### Task 7: Implement a symbol translator for Paradex

Objective: Decouple Nunchi strategy symbols from Paradex market symbols.

Files:
- Create: `common/paradex_symbols.py`
- Test: `tests/test_paradex_symbols.py` (new)

Step 1: Write the failing test.

Create `tests/test_paradex_symbols.py`:

```python
import pytest
from common.paradex_symbols import instrument_to_paradex_market, paradex_market_to_instrument


def test_eth_perp_maps_to_paradex():
    assert instrument_to_paradex_market("ETH-PERP") == "ETH-USD-PERP"


def test_btc_perp_maps_back():
    assert paradex_market_to_instrument("BTC-USD-PERP") == "BTC-PERP"


def test_unknown_symbol_raises():
    with pytest.raises(ValueError):
        instrument_to_paradex_market("VXX-USDYP")
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_paradex_symbols.py -v
```

Expected: FAIL.

Step 3: Implement minimal translator.

Create `common/paradex_symbols.py`:

```python
def instrument_to_paradex_market(instrument: str) -> str:
    instrument = instrument.upper()
    if instrument.endswith("-PERP"):
        base = instrument[:-5]
        return f"{base}-USD-PERP"
    raise ValueError(f"Unsupported Paradex instrument: {instrument}")


def paradex_market_to_instrument(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol.endswith("-USD-PERP"):
        base = symbol[:-9]
        return f"{base}-PERP"
    return symbol
```

Step 4: Run the test.

Run:

```bash
pytest tests/test_paradex_symbols.py -v
```

Expected: PASS.

Step 5: Commit.

```bash
git add common/paradex_symbols.py tests/test_paradex_symbols.py
git commit -m "feat: add paradex symbol mapping helpers"
```

---

### Task 8: Implement a first-pass Paradex adapter

Objective: Create a `VenueAdapter` implementation backed by Paradex.

Files:
- Create: `adapters/paradex_adapter.py`
- Test: `tests/test_paradex_adapter.py` (new)

Step 1: Write the failing contract test.

Create `tests/test_paradex_adapter.py`:

```python
from common.venue_adapter import VenueAdapter
from adapters.paradex_adapter import ParadexVenueAdapter


def test_paradex_adapter_implements_interface():
    assert issubclass(ParadexVenueAdapter, VenueAdapter)
```

Add one mock-driven behavior test:

```python
from unittest.mock import MagicMock
from adapters.paradex_adapter import ParadexVenueAdapter


def test_get_open_orders_normalizes_results():
    proxy = MagicMock()
    proxy.fetch_orders.return_value = {"results": [{"id": "1", "market": "ETH-USD-PERP"}]}
    adapter = ParadexVenueAdapter(proxy)
    orders = adapter.get_open_orders("ETH-PERP")
    assert orders[0]["id"] == "1"
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_paradex_adapter.py -v
```

Expected: FAIL.

Step 3: Implement minimal adapter.

Create `adapters/paradex_adapter.py` implementing these methods first:

- `capabilities()`
- `get_all_markets()`
- `get_open_orders()`
- `cancel_order()`
- `get_account_state()`
- `place_order()`
- `get_snapshot()`
- `get_all_mids()`
- `connect()` no-op
- `set_leverage()` no-op or explicit `NotImplementedError` if unused

Suggested skeleton:

```python
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional

from paradex_py.common.order import Order, OrderSide, OrderType

from common.models import MarketSnapshot
from common.paradex_symbols import instrument_to_paradex_market, paradex_market_to_instrument
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities


class ParadexVenueAdapter(VenueAdapter):
    def __init__(self, proxy):
        self._proxy = proxy

    def connect(self, private_key: str, testnet: bool = True) -> None:
        return None

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            supports_alo=False,
            supports_trigger_orders=False,
            supports_builder_fee=False,
            supports_cross_margin=True,
        )

    def get_all_markets(self) -> list:
        return self._proxy.fetch_markets().get("results", [])

    def get_all_mids(self) -> Dict[str, str]:
        results = {}
        markets = self._proxy.fetch_markets().get("results", [])
        for market in markets:
            symbol = market.get("symbol")
            mark_price = market.get("mark_price") or market.get("last_traded_price") or "0"
            if symbol:
                results[paradex_market_to_instrument(symbol)] = str(mark_price)
        return results

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        market = instrument_to_paradex_market(instrument)
        summary = self._proxy.api_client.fetch_markets_summary({"market": market})
        items = summary.get("results", [])
        item = items[0] if items else {}
        bid = float(item.get("best_bid_price") or 0)
        ask = float(item.get("best_ask_price") or 0)
        mid = (bid + ask) / 2 if bid and ask else float(item.get("mark_price") or 0)
        spread = ((ask - bid) / mid * 10000) if bid and ask and mid else 0.0
        return MarketSnapshot(instrument=instrument, mid_price=mid, bid=bid, ask=ask, spread_bps=spread)

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        return []

    def place_order(self, instrument: str, side: str, size: float, price: float, tif: str = "Ioc", builder: Optional[dict] = None) -> Optional[Fill]:
        market = instrument_to_paradex_market(instrument)
        instruction = {"Ioc": "IOC", "Gtc": "GTC", "Alo": "POST_ONLY"}.get(tif, "GTC")
        order = Order(
            market=market,
            order_type=OrderType.Limit,
            order_side=OrderSide.Buy if side.lower() == "buy" else OrderSide.Sell,
            size=Decimal(str(size)),
            limit_price=Decimal(str(price)),
            instruction=instruction,
            reduce_only=False,
        )
        response = self._proxy.submit_order(order)
        order_id = response.get("id") or response.get("order_id") or ""
        if not order_id:
            return None
        return Fill(
            oid=str(order_id),
            instrument=instrument,
            side=side.lower(),
            price=price,
            quantity=size,
            timestamp_ms=0,
            fee=0.0,
        )

    def cancel_order(self, instrument: str, oid: str) -> bool:
        self._proxy.cancel_order(oid)
        return True

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        params = None
        if instrument:
            params = {"market": instrument_to_paradex_market(instrument)}
        return self._proxy.fetch_orders(params=params).get("results", [])

    def get_account_state(self) -> Dict:
        balances = self._proxy.fetch_balances().get("results", [])
        positions = self._proxy.fetch_positions().get("results", [])
        return {
            "balances": balances,
            "positions": positions,
            "spot_balances": balances,
        }

    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> None:
        return None
```

Step 4: Run the test.

Run:

```bash
pytest tests/test_paradex_adapter.py tests/test_venue_adapter.py -q
```

Expected: PASS.

Step 5: Commit.

```bash
git add adapters/paradex_adapter.py tests/test_paradex_adapter.py
git commit -m "feat: add initial paradex venue adapter"
```

---

### Task 9: Wire the factory to build live HL or Paradex adapters

Objective: Make runtime adapter construction venue-aware.

Files:
- Modify: `cli/venue_factory.py`
- Test: `tests/test_venue_factory.py`

Step 1: Extend the failing test.

Add mock-based tests:

```python
from unittest.mock import patch
from cli.venue_factory import build_live_venue_adapter


def test_build_live_hl_adapter_uses_hl_proxy():
    with patch("cli.venue_factory.HLProxy") as hl_proxy, patch("cli.venue_factory.DirectHLProxy") as direct:
        build_live_venue_adapter(venue="hl", mainnet=False, private_key="secret")
        hl_proxy.assert_called_once()
        direct.assert_called_once()
```

And a Paradex branch test:

```python
from unittest.mock import patch
from cli.venue_factory import build_live_venue_adapter


def test_build_live_paradex_adapter_uses_paradex_proxy():
    with patch("cli.venue_factory.ParadexProxy") as proxy_cls, patch("cli.venue_factory.ParadexVenueAdapter") as adapter_cls:
        build_live_venue_adapter(
            venue="paradex",
            mainnet=False,
            private_key="secret",
            account_address="0xabc",
        )
        proxy_cls.assert_called_once()
        adapter_cls.assert_called_once()
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_venue_factory.py -v
```

Expected: FAIL.

Step 3: Implement the live factory.

Update `cli/venue_factory.py`:

```python
from adapters.hl_adapter import HLVenueAdapter
from adapters.paradex_adapter import ParadexVenueAdapter
from cli.hl_adapter import DirectHLProxy
from parent.hl_proxy import HLProxy
from parent.paradex_proxy import ParadexProxy


def build_live_venue_adapter(*, venue: str, mainnet: bool, private_key: str, account_address: Optional[str] = None):
    venue = normalize_venue(venue)
    if venue == "hl":
        raw = HLProxy(private_key=private_key, testnet=not mainnet)
        return HLVenueAdapter(DirectHLProxy(raw))
    if venue == "paradex":
        if not account_address:
            raise RuntimeError("Paradex requires account_address")
        proxy = ParadexProxy(account_address=account_address, private_key=private_key, mainnet=mainnet)
        return ParadexVenueAdapter(proxy)
    raise ValueError(f"Unsupported venue: {venue}")
```

Step 4: Run the test.

Run:

```bash
pytest tests/test_venue_factory.py -v
```

Expected: PASS.

Step 5: Commit.

```bash
git add cli/venue_factory.py tests/test_venue_factory.py
git commit -m "feat: wire venue factory for hl and paradex"
```

---

### Task 10: Refactor `run` command to use the venue factory

Objective: Allow autonomous strategies to run on either venue.

Files:
- Modify: `cli/commands/run.py`
- Test: `tests/test_run_cmd_venue.py` (new)

Step 1: Write the failing test.

Create `tests/test_run_cmd_venue.py`:

```python
from unittest.mock import patch
from cli.commands.run import run_cmd


def test_run_cmd_uses_venue_factory():
    with patch("cli.commands.run.load_strategy") as load_strategy, \
         patch("cli.commands.run.build_live_venue_adapter") as build_live, \
         patch("cli.commands.run.TradingEngine") as engine_cls:
        strategy_cls = type("S", (), {"__init__": lambda self, strategy_id, **kw: None})
        load_strategy.return_value = strategy_cls
        run_cmd("avellaneda_mm", max_ticks=1)
        assert build_live.called
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_run_cmd_venue.py -v
```

Expected: FAIL.

Step 3: Refactor `cli/commands/run.py`.

Implementation notes:
- add `--venue` option with default `None`
- resolve with `normalize_venue()`
- if `mock or dry_run`, use `build_mock_venue_adapter()`
- else:
  - for `hl`: use `cfg.get_private_key()`
  - for `paradex`: use `ParadexConfig.from_env(mainnet=cfg.mainnet)`
  - call `build_live_venue_adapter(...)`
- pass the adapter to `TradingEngine`
- update user-facing output to show venue + network

Step 4: Run targeted tests.

Run:

```bash
pytest tests/test_run_cmd_venue.py tests/test_venue_factory.py -q
```

Expected: PASS.

Step 5: Commit.

```bash
git add cli/commands/run.py tests/test_run_cmd_venue.py
git commit -m "feat: make run command venue-aware"
```

---

### Task 11: Refactor manual `trade` command to use the venue factory

Objective: Allow single-order manual Paradex testing from the CLI.

Files:
- Modify: `cli/commands/trade.py`
- Test: `tests/test_trade_cmd_venue.py` (new)

Step 1: Write the failing test.

Create `tests/test_trade_cmd_venue.py`:

```python
from unittest.mock import MagicMock, patch
from cli.commands.trade import trade_cmd


def test_trade_cmd_builds_live_venue_adapter(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    with patch("cli.commands.trade.build_live_venue_adapter") as build_live:
        trade_cmd("ETH-PERP", "buy", 1.0)
        assert build_live.called
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_trade_cmd_venue.py -v
```

Expected: FAIL.

Step 3: Refactor `cli/commands/trade.py`.

Implementation notes:
- add `--venue` option
- use `normalize_venue()`
- use `ParadexConfig` when venue is `paradex`
- use factory-built adapter
- keep current price autofill logic using `get_snapshot()`
- update copy from “on Hyperliquid” to “on {venue}”

Step 4: Run targeted tests.

Run:

```bash
pytest tests/test_trade_cmd_venue.py -q
```

Expected: PASS.

Step 5: Commit.

```bash
git add cli/commands/trade.py tests/test_trade_cmd_venue.py
git commit -m "feat: make trade command venue-aware"
```

---

### Task 12: Refactor `account` command to use the venue factory

Objective: Allow balance/position inspection on Paradex.

Files:
- Modify: `cli/commands/account.py`
- Test: `tests/test_account_cmd_venue.py` (new)

Step 1: Write the failing test.

Create `tests/test_account_cmd_venue.py`:

```python
from unittest.mock import patch
from cli.commands.account import account_cmd


def test_account_cmd_builds_live_venue_adapter():
    with patch("cli.commands.account.build_live_venue_adapter") as build_live, \
         patch("cli.commands.account.account_table", return_value="ok"):
        build_live.return_value.get_account_state.return_value = {"positions": []}
        account_cmd()
        assert build_live.called
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_account_cmd_venue.py -v
```

Expected: FAIL.

Step 3: Refactor `cli/commands/account.py`.

Implementation notes:
- add `--venue`
- use factory and ParadexConfig as needed
- if `account_table()` is too HL-shaped for Paradex responses, keep command working by adding a minimal Paradex pretty-printer branch instead of forcing the old table format

Step 4: Run targeted tests.

Run:

```bash
pytest tests/test_account_cmd_venue.py -q
```

Expected: PASS.

Step 5: Commit.

```bash
git add cli/commands/account.py tests/test_account_cmd_venue.py
git commit -m "feat: make account command venue-aware"
```

---

### Task 13: Add a smoke-test command or script for Paradex testnet

Objective: Provide a safe end-to-end validation path before using the trading engine.

Files:
- Create: `scripts/paradex_smoke_test.py`
- Create: `tests/test_paradex_smoke_script_import.py`

Step 1: Write a small import test.

Create `tests/test_paradex_smoke_script_import.py`:

```python
from pathlib import Path


def test_smoke_script_exists():
    assert Path("scripts/paradex_smoke_test.py").exists()
```

Step 2: Run the test.

Run:

```bash
pytest tests/test_paradex_smoke_script_import.py -v
```

Expected: FAIL.

Step 3: Create the script.

`scripts/paradex_smoke_test.py` should:
- load `ParadexConfig.from_env(mainnet=False)`
- build `ParadexProxy`
- fetch account summary, balances, positions, markets
- print one market snapshot for `ETH-USD-PERP`
- optionally place and cancel a post-only dust-sized order if `--place-test-order` is passed

Skeleton:

```python
from common.paradex_config import ParadexConfig
from parent.paradex_proxy import ParadexProxy


def main():
    cfg = ParadexConfig.from_env(mainnet=False)
    proxy = ParadexProxy(account_address=cfg.account_address, private_key=cfg.private_key, mainnet=False)
    print(proxy.fetch_markets().get("results", [])[:3])
    print(proxy.fetch_balances())
    print(proxy.fetch_positions())


if __name__ == "__main__":
    main()
```

Step 4: Run the import test.

Run:

```bash
pytest tests/test_paradex_smoke_script_import.py -q
```

Expected: PASS.

Step 5: Manual smoke-test command.

Run:

```bash
export PARADEX_ACCOUNT_ADDRESS='0x...'
export PARADEX_PRIVATE_KEY='0x...'
python3 scripts/paradex_smoke_test.py
```

Expected: markets/balances/positions print successfully.

Step 6: Commit.

```bash
git add scripts/paradex_smoke_test.py tests/test_paradex_smoke_script_import.py
git commit -m "test: add paradex testnet smoke script"
```

---

### Task 14: Add real adapter behavior tests around order placement and cancel

Objective: Lock down the critical contract for live trading behavior.

Files:
- Modify: `tests/test_paradex_adapter.py`

Step 1: Add a failing place-order test.

```python
from unittest.mock import MagicMock
from adapters.paradex_adapter import ParadexVenueAdapter


def test_place_order_submits_limit_order_and_returns_fill_stub():
    proxy = MagicMock()
    proxy.submit_order.return_value = {"id": "abc123"}
    adapter = ParadexVenueAdapter(proxy)
    fill = adapter.place_order("ETH-PERP", "buy", 0.5, 2000.0, tif="Gtc")
    assert fill is not None
    assert fill.oid == "abc123"
    assert fill.instrument == "ETH-PERP"
```

Step 2: Add a failing cancel test.

```python
def test_cancel_order_returns_true_when_proxy_succeeds():
    proxy = MagicMock()
    adapter = ParadexVenueAdapter(proxy)
    assert adapter.cancel_order("ETH-PERP", "abc123") is True
    proxy.cancel_order.assert_called_once_with("abc123")
```

Step 3: Run the test.

Run:

```bash
pytest tests/test_paradex_adapter.py -q
```

Expected: FAIL if the adapter shape is incomplete.

Step 4: Make the implementation pass with the smallest possible code.

Step 5: Re-run.

```bash
pytest tests/test_paradex_adapter.py -q
```

Expected: PASS.

Step 6: Commit.

```bash
git add tests/test_paradex_adapter.py adapters/paradex_adapter.py
git commit -m "test: cover paradex order placement and cancellation"
```

---

### Task 15: Document operator workflow for Paradex subkeys

Objective: Make setup repeatable and safe for humans.

Files:
- Modify: `README.md`
- Optionally create: `docs/paradex.md`

Step 1: Add documentation section.

Include:
- why to use a subkey instead of the main key
- required env vars
- testnet vs prod note
- smoke-test instructions
- first live order instructions
- warning that Nunchi should not hold the withdrawal-capable main account key

Suggested README section:

```md
## Paradex integration

Use a Paradex subkey for automation.

Required environment variables:

```bash
export NUNCHI_VENUE=paradex
export PARADEX_ACCOUNT_ADDRESS=0x...
export PARADEX_PRIVATE_KEY=0x...
```

Test the connection first:

```bash
python3 scripts/paradex_smoke_test.py
```

Then place a small manual order:

```bash
hl trade ETH-PERP buy 0.01 --price 1000 --venue paradex
```
```

Step 2: Verify formatting and examples.

Run:

```bash
python3 -m compileall cli common adapters parent
pytest -q
```

Expected: no syntax errors, tests pass.

Step 3: Commit.

```bash
git add README.md docs/paradex.md
git commit -m "docs: add paradex setup and operating guide"
```

---

### Task 16: Optional second-pass cleanup for branding and CLI naming

Objective: Decide whether the CLI remains `hl` with multi-venue support or becomes venue-neutral.

Files:
- Modify later if desired: `cli/main.py`, `pyproject.toml`

Recommendation:
- Do not rename the CLI in the first Paradex delivery.
- Keep `hl` as the executable for now and add `--venue paradex`.
- Only rename after the Paradex integration is stable.

Rationale:
- Lower migration risk
- Smaller PR
- Easier rollback if Paradex support needs iteration

---

## Verification checklist

After all tasks:

Run:

```bash
pytest tests/test_config_venue.py \
       tests/test_engine_exceptions.py \
       tests/test_venue_factory.py \
       tests/test_paradex_config.py \
       tests/test_paradex_proxy.py \
       tests/test_paradex_symbols.py \
       tests/test_paradex_adapter.py \
       tests/test_trade_cmd_venue.py \
       tests/test_account_cmd_venue.py \
       tests/test_run_cmd_venue.py -q
```

Then run:

```bash
pytest -q
```

Then do manual testnet validation:

```bash
export NUNCHI_VENUE=paradex
export PARADEX_ACCOUNT_ADDRESS='0x...'
export PARADEX_PRIVATE_KEY='0x...'
python3 scripts/paradex_smoke_test.py
hl account --venue paradex
hl trade ETH-PERP buy 0.01 --price 1000 --venue paradex
```

Expected final state:
- smoke test reads private account data successfully
- manual order path can place and cancel a testnet order
- autonomous run path boots with `--venue paradex`
- Hyperliquid commands still work unchanged when venue defaults to `hl`

---

## Notes for the implementer

- Keep first delivery to testnet-safe functionality.
- Do not implement withdrawals, transfers, or privileged account management in the Paradex adapter.
- Prefer subkey-only trading.
- If `account_table()` is too HL-specific, create a small Paradex rendering branch instead of twisting the adapter output to mimic HL internals.
- If Paradex market metadata exposes better precision rules than the first-pass adapter uses, add a follow-up task to quantize size/price from live metadata before any production rollout.
- Private WebSocket reconciliation is important, but can land in a second PR if REST place/cancel is already working and tested.

Plan complete and saved. Ready to execute using subagent-driven-development — I can implement it task-by-task next.