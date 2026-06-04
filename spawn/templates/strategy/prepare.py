"""
Autotrader backtesting engine. Fixed evaluation harness — DO NOT MODIFY.
Downloads Hyperliquid historical data, runs backtests, computes scores.

Usage:
    python prepare.py                  # download data
    python prepare.py --symbols BTC    # download specific symbols
"""

import os
import sys
import time
import math
import signal
import argparse
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Constants (fixed, do not modify)
# ---------------------------------------------------------------------------

TIME_BUDGET = 120              # backtest time budget in seconds (2 minutes)
INITIAL_CAPITAL = 100_000.0    # $100K starting capital
MAKER_FEE = 0.0002             # 2 bps
TAKER_FEE = 0.0005             # 5 bps
SLIPPAGE_BPS = 1.0             # 1 bps simulated slippage
MAX_LEVERAGE = 20              # max leverage allowed
LOOKBACK_BARS = 500            # history buffer provided to strategy
BAR_INTERVAL = "1h"

# Crypto majors (CryptoCompare histohour, no geo-restrictions).
CRYPTO_SYMBOLS = ["BTC", "ETH", "SOL"]

# HIP-3 instruments. Data comes from the Hyperliquid info API (candleSnapshot +
# fundingHistory) using the dex-prefixed coin names, NOT CryptoCompare.
#
# Coin names + the dex serving them were discovered live on 2026-06-04 via:
#   curl ... -d '{"type":"perpDexs"}'                       # list dexs
#   curl ... -d '{"type":"meta","dex":"<dex>"}'             # list coins
#   curl ... -d '{"type":"candleSnapshot","req":{...}}'     # confirm OHLCV
#
#   yex (Nunchi yield-perp dex, TESTNET): yex:VXX, yex:US3M, yex:BTCSWP
#   xyz (tradfi/commodity dex, MAINNET) : GOLD, SILVER, COPPER, PLATINUM,
#       PALLADIUM, NATGAS, BRENTOIL, CL (WTI crude)  ← all confirmed returning
#       hourly candles. (WHEAT/CORN/URANIUM/ALUMINIUM are listed but returned 0
#       candles on 2026-06-04, so they are NOT enabled by default — add them to
#       SYMBOL_SOURCES once they carry history.)
#
# Each entry: bare symbol -> {"coin": <hl coin>, "network": "mainnet"|"testnet"}.
SYMBOL_SOURCES: dict = {
    "BTCSWP": {"coin": "yex:BTCSWP", "network": "testnet"},
    "GOLD": {"coin": "xyz:GOLD", "network": "mainnet"},
    "SILVER": {"coin": "xyz:SILVER", "network": "mainnet"},
    "COPPER": {"coin": "xyz:COPPER", "network": "mainnet"},
    "PLATINUM": {"coin": "xyz:PLATINUM", "network": "mainnet"},
    "PALLADIUM": {"coin": "xyz:PALLADIUM", "network": "mainnet"},
    "NATGAS": {"coin": "xyz:NATGAS", "network": "mainnet"},
    "BRENTOIL": {"coin": "xyz:BRENTOIL", "network": "mainnet"},
    "CL": {"coin": "xyz:CL", "network": "mainnet"},  # WTI crude oil
}

HIP3_SYMBOLS = list(SYMBOL_SOURCES.keys())

# Full symbol universe. Crypto first (CryptoCompare), then HIP-3 (HL info API).
SYMBOLS = CRYPTO_SYMBOLS + HIP3_SYMBOLS

# Date splits (UTC timestamps). Overridable via env for instruments whose
# listing history is short (commodities + BTCSWP only carry data from late
# 2025 / early 2026 — confirmed 2026-06-04), so a recent-only window can be
# backtested without editing this file:
#   AUTOTRADER_TRAIN_START / _TRAIN_END / _VAL_START / _VAL_END /
#   _TEST_START / _TEST_END  (YYYY-MM-DD)
TRAIN_START = os.environ.get("AUTOTRADER_TRAIN_START", "2023-06-01")
TRAIN_END = os.environ.get("AUTOTRADER_TRAIN_END", "2024-06-30")
VAL_START = os.environ.get("AUTOTRADER_VAL_START", "2024-07-01")
VAL_END = os.environ.get("AUTOTRADER_VAL_END", "2025-03-31")
TEST_START = os.environ.get("AUTOTRADER_TEST_START", "2025-04-01")
TEST_END = os.environ.get("AUTOTRADER_TEST_END", "2025-12-31")

