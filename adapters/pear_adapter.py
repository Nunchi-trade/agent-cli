"""Pear Protocol VenueAdapter.

Pear is a pair-trading layer on top of Hyperliquid. Each instrument is a basket
of one long leg vs one short leg (e.g. "ETH-BTC" = long ETH, short BTC). The
REST API at https://hl-v2.pearprotocol.io is custom (not HL-passthrough): it
issues HL orders server-side and exposes a basket-aware surface.

Reference: https://docs.pearprotocol.io/api-integration/

Surface mapping (Pear endpoint  ->  VenueAdapter method):
    GET    /markets                          -> get_all_markets
    GET    /accounts                         -> get_account_state
    GET    /orders/open                      -> get_open_orders
    POST   /positions                        -> place_order (executionType=MARKET)
                                             -> place_trigger_order (TRIGGER)
    DELETE /orders/{orderId}/cancel          -> cancel_order, cancel_trigger_order
    GET    /auth/eip712-message              -> PearAuth.bootstrap_with_wallet
    POST   /auth/authenticate                -> PearAuth (eip712 + api_key modes)
    POST   /auth/refresh                     -> PearAuth._refresh
    POST   /api-keys                         -> PearAuth.create_api_key

Intentionally not implemented (no documented Pear endpoint at time of writing):
    get_snapshot, get_candles, get_all_mids  -> raise NotImplementedError
    tif="Alo" / per-leg limit prices         -> raise NotImplementedError
    set_leverage on coin (no position yet)   -> deferred to next place_order
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import requests

from common.models import MarketSnapshot
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities

log = logging.getLogger("adapters.pear")

BASE_URL = "https://hl-v2.pearprotocol.io"
WS_URL = "wss://hl-v2.pearprotocol.io/ws"
BUILDER_ADDRESS = "0xA47D4d99191db54A4829cdf3de2417E527c3b042"
DEFAULT_CLIENT_ID = "APITRADER"

ACCESS_TOKEN_TTL_S = 15 * 60
REFRESH_LEEWAY_S = 30


# ---------------------------------------------------------------------------
# HTTP transport (injectable for tests)
# ---------------------------------------------------------------------------

class PearHTTPClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]: ...


class RequestsClient:
    """Default PearHTTPClient backed by the requests library."""

    def __init__(self, base_url: str = BASE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = self._session.request(
            method, url, params=params, json=json, headers=headers, timeout=self.timeout
        )
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()


# ---------------------------------------------------------------------------
# Pair instruments
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PairAsset:
    asset: str
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {"asset": self.asset, "weight": self.weight}


def _parse_pair(instrument: str) -> tuple[str, str]:
    """Split 'ETH-BTC' into ('ETH', 'BTC'). Reject anything else."""
    parts = instrument.split("-")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Pear instruments must be pair-format 'LEG_A-LEG_B' (got {instrument!r})"
        )
    return parts[0], parts[1]


def _legs_for_side(instrument: str, side: str) -> tuple[List[Dict], List[Dict]]:
    """Convert (instrument, side) to (longAssets, shortAssets) per Pear schema.

    side="buy"  on 'ETH-BTC' -> long ETH, short BTC
    side="sell" on 'ETH-BTC' -> long BTC, short ETH  (flip legs)
    """
    leg_a, leg_b = _parse_pair(instrument)
    s = side.lower()
    if s == "buy":
        return [PairAsset(leg_a).to_dict()], [PairAsset(leg_b).to_dict()]
    if s == "sell":
        return [PairAsset(leg_b).to_dict()], [PairAsset(leg_a).to_dict()]
    raise ValueError(f"side must be 'buy' or 'sell' (got {side!r})")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@dataclass
class _Tokens:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0  # epoch seconds
    address: str = ""
    client_id: str = ""


class PearAuth:
    """Manages Pear authentication state.

    Two entry paths:
      * `bootstrap_with_api_key` — steady-state for headless clients.
      * `bootstrap_with_wallet` — one-time EIP-712 onboarding from a private key
        (also used to mint an API key via `create_api_key`).

    Access tokens last 15 minutes; this class auto-refreshes via
    POST /auth/refresh before each call when the leeway is exceeded.
    """

    def __init__(
        self,
        http: PearHTTPClient,
        *,
        address: str,
        client_id: str = DEFAULT_CLIENT_ID,
    ):
        self._http = http
        self._tokens = _Tokens(address=address.lower(), client_id=client_id)

    # ----- public bootstrap paths --------------------------------------

    def bootstrap_with_api_key(self, api_key: str) -> None:
        body = {
            "method": "api_key",
            "address": self._tokens.address,
            "clientId": self._tokens.client_id,
            "details": {"apiKey": api_key},
        }
        self._apply_token_response(self._http.request("POST", "/auth/authenticate", json=body))

    def bootstrap_with_wallet(self, private_key: str) -> None:
        """Sign the EIP-712 message Pear returns and exchange for tokens.

        Requires `eth_account`. The signed payload structure is server-defined;
        we treat it opaquely.
        """
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        msg_resp = self._http.request(
            "GET",
            "/auth/eip712-message",
            params={"address": self._tokens.address, "clientId": self._tokens.client_id},
        )
        signable = encode_typed_data(full_message={
            "domain": msg_resp["domain"],
            "types": msg_resp["types"],
            "primaryType": msg_resp["primaryType"],
            "message": msg_resp["message"],
        })
        signed = Account.from_key(private_key).sign_message(signable)
        body = {
            "method": "eip712",
            "address": self._tokens.address,
            "clientId": self._tokens.client_id,
            "details": {
                "signature": signed.signature.hex(),
                "timestamp": int(msg_resp.get("timestamp", time.time())),
            },
        }
        self._apply_token_response(self._http.request("POST", "/auth/authenticate", json=body))

    def create_api_key(self, name: Optional[str] = None) -> str:
        """Mint a fresh API key — returns the secret (only shown once)."""
        body: Dict[str, Any] = {}
        if name:
            body["name"] = name
        resp = self._http.request("POST", "/api-keys", json=body, headers=self.headers())
        return resp["apiKey"]

    # ----- per-request --------------------------------------------------

    def headers(self) -> Dict[str, str]:
        self._refresh_if_needed()
        if not self._tokens.access_token:
            raise RuntimeError("PearAuth has no access token — call bootstrap_* first")
        return {"Authorization": f"Bearer {self._tokens.access_token}"}

    # ----- internals ----------------------------------------------------

    def _apply_token_response(self, resp: Dict[str, Any]) -> None:
        self._tokens.access_token = resp["accessToken"]
        self._tokens.refresh_token = resp.get("refreshToken", "")
        expires_in = int(resp.get("expiresIn", ACCESS_TOKEN_TTL_S))
        self._tokens.expires_at = time.time() + expires_in

    def _refresh_if_needed(self) -> None:
        if not self._tokens.refresh_token:
            return
        if time.time() < self._tokens.expires_at - REFRESH_LEEWAY_S:
            return
        resp = self._http.request(
            "POST", "/auth/refresh", json={"refreshToken": self._tokens.refresh_token}
        )
        self._apply_token_response(resp)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@dataclass
class _OrderDefaults:
    leverage: int = 1
    slippage: float = 0.005  # 50bps; Pear range is 0.001-0.1


def _position_response_to_fill(
    resp: Dict[str, Any], instrument: str, side: str
) -> Optional[Fill]:
    """Best-effort conversion of POST /positions response to a Fill.

    Pear returns {orderId, fills: [...]} for synchronous executions. We
    aggregate fills into a single Fill row matching VenueAdapter semantics.
    If the order is still pending (no fills yet), return None.
    """
    fills = resp.get("fills") or []
    if not fills:
        return None
    total_size = 0.0
    notional = 0.0
    fee = 0.0
    last_ts = 0
    for f in fills:
        sz = float(f.get("size", 0) or 0)
        px = float(f.get("price", 0) or 0)
        total_size += sz
        notional += sz * px
        fee += float(f.get("externalFeePaid", 0) or 0) + float(f.get("builderFeePaid", 0) or 0)
        last_ts = max(last_ts, int(f.get("fillTime", 0) or 0))
    avg_price = (notional / total_size) if total_size > 0 else 0.0
    return Fill(
        oid=str(resp.get("orderId", "")),
        instrument=instrument,
        side=side,
        price=avg_price,
        quantity=total_size,
        timestamp_ms=last_ts or int(time.time() * 1000),
        fee=fee,
    )


class PearVenueAdapter(VenueAdapter):
    """VenueAdapter backed by Pear Protocol's REST API."""

    def __init__(
        self,
        http: Optional[PearHTTPClient] = None,
        auth: Optional[PearAuth] = None,
        defaults: Optional[_OrderDefaults] = None,
    ):
        self._http = http or RequestsClient()
        self._auth = auth
        self._defaults = defaults or _OrderDefaults()
        # set_leverage(coin) writes here; the next place_order on a matching
        # pair picks it up. Keyed by leg asset (BTC, ETH, ...).
        self._leverage_by_leg: Dict[str, int] = {}

    # --- Connection -----------------------------------------------------

    def connect(self, private_key: str, testnet: bool = True) -> None:
        """Derive the address from `private_key` and bootstrap with EIP-712.

        For headless deployments, prefer constructing `PearAuth` separately
        with `bootstrap_with_api_key` and passing it to the adapter directly.
        `testnet` is accepted for VenueAdapter interface parity; Pear's
        environments are selected by `base_url`, not a runtime flag.
        """
        from eth_account import Account

        address = Account.from_key(private_key).address.lower()
        if self._auth is None:
            self._auth = PearAuth(self._http, address=address)
        self._auth.bootstrap_with_wallet(private_key)

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            supports_alo=False,
            supports_trigger_orders=True,
            supports_builder_fee=True,
            supports_cross_margin=False,
        )

    # --- Market Data ----------------------------------------------------

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        _parse_pair(instrument)
        raise NotImplementedError(
            "Pear /markets does not expose mid/bid/ask per pair — "
            "snapshot requires deriving from leg prices on HL"
        )

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        _parse_pair(coin)
        raise NotImplementedError("Pear does not expose a candles endpoint")

    def get_all_markets(self) -> list:
        resp = self._http.request("GET", "/markets", headers=self._auth_headers())
        return resp.get("markets", resp if isinstance(resp, list) else [])

    def get_all_mids(self) -> Dict[str, str]:
        raise NotImplementedError("Pear does not expose a mids endpoint")

    # --- Execution ------------------------------------------------------

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
    ) -> Optional[Fill]:
        if tif.lower() not in ("ioc", "market"):
            raise NotImplementedError(
                f"Pear basket orders are MARKET-only via place_order; "
                f"tif={tif!r} not supported. Use place_trigger_order for "
                f"conditional entries."
            )
        usd_value = float(size) * float(price)
        if usd_value < 1.0:
            raise ValueError(
                f"usdValue {usd_value:.4f} below Pear minimum of 1 "
                f"(size={size}, price={price})"
            )
        long_assets, short_assets = _legs_for_side(instrument, side)
        leg_a, _ = _parse_pair(instrument)
        body = {
            "executionType": "MARKET",
            "usdValue": round(usd_value, 4),
            "leverage": self._leverage_by_leg.get(leg_a, self._defaults.leverage),
            "slippage": self._defaults.slippage,
            "longAssets": long_assets,
            "shortAssets": short_assets,
        }
        resp = self._http.request(
            "POST", "/positions", json=body, headers=self._auth_headers()
        )
        return _position_response_to_fill(resp, instrument, side)

    def cancel_order(self, instrument: str, oid: str) -> bool:
        if instrument:
            _parse_pair(instrument)
        try:
            resp = self._http.request(
                "DELETE", f"/orders/{oid}/cancel", headers=self._auth_headers()
            )
        except requests.HTTPError as e:
            log.warning("cancel_order(%s) failed: %s", oid, e)
            return False
        return str(resp.get("status", "")).upper() == "CANCELLED"

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        orders = self._http.request("GET", "/orders/open", headers=self._auth_headers())
        if not isinstance(orders, list):
            orders = orders.get("data", [])
        if not instrument:
            return orders
        _parse_pair(instrument)
        leg_a, leg_b = _parse_pair(instrument)
        out: List[Dict] = []
        for o in orders:
            longs = {a.get("asset") for a in o.get("longAssets", [])}
            shorts = {a.get("asset") for a in o.get("shortAssets", [])}
            if longs == {leg_a} and shorts == {leg_b}:
                out.append(o)
            elif longs == {leg_b} and shorts == {leg_a}:
                out.append(o)
        return out

    # --- Account --------------------------------------------------------

    def get_account_state(self) -> Dict:
        return self._http.request("GET", "/accounts", headers=self._auth_headers())

    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> None:
        """Stage leverage for the next pair order whose long leg matches `coin`.

        Pear leverage is set per-position; there's no global "per coin" setting.
        We stash it here so the next `place_order` whose long leg == coin picks
        it up. `is_cross` is accepted for interface parity but ignored — Pear's
        margin mode isn't exposed in the documented surface.
        """
        if leverage < 1 or leverage > 100:
            raise ValueError(f"leverage {leverage} outside Pear range 1-100")
        self._leverage_by_leg[coin] = int(leverage)

    # --- Trigger orders -------------------------------------------------

    def place_trigger_order(
        self,
        instrument: str,
        side: str,
        size: float,
        trigger_price: float,
        builder: Optional[dict] = None,
    ) -> Optional[str]:
        usd_value = float(size) * float(trigger_price)
        if usd_value < 1.0:
            raise ValueError(f"usdValue {usd_value:.4f} below Pear minimum of 1")
        long_assets, short_assets = _legs_for_side(instrument, side)
        leg_a, _ = _parse_pair(instrument)
        body = {
            "executionType": "TRIGGER",
            "usdValue": round(usd_value, 4),
            "leverage": self._leverage_by_leg.get(leg_a, self._defaults.leverage),
            "slippage": self._defaults.slippage,
            "longAssets": long_assets,
            "shortAssets": short_assets,
            "triggerType": "PRICE",
            "triggerValue": float(trigger_price),
            "direction": "MORE_THAN" if side.lower() == "buy" else "LESS_THAN",
        }
        resp = self._http.request(
            "POST", "/positions", json=body, headers=self._auth_headers()
        )
        oid = resp.get("orderId")
        return str(oid) if oid else None

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        return self.cancel_order(instrument, oid)

    # --- internals ------------------------------------------------------

    def _auth_headers(self) -> Dict[str, str]:
        if self._auth is None:
            raise RuntimeError(
                "PearVenueAdapter is unauthenticated — call connect(private_key) "
                "or construct with an authenticated PearAuth"
            )
        return self._auth.headers()