HOURS_PER_YEAR = 8760

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autotrader")
DATA_DIR = os.path.join(CACHE_DIR, "data")
ISFR_HISTORY_PATH_ENV = "NUNCHI_ISFR_HISTORY_PATH"
ISFR_CACHE_FILE = os.path.join(DATA_DIR, "ISFR_1h.parquet")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BarData:
    symbol: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float
    history: pd.DataFrame  # last LOOKBACK_BARS bars
    isfr_rate: float = 0.0

@dataclass
class Signal:
    symbol: str
    target_position: float   # target USD notional (signed: +long, -short)
    order_type: str = "market"

@dataclass
class PortfolioState:
    cash: float
    positions: dict          # symbol -> signed USD notional
    entry_prices: dict       # symbol -> avg entry price
    equity: float = 0.0
    timestamp: int = 0

@dataclass
class BacktestResult:
    sharpe: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    annual_turnover: float = 0.0
    backtest_seconds: float = 0.0
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)

# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_INFO_URL_TESTNET = "https://api.hyperliquid-testnet.xyz/info"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/histohour"


def _hl_base_url(network: str = "mainnet") -> str:
    """Return the HL info endpoint for a network. yex (Nunchi) is testnet-only;
    xyz commodities are mainnet."""
    return HL_INFO_URL_TESTNET if network == "testnet" else HL_INFO_URL

def _download_cryptocompare_candles(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download hourly OHLCV from CryptoCompare (no geo-restrictions)."""
    all_rows = []
    # CryptoCompare uses 'toTs' (end timestamp in seconds) and returns up to 2000 bars
    current_end = end_ms // 1000
    start_s = start_ms // 1000

    while current_end > start_s:
        params = {
            "fsym": symbol,
            "tsym": "USD",
            "limit": 2000,
            "toTs": current_end,
        }
        resp = requests.get(CRYPTOCOMPARE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        bars = data.get("Data", {}).get("Data", [])
        if not bars:
            break
        for bar in bars:
            ts_s = bar["time"]
            if ts_s < start_s:
                continue
            all_rows.append({
                "timestamp": ts_s * 1000,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volumefrom", 0)),
            })
        # Move window back
        earliest = bars[0]["time"]
        if earliest >= current_end:
            break
        current_end = earliest - 1
        time.sleep(0.3)

    if not all_rows:
        return pd.DataFrame()
    df = pd.DataFrame(all_rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def _download_hl_funding(symbol: str, start_ms: int, end_ms: int,
                         base_url: str = HL_INFO_URL) -> pd.DataFrame:
    """Download funding rate history from Hyperliquid.

    `symbol` is the HL coin name (e.g. "BTC" or a dex-prefixed "yex:BTCSWP" /
    "xyz:GOLD"). `base_url` selects mainnet vs testnet.
    """
    all_rows = []
    current = start_ms
    while current < end_ms:
        body = {
            "type": "fundingHistory",
            "coin": symbol,
            "startTime": current,
            "endTime": min(current + 30 * 24 * 3600 * 1000, end_ms),
        }
        try:
            resp = requests.post(base_url, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            for row in data:
                all_rows.append({
                    "timestamp": int(row["time"]),
                    "funding_rate": float(row["fundingRate"]),
                })
            current = int(data[-1]["time"]) + 1
        except Exception:
            break
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    return pd.DataFrame(all_rows)


def _normalize_isfr_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize user-provided ISFR history into timestamp-ms + decimal rate."""
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])

    timestamp_col = "timestamp" if "timestamp" in df.columns else "time" if "time" in df.columns else "date" if "date" in df.columns else None
    value_col = None
    for candidate in ("isfr_rate", "isfr", "isfr_score", "composite_bps", "rate_bps"):
        if candidate in df.columns:
            value_col = candidate
            break
    if timestamp_col is None or value_col is None:
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])

    out = df[[timestamp_col, value_col]].copy()
    out.columns = ["timestamp", "isfr_rate"]
    if not np.issubdtype(out["timestamp"].dtype, np.number):
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce").astype("int64") // 1_000_000
    out["timestamp"] = pd.to_numeric(out["timestamp"], errors="coerce")
    out["isfr_rate"] = pd.to_numeric(out["isfr_rate"], errors="coerce")
    out = out.dropna().sort_values("timestamp").drop_duplicates("timestamp")
    if out.empty:
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])

    # Accept seconds, milliseconds, bps, percent-like scores (1.37), or decimals.
    if out["timestamp"].max() < 10_000_000_000:
        out["timestamp"] = out["timestamp"] * 1000
    median_abs = out["isfr_rate"].abs().median()
    if median_abs > 10:
        out["isfr_rate"] = out["isfr_rate"] / 10_000.0
    elif median_abs > 0.5:
        out["isfr_rate"] = out["isfr_rate"] / 100.0
    return out[["timestamp", "isfr_rate"]].reset_index(drop=True)


def _load_isfr_history_from_path(path: str) -> pd.DataFrame:
    if not path:
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])
    try:
        if expanded.endswith(".parquet"):
            raw = pd.read_parquet(expanded)
        else:
            raw = pd.read_csv(expanded)
    except Exception:
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])
    return _normalize_isfr_frame(raw)


def _synthetic_isfr_proxy(start_ms: int, end_ms: int) -> pd.DataFrame:
    """Deterministic ISFR proxy for local research when canonical history is absent."""
    timestamps = np.arange(start_ms, end_ms + 1, 3600 * 1000, dtype=np.int64)
    if len(timestamps) == 0:
        return pd.DataFrame(columns=["timestamp", "isfr_rate"])
    hours = (timestamps - timestamps[0]) / (3600 * 1000)
    base = 0.0137
    cycle = 0.0014 * np.sin(hours / (24 * 9))
    stress = 0.0008 * np.sin(hours / (24 * 31) + 0.7)
    pulse = 0.0005 * np.maximum(0, np.sin(hours / (24 * 5) - 1.2))
    return pd.DataFrame({"timestamp": timestamps, "isfr_rate": base + cycle + stress + pulse})


def _load_or_build_isfr_series(start_ms: int, end_ms: int) -> pd.DataFrame:
    path = os.environ.get(ISFR_HISTORY_PATH_ENV, "").strip()
    source = _load_isfr_history_from_path(path)
    if source.empty and os.path.exists(ISFR_CACHE_FILE):
        try:
            source = pd.read_parquet(ISFR_CACHE_FILE)
        except Exception:
            source = pd.DataFrame(columns=["timestamp", "isfr_rate"])
    if source.empty:
        source = _synthetic_isfr_proxy(start_ms, end_ms)
    source = _normalize_isfr_frame(source)
    if not source.empty:
        os.makedirs(DATA_DIR, exist_ok=True)
        source.to_parquet(ISFR_CACHE_FILE, index=False)
    return source


def _merge_isfr(df: pd.DataFrame, isfr: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isfr.empty:
        df["isfr_rate"] = 0.0
        return df
    left = df.drop(columns=["isfr_rate"], errors="ignore").drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    right = isfr.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    merged = pd.merge_asof(left, right, on="timestamp", direction="backward")
    merged["isfr_rate"] = merged["isfr_rate"].bfill().fillna(0.0)
    return merged


def _download_hl_candles(symbol: str, interval: str, start_ms: int, end_ms: int,
                         base_url: str = HL_INFO_URL) -> pd.DataFrame:
    """Download OHLCV candles from Hyperliquid.

    `symbol` is the HL coin name (e.g. "BTC" or a dex-prefixed "yex:BTCSWP" /
    "xyz:GOLD"). `base_url` selects mainnet vs testnet.
    """
    all_rows = []
    current = start_ms
    chunk_ms = 30 * 24 * 3600 * 1000  # 30 days
    while current < end_ms:
        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": interval,
                "startTime": current,
                "endTime": min(current + chunk_ms, end_ms),
            }
        }
        try:
            resp = requests.post(base_url, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                current += chunk_ms
                continue
            for row in data:
                all_rows.append({
                    "timestamp": int(row["t"]),
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row["v"]),
                })
            current = int(data[-1]["t"]) + 3600 * 1000
        except Exception:
            current += chunk_ms
        time.sleep(0.2)
    return pd.DataFrame(all_rows)


def _download_hip3_symbol(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Download OHLCV + funding for a HIP-3 instrument (BTCSWP / commodities).

    Uses the HL info API with the real dex-prefixed coin name and the right
    network (yex=testnet, xyz=mainnet) per SYMBOL_SOURCES. Returns OHLCV merged
    with funding (funding columns may be all-zero for HIP-3 dexs that don't
    publish funding history yet — that's fine, the engine treats 0 funding as
    no carry).
    """
    src = SYMBOL_SOURCES[symbol]
    coin = src["coin"]
    base_url = _hl_base_url(src.get("network", "mainnet"))
    print(f"  {symbol}: downloading HIP-3 candles ({coin} @ {src.get('network','mainnet')})...")
    df = _download_hl_candles(coin, "1h", start_ms, end_ms, base_url=base_url)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    funding = _download_hl_funding(coin, start_ms, end_ms, base_url=base_url)
    if not funding.empty:
        funding = funding.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        df = pd.merge_asof(df, funding, on="timestamp", direction="backward")
    if "funding_rate" not in df.columns:
        df["funding_rate"] = 0.0
    df["funding_rate"] = df["funding_rate"].fillna(0.0)
    return df


def download_data(symbols=None):
    """Download historical OHLCV + funding data for all symbols."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if symbols is None:
        symbols = SYMBOLS

    start_ms = int(pd.Timestamp(TRAIN_START, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(TEST_END, tz="UTC").timestamp() * 1000)
    isfr = _load_or_build_isfr_series(start_ms, end_ms)
    print(f"  ISFR: loaded {len(isfr)} hourly points")

    for symbol in symbols:
        filepath = os.path.join(DATA_DIR, f"{symbol}_1h.parquet")
        if os.path.exists(filepath):
            existing = pd.read_parquet(filepath)
            if "isfr_rate" not in existing.columns:
                existing = _merge_isfr(existing, isfr)
                existing.to_parquet(filepath, index=False)
                print(f"  {symbol}: added ISFR feature to {len(existing)} cached bars")
            else:
                print(f"  {symbol}: already have {len(existing)} bars")
            continue

        if symbol in SYMBOL_SOURCES:
            # HIP-3 instrument (BTCSWP / commodities) — HL info API, not CC.
            df = _download_hip3_symbol(symbol, start_ms, end_ms)
            if df.empty:
                print(f"  {symbol}: NO DATA AVAILABLE, skipping")
                continue
        else:
            print(f"  {symbol}: downloading candles from CryptoCompare...")

            # Use CryptoCompare for reliable historical OHLCV (no geo-restrictions)
            df = _download_cryptocompare_candles(symbol, start_ms, end_ms)
            if len(df) < 100:
                print(f"  {symbol}: CryptoCompare insufficient ({len(df)} bars), trying HL...")
                df = _download_hl_candles(symbol, "1h", start_ms, end_ms)

            if df.empty:
                print(f"  {symbol}: NO DATA AVAILABLE, skipping")
                continue

            # Download funding rates
            print(f"  {symbol}: downloading funding rates...")
            funding = _download_hl_funding(symbol, start_ms, end_ms)

            # Merge
            df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            if not funding.empty:
                funding = funding.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
                # Merge nearest — funding is every 8h, candles every 1h
                df = pd.merge_asof(df, funding, on="timestamp", direction="backward")
            if "funding_rate" not in df.columns:
                df["funding_rate"] = 0.0
            df["funding_rate"] = df["funding_rate"].fillna(0.0)

        df = _merge_isfr(df, isfr)

        df.to_parquet(filepath, index=False)
        print(f"  {symbol}: saved {len(df)} bars to {filepath}")


def load_data(split: str = "val") -> dict:
    """Load OHLCV+funding data for the given split. Returns {symbol: DataFrame}."""
    splits = {
        "train": (TRAIN_START, TRAIN_END),
        "val": (VAL_START, VAL_END),
        "test": (TEST_START, TEST_END),
    }
    assert split in splits, f"split must be one of {list(splits.keys())}"
    start_str, end_str = splits[split]
    start_ms = int(pd.Timestamp(start_str, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end_str, tz="UTC").timestamp() * 1000)

    result = {}
    for symbol in SYMBOLS:
        filepath = os.path.join(DATA_DIR, f"{symbol}_1h.parquet")
        if not os.path.exists(filepath):
            continue
        df = pd.read_parquet(filepath)
        if "isfr_rate" not in df.columns:
            df["isfr_rate"] = 0.0
        mask = (df["timestamp"] >= start_ms) & (df["timestamp"] < end_ms)
        split_df = df[mask].reset_index(drop=True)
        if len(split_df) > 0:
            result[symbol] = split_df
    return result

# ---------------------------------------------------------------------------
# Backtesting engine (DO NOT CHANGE)
# ---------------------------------------------------------------------------

def run_backtest(strategy, data: dict) -> BacktestResult:
    """
    Run strategy over data. Returns BacktestResult with full metrics.
    Enforces TIME_BUDGET.
    """
    t_start = time.time()

    # Build unified timeline
    all_timestamps = set()
    for symbol, df in data.items():
        all_timestamps.update(df["timestamp"].tolist())
    timestamps = sorted(all_timestamps)

    if not timestamps:
        return BacktestResult()

    # Index data by (symbol, timestamp) for fast lookup
    indexed = {}
    for symbol, df in data.items():
        indexed[symbol] = df.set_index("timestamp")

    # Portfolio state
    portfolio = PortfolioState(
        cash=INITIAL_CAPITAL,
        positions={},
        entry_prices={},
        equity=INITIAL_CAPITAL,
        timestamp=0,
    )

    equity_curve = [INITIAL_CAPITAL]
    hourly_returns = []
    trade_log = []
    total_volume = 0.0
    prev_equity = INITIAL_CAPITAL

    # History buffers
    history_buffers = {symbol: [] for symbol in data}

    for ts in timestamps:
        elapsed = time.time() - t_start
        if elapsed > TIME_BUDGET:
            break

        portfolio.timestamp = ts

        # Build bar data
        bar_data = {}
        for symbol in data:
            if symbol not in indexed or ts not in indexed[symbol].index:
                continue
            row = indexed[symbol].loc[ts]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]

            # Update history buffer
            bar_dict = {
                "timestamp": ts,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "funding_rate": row.get("funding_rate", 0.0),
                "isfr_rate": row.get("isfr_rate", 0.0),
            }
            history_buffers[symbol].append(bar_dict)
            if len(history_buffers[symbol]) > LOOKBACK_BARS:
                history_buffers[symbol] = history_buffers[symbol][-LOOKBACK_BARS:]

            hist_df = pd.DataFrame(history_buffers[symbol])

            bar_data[symbol] = BarData(
                symbol=symbol,
                timestamp=ts,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                funding_rate=row.get("funding_rate", 0.0),
                history=hist_df,
                isfr_rate=row.get("isfr_rate", 0.0),
            )

        if not bar_data:
            continue

        # Update portfolio equity (mark-to-market)
        unrealized_pnl = 0.0
        for sym, pos_notional in portfolio.positions.items():
            if sym in bar_data:
                current_price = bar_data[sym].close
                entry_price = portfolio.entry_prices.get(sym, current_price)
                if entry_price > 0:
                    price_change = (current_price - entry_price) / entry_price
                    unrealized_pnl += pos_notional * price_change

        portfolio.equity = portfolio.cash + sum(abs(v) for v in portfolio.positions.values()) + unrealized_pnl

        # Apply funding rates (on open positions)
        for sym, pos_notional in list(portfolio.positions.items()):
            if sym in bar_data:
                fr = bar_data[sym].funding_rate
                # Funding: longs pay when positive, shorts receive
                # Applied every 8h, but we have hourly bars so scale by 1/8
                funding_payment = pos_notional * fr / 8.0
                portfolio.cash -= funding_payment

        # Get signals from strategy
        try:
            signals = strategy.on_bar(bar_data, portfolio)
        except Exception:
            signals = []

        # Execute signals
        for sig in (signals or []):
            if sig.symbol not in bar_data:
                continue

            current_price = bar_data[sig.symbol].close
            current_pos = portfolio.positions.get(sig.symbol, 0.0)
            delta = sig.target_position - current_pos

            if abs(delta) < 1.0:  # < $1 change, skip
                continue

            # Check leverage constraint
            new_positions = dict(portfolio.positions)
            new_positions[sig.symbol] = sig.target_position
            total_exposure = sum(abs(v) for v in new_positions.values())
            if total_exposure > portfolio.equity * MAX_LEVERAGE:
                continue

            # Apply slippage and fees
            slippage = current_price * SLIPPAGE_BPS / 10000
            fee_rate = TAKER_FEE
            if delta > 0:  # buying
                exec_price = current_price + slippage
            else:  # selling
                exec_price = current_price - slippage

            fee = abs(delta) * fee_rate
            portfolio.cash -= fee
            total_volume += abs(delta)

            # Update position
            if sig.target_position == 0:
                # Closing position — realize PnL
                if sig.symbol in portfolio.entry_prices:
                    entry = portfolio.entry_prices[sig.symbol]
                    if entry > 0:
                        pnl = current_pos * (exec_price - entry) / entry
                        portfolio.cash += abs(current_pos) + pnl
                    del portfolio.entry_prices[sig.symbol]
                if sig.symbol in portfolio.positions:
                    del portfolio.positions[sig.symbol]
                trade_log.append(("close", sig.symbol, delta, exec_price, pnl if 'pnl' in dir() else 0))
            else:
                if current_pos == 0:
                    # Opening new position
                    portfolio.cash -= abs(sig.target_position)
                    portfolio.positions[sig.symbol] = sig.target_position
                    portfolio.entry_prices[sig.symbol] = exec_price
                    trade_log.append(("open", sig.symbol, delta, exec_price, 0))
                else:
                    # Modifying position
                    old_notional = abs(current_pos)
                    old_entry = portfolio.entry_prices.get(sig.symbol, exec_price)
                    # Realize PnL on reduced portion
                    if abs(sig.target_position) < abs(current_pos):
                        reduced = abs(current_pos) - abs(sig.target_position)
                        if old_entry > 0:
                            pnl = (current_pos / abs(current_pos)) * reduced * (exec_price - old_entry) / old_entry
                        else:
                            pnl = 0
                        portfolio.cash += reduced + pnl
                    elif abs(sig.target_position) > abs(current_pos):
                        added = abs(sig.target_position) - abs(current_pos)
                        portfolio.cash -= added
                        # Weighted average entry
                        if old_notional + added > 0:
                            new_entry = (old_entry * old_notional + exec_price * added) / (old_notional + added)
                            portfolio.entry_prices[sig.symbol] = new_entry
                    portfolio.positions[sig.symbol] = sig.target_position
                    trade_log.append(("modify", sig.symbol, delta, exec_price, 0))

        # Recalculate equity after trades
        unrealized_pnl = 0.0
        for sym, pos_notional in portfolio.positions.items():
            if sym in bar_data:
                current_price = bar_data[sym].close
                entry_price = portfolio.entry_prices.get(sym, current_price)
                if entry_price > 0:
                    price_change = (current_price - entry_price) / entry_price
                    unrealized_pnl += pos_notional * price_change

        current_equity = portfolio.cash + sum(abs(v) for v in portfolio.positions.values()) + unrealized_pnl
        equity_curve.append(current_equity)

        # Hourly return
        if prev_equity > 0:
            hourly_returns.append((current_equity - prev_equity) / prev_equity)
        prev_equity = current_equity

        # Liquidation check
        if current_equity < INITIAL_CAPITAL * 0.01:
            break

    t_end = time.time()

    # Compute metrics
    returns = np.array(hourly_returns) if hourly_returns else np.array([0.0])
    eq = np.array(equity_curve)

    # Sharpe ratio (annualized from hourly)
    if returns.std() > 0:
        sharpe = (returns.mean() / returns.std()) * np.sqrt(HOURS_PER_YEAR)
    else:
        sharpe = 0.0

    # Total return
    final_equity = eq[-1] if len(eq) > 0 else INITIAL_CAPITAL
    total_return_pct = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # Max drawdown
    peak = np.maximum.accumulate(eq)
    drawdown = (peak - eq) / np.where(peak > 0, peak, 1)
    max_drawdown_pct = drawdown.max() * 100

    # Win rate and profit factor
    trade_pnls = [t[4] for t in trade_log if t[0] == "close"]
    num_trades = len(trade_log)
    if trade_pnls:
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]
        win_rate_pct = len(wins) / len(trade_pnls) * 100 if trade_pnls else 0
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-10
        profit_factor = gross_profit / gross_loss
    else:
        win_rate_pct = 0.0
        profit_factor = 0.0

    # Annual turnover
    data_hours = len(timestamps)
    if data_hours > 0:
        annual_turnover = total_volume * (HOURS_PER_YEAR / data_hours)
    else:
        annual_turnover = 0.0

    return BacktestResult(
        sharpe=sharpe,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        num_trades=num_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        annual_turnover=annual_turnover,
        backtest_seconds=t_end - t_start,
        equity_curve=equity_curve,
        trade_log=trade_log,
    )

# ---------------------------------------------------------------------------
# Evaluation metric (DO NOT CHANGE — this is the fixed metric)
# ---------------------------------------------------------------------------

def compute_score(result: BacktestResult) -> float:
    """
    Composite risk-adjusted score (HIGHER is better).

    score = sharpe * sqrt(trade_count_factor) - drawdown_penalty - turnover_penalty

    Hard cutoffs for degenerate strategies.

    NOTE (all-symbol support): the penalties are instrument-AGNOSTIC — they act on
    the portfolio-level equity curve (Sharpe, drawdown, turnover) regardless of
    which symbols traded, and the √8760 annualization holds because every symbol
    (crypto + HIP-3 commodities + BTCSWP) is sampled on the SAME 1h bar grid. So
    no per-instrument re-tuning was needed when BTCSWP/commodities were added.
    """
    # Hard cutoffs
    if result.num_trades < 10:
        return -999.0
    if result.max_drawdown_pct > 50.0:
        return -999.0
    final_equity = result.equity_curve[-1] if result.equity_curve else INITIAL_CAPITAL
    if final_equity < INITIAL_CAPITAL * 0.5:
        return -999.0

    # Trade count factor: full credit at 50+ trades
    trade_count_factor = min(result.num_trades / 50.0, 1.0)

    # Drawdown penalty: no penalty below 15%, then 5x per additional percent
    drawdown_penalty = max(0, result.max_drawdown_pct - 15.0) * 0.05

    # Turnover penalty: penalize excessive churning (>500x annual)
    turnover_ratio = result.annual_turnover / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0
    turnover_penalty = max(0, turnover_ratio - 500) * 0.001

    score = result.sharpe * math.sqrt(trade_count_factor) - drawdown_penalty - turnover_penalty
    return score

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare data for autotrader")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to download (default: all)")
    args = parser.parse_args()

    print(f"Cache directory: {CACHE_DIR}")
    print()

    print("Downloading data...")
    download_data(args.symbols)
    print()
    print("Done! Ready to backtest.")
