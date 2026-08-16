"""KuSignal Bot - KuCoin crypto signals backend.

Public KuCoin REST API is used for candles and pair lists. No authenticated
endpoints or order execution in this MVP.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("kusignal")

# ---------------------------------------------------------------------------
# KuCoin constants
# ---------------------------------------------------------------------------
KUCOIN_BASE = "https://api.kucoin.com"
TF_MAP = {
    "15m": "15min",
    "1h": "1hour",
    "4h": "4hour",
    "1d": "1day",
}
TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
DEFAULT_TIMEFRAMES = ["1h", "4h"]
CANDLE_LIMIT = 200  # candles fetched per pair/tf

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class Config(BaseModel):
    scan_interval_minutes: int = 5
    timeframes: list[str] = Field(default_factory=lambda: DEFAULT_TIMEFRAMES.copy())
    quote_filter: str = "USDT"  # only pairs quoted in this asset
    min_24h_volume_usdt: float = 500_000.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    pivot_window: int = 5
    ema_fast: int = 20
    ema_slow: int = 50
    volume_ma_period: int = 20
    volume_spike_multiplier: float = 1.5
    require_volume_confirmation: bool = False
    require_ma_alignment: bool = False
    rr_ratio: float = 2.0
    sl_padding_pct: float = 0.3  # min % buffer beyond FVG edge (fallback)
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5  # SL buffer = max(atr_mult*ATR, sl_padding_pct)
    min_rr_ratio: float = 1.5  # reject setups below this estimated R:R
    premature_lookahead: int = 20  # candles to check if target would've been hit
    # --- Scoring system (replaces rigid AND logic) ---
    score_fvg: float = 2.0
    score_rsi_divergence: float = 2.0
    score_volume: float = 1.0
    score_ma_cross: float = 1.0
    score_fvg_reversal: float = 2.0  # NEW: confirmed reversal inside FVG zone
    min_score_threshold: float = 4.0
    signal_validity_candles: int = 5  # a condition counts if it happened within N bars
    fvg_lookback: int = 40  # how far back to look for an open FVG
    # --- FVG reversal / fill entry extension ---
    reversal_min_signals: int = 2  # min reversal sub-signals to confirm
    reversal_rejection_wick_ratio: float = 1.5  # wick/body ratio for rejection candle
    fvg_fill_mode: str = "opposite_edge"  # "opposite_edge" or "midpoint" (50% CE)
    max_pairs_per_scan: int = 200  # cap for MVP performance
    enabled_pairs: list[str] = Field(default_factory=list)  # empty = all matching filter
    excluded_pairs: list[str] = Field(default_factory=list)
    # --- Position management: timeout / breakeven / trailing ---
    timeout_15m: int = 14
    timeout_1h: int = 7
    timeout_4h: int = 5
    timeout_1d: int = 3
    timeout_min_r: float = 0.3  # move to BE if profit_in_R below this at timeout
    breakeven_safety_pct: float = 0.05  # % safety margin added to breakeven
    default_fee_rate: float = 0.001  # fallback maker/taker if API unavailable
    trailing_activation_r: float = 1.0  # activate trailing when profit_in_R >= this
    trailing_atr_mult: float = 1.2
    partial_close_enabled: bool = True
    partial_close_r: float = 1.0
    partial_close_pct: float = 35.0  # % of position closed at partial_close_r
    liq_min_distance_pct: float = 25.0  # leverage: min distance from liquidation (%)
    # --- Impulse-FVG + Consolidation strategy (additive, selectable) ---
    strategy_mode: str = "scoring"  # "scoring" | "impulse_fvg" | "counter_trend" | "both"
    impulse_atr_mult: float = 1.5  # impulse candle range >= this * ATR
    consolidation_min_candles: int = 3
    consolidation_max_atr: float = 1.5  # channel width <= this * ATR
    tp1_pct: float = 65.0  # % closed at TP1
    tp2_pct: float = 35.0  # % remainder to TP2 / trailing
    # --- Counter-trend strategy RSI filters ---
    rsi_high_tf_ob: float = 80.0  # higher-TF overbought (short)
    rsi_high_tf_os: float = 20.0  # higher-TF oversold (long)
    trailing_pct_from_entry: float = 1.0  # counter-trend trailing distance %


class Signal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    timeframe: str
    side: str  # "long" or "short"
    entry: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    confirmations: list[str]
    strength: int  # number of satisfied conditions
    score: float = 0.0  # weighted confluence score
    max_score: float = 0.0  # max achievable score with active weights
    reversal_signals: list[str] = Field(default_factory=list)  # FVG reversal contributors
    strategy: str = "scoring"  # "scoring" | "impulse_fvg"
    tp1: float = 0.0
    tp2: float = 0.0
    consolidation_high: float = 0.0
    consolidation_low: float = 0.0
    rsi_value: float
    volume_ratio: float
    created_at: str  # ISO string
    fvg_top: float
    fvg_bottom: float
    atr: float = 0.0  # ATR value at entry (same units as price)
    atr_multiplier: float = 0.0  # multiplier applied for the SL buffer
    status: str = "active"  # active | hit_tp | hit_sl | expired
    outcome: Optional[str] = None


class ScanState(BaseModel):
    last_scan_at: Optional[str] = None
    last_scan_duration_s: Optional[float] = None
    last_scanned_pairs: int = 0
    last_signals_found: int = 0
    is_scanning: bool = False


# ---------------------------------------------------------------------------
# Paper trading models
# ---------------------------------------------------------------------------
class PaperConfig(BaseModel):
    initial_capital: float = 10000.0
    risk_per_trade_pct: float = 1.0  # % of equity risked per position
    auto_execute: bool = False
    max_open_positions: int = 5
    trading_mode: str = "spot"  # "spot" or "leverage"
    max_position_size_usdt: float = 10.0  # HARD cap per single trade
    one_position_per_pair: bool = True


class PaperPosition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    symbol: str
    timeframe: str
    side: str  # long | short
    entry: float  # signal entry (planned)
    fill_price: float = 0.0  # actual execution price
    slippage_usdt: float = 0.0
    slippage_pct: float = 0.0
    stop_loss: float
    take_profit: float
    quantity: float
    risk_usdt: float
    opened_at: str
    # --- Position management (timeout / breakeven / trailing) ---
    current_stop: float = 0.0  # active stop (0 = use stop_loss)
    initial_risk: float = 0.0  # |entry - stop_loss| at open
    breakeven_active: bool = False
    trailing_active: bool = False
    partial_closed: bool = False
    last_trail_candle_t: float = 0.0  # last candle time used to recompute trailing
    liquidation_price: float = 0.0  # leverage only (0 = unknown/spot)
    strategy: str = "scoring"
    tp1: float = 0.0
    tp2: float = 0.0


class PaperTrade(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    symbol: str
    side: str
    entry: float
    exit: float
    quantity: float
    pnl_usdt: float
    pnl_pct: float
    outcome: str  # win | loss
    opened_at: str
    closed_at: str


class ExchangeConnectRequest(BaseModel):
    api_key: str
    api_secret: str
    api_passphrase: str


# ---------------------------------------------------------------------------
# Encryption for exchange credentials (at-rest)
# ---------------------------------------------------------------------------
KEY_PATH = ROOT_DIR / ".fernet_key"


def _load_or_create_fernet() -> Fernet:
    if KEY_PATH.exists():
        return Fernet(KEY_PATH.read_bytes())
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return Fernet(key)


fernet = _load_or_create_fernet()


def encrypt_str(v: str) -> str:
    return fernet.encrypt(v.encode()).decode()


def decrypt_str(v: str) -> str:
    return fernet.decrypt(v.encode()).decode()


# ---------------------------------------------------------------------------
# Technical analysis helpers (numpy free — pure python for MVP simplicity)
# ---------------------------------------------------------------------------
def rsi_wilder(closes: list[float], period: int = 14) -> list[Optional[float]]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis: list[Optional[float]] = [None] * (period)
    if avg_loss == 0:
        rsis.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsis.append(100.0 - 100.0 / (1.0 + rs))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100.0 - 100.0 / (1.0 + rs))
    return rsis


def ema(values: list[float], period: int) -> list[Optional[float]]:
    if len(values) < period:
        return [None] * len(values)
    k = 2.0 / (period + 1)
    out: list[Optional[float]] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    out.append(seed)
    prev = seed
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def detect_pivots(series: list[float], window: int = 5) -> tuple[list[int], list[int]]:
    """Return (lows, highs) indices where index is a local pivot."""
    lows: list[int] = []
    highs: list[int] = []
    for i in range(window, len(series) - window):
        seg = series[i - window : i + window + 1]
        v = series[i]
        if v == min(seg) and seg.count(v) == 1:
            lows.append(i)
        if v == max(seg) and seg.count(v) == 1:
            highs.append(i)
    return lows, highs


def detect_rsi_divergence(
    closes: list[float], rsis: list[Optional[float]], window: int = 5
) -> Optional[str]:
    """Return 'bullish', 'bearish' or None based on last 2 relevant pivots."""
    # Bullish: lower low in price, higher low in RSI
    # Bearish: higher high in price, lower high in RSI
    lows, highs = detect_pivots(closes, window)
    # keep recent pivots only within lookback ~50 bars
    lookback = 50
    n = len(closes)
    lows = [i for i in lows if i >= n - lookback and rsis[i] is not None]
    highs = [i for i in highs if i >= n - lookback and rsis[i] is not None]

    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if closes[i2] < closes[i1] and (rsis[i2] or 0) > (rsis[i1] or 0):
            return "bullish"
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if closes[i2] > closes[i1] and (rsis[i2] or 0) < (rsis[i1] or 0):
            return "bearish"
    return None


def detect_fvg(
    highs: list[float], lows: list[float], lookback: int = 40
) -> Optional[dict[str, Any]]:
    """Return the most recent still-open FVG in the last `lookback` bars.

    Bullish FVG: high of candle[i-2] < low of candle[i]. Gap zone = [high[i-2], low[i]].
    Bearish FVG: low of candle[i-2] > high of candle[i]. Gap zone = [high[i], low[i-2]].
    A zone is still open if price hasn't fully traversed it after formation.
    """
    n = len(highs)
    start = max(2, n - lookback)
    open_fvgs: list[dict[str, Any]] = []
    for i in range(start, n):
        # Bullish
        if highs[i - 2] < lows[i]:
            top, bottom = lows[i], highs[i - 2]
            filled = False
            for j in range(i + 1, n):
                if lows[j] <= bottom:
                    filled = True
                    break
            if not filled:
                open_fvgs.append(
                    {"kind": "bullish", "top": top, "bottom": bottom, "index": i}
                )
        # Bearish
        if lows[i - 2] > highs[i]:
            top, bottom = lows[i - 2], highs[i]
            filled = False
            for j in range(i + 1, n):
                if highs[j] >= top:
                    filled = True
                    break
            if not filled:
                open_fvgs.append(
                    {"kind": "bearish", "top": top, "bottom": bottom, "index": i}
                )
    if not open_fvgs:
        return None
    return open_fvgs[-1]


def volume_spike_ratio(volumes: list[float], period: int = 20) -> float:
    if len(volumes) < period + 1:
        return 1.0
    avg = sum(volumes[-period - 1 : -1]) / period
    if avg == 0:
        return 1.0
    return volumes[-1] / avg


def atr_wilder(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> Optional[float]:
    """Average True Range (Wilder smoothing). Returns latest ATR value."""
    n = len(closes)
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def detect_fvg_reversal(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rsis: list[Optional[float]],
    fvg: dict[str, Any],
    side: str,
    cfg: Config,
) -> dict[str, Any]:
    """Detect a confirmed reversal INSIDE the FVG zone (price rejecting the
    zone edge to go fill it), reusing existing RSI-divergence logic.

    Returns {"confirmed": bool, "signals": [names]}.
    'side' == 'long' means a bullish FVG acting as support (expected bounce up);
    'short' means a bearish FVG acting as resistance (expected drop down).
    """
    signals: list[str] = []
    n = len(closes)
    if n < 8:
        return {"confirmed": False, "signals": signals}

    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    if body <= 0:
        body = (h - l) * 0.25 or 1e-9
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    ratio = cfg.reversal_rejection_wick_ratio

    # 1) Rejection candle: long wick in the fill direction.
    if side == "long":
        if lower_wick >= ratio * body and lower_wick > upper_wick:
            signals.append("Rejection Candle")
    else:
        if upper_wick >= ratio * body and upper_wick > lower_wick:
            signals.append("Rejection Candle")

    # 2) Volume spike on the rejection candle.
    if volume_spike_ratio(volumes, cfg.volume_ma_period) >= cfg.volume_spike_multiplier:
        signals.append("Reversal Volume")

    # 3) Change of character: break of the local mini swing on the last bars.
    window = max(3, cfg.pivot_window)
    prior = slice(-(window + 1), -1)
    if side == "long":
        local_high = max(highs[prior]) if highs[prior] else h
        if c > local_high:
            signals.append("Change of Character")
    else:
        local_low = min(lows[prior]) if lows[prior] else l
        if c < local_low:
            signals.append("Change of Character")

    # 4) RSI divergence in the FVG direction (reuse existing detector).
    divergence = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if (divergence == "bullish" and side == "long") or (
        divergence == "bearish" and side == "short"
    ):
        signals.append("RSI Divergence (FVG)")

    confirmed = len(signals) >= cfg.reversal_min_signals
    return {"confirmed": confirmed, "signals": signals}




# ---------------------------------------------------------------------------
# KuCoin client
# ---------------------------------------------------------------------------
class KuCoinClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=KUCOIN_BASE, timeout=15.0)
        self._sema = asyncio.Semaphore(15)  # respect rate limits

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        async with self._sema:
            for attempt in range(3):
                try:
                    r = await self._client.get(path, params=params)
                    if r.status_code == 429:
                        await asyncio.sleep(1.0 + attempt)
                        continue
                    r.raise_for_status()
                    return r.json()
                except (httpx.HTTPError, httpx.ReadTimeout) as e:
                    if attempt == 2:
                        logger.warning("KuCoin GET failed %s: %s", path, e)
                        return None
                    await asyncio.sleep(0.5)
            return None

    async def get_symbols(self) -> list[dict[str, Any]]:
        data = await self._get("/api/v2/symbols")
        if not data or data.get("code") != "200000":
            return []
        return data.get("data", [])

    async def get_tickers(self) -> list[dict[str, Any]]:
        data = await self._get("/api/v1/market/allTickers")
        if not data or data.get("code") != "200000":
            return []
        return data.get("data", {}).get("ticker", [])

    async def get_klines(self, symbol: str, tf: str) -> list[list[float]]:
        kucoin_tf = TF_MAP.get(tf)
        if not kucoin_tf:
            return []
        data = await self._get(
            "/api/v1/market/candles",
            params={"symbol": symbol, "type": kucoin_tf},
        )
        if not data or data.get("code") != "200000":
            return []
        # KuCoin returns [time, open, close, high, low, volume, turnover] descending
        raw = data.get("data", [])
        raw.reverse()
        # BUGFIX (RSI misalignment): drop the last, still-forming candle so that
        # BOTH signal logic and chart rendering reference the same CLOSED candles
        # using the same rsi_wilder function.
        if len(raw) > 1:
            raw = raw[:-1]
        candles = []
        for row in raw[-CANDLE_LIMIT:]:
            try:
                candles.append(
                    [
                        float(row[0]),  # time
                        float(row[1]),  # open
                        float(row[2]),  # close
                        float(row[3]),  # high
                        float(row[4]),  # low
                        float(row[5]),  # volume
                    ]
                )
            except (ValueError, IndexError):
                continue
        return candles


kucoin = KuCoinClient()


# ---------------------------------------------------------------------------
# Real-time price feed via KuCoin public WebSocket
# ---------------------------------------------------------------------------
class PriceFeed:
    """Maintains a live cache of last-trade prices via KuCoin WS.

    Falls back to REST if the socket drops. Subscribes to the symbols of
    currently open paper positions so SL/TP can trigger with minimal delay.
    """

    def __init__(self) -> None:
        self.prices: dict[str, float] = {}
        self.updated_at: dict[str, float] = {}
        self._ws: Optional[Any] = None
        self._subscribed: set[str] = set()
        self._connected = False
        self._lock = asyncio.Lock()

    def get(self, symbol: str) -> Optional[float]:
        return self.prices.get(symbol)

    async def _get_token(self) -> Optional[tuple[str, str]]:
        try:
            async with httpx.AsyncClient(base_url=KUCOIN_BASE, timeout=10.0) as c:
                r = await c.post("/api/v1/bullet-public")
                data = r.json()
                if data.get("code") != "200000":
                    return None
                token = data["data"]["token"]
                endpoint = data["data"]["instanceServers"][0]["endpoint"]
                return token, endpoint
        except (httpx.HTTPError, KeyError, IndexError) as e:
            logger.warning("WS token fetch failed: %s", e)
            return None

    async def desired_symbols(self) -> list[str]:
        docs = await db.paper_positions.find({}, {"symbol": 1, "_id": 0}).to_list(1000)
        return sorted({d["symbol"] for d in docs})

    async def run(self) -> None:
        import websockets  # local import, dep added

        while True:
            tok = await self._get_token()
            if not tok:
                await asyncio.sleep(5)
                continue
            token, endpoint = tok
            url = f"{endpoint}?token={token}"
            try:
                async with websockets.connect(url, ping_interval=None) as ws:
                    self._ws = ws
                    self._connected = True
                    self._subscribed.clear()
                    logger.info("KuCoin WS connected")
                    # background ping every 15s
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    sub_task = asyncio.create_task(self._resub_loop(ws))
                    try:
                        async for raw in ws:
                            self._handle(raw)
                    finally:
                        ping_task.cancel()
                        sub_task.cancel()
            except Exception as e:  # noqa: BLE001
                logger.warning("KuCoin WS error, reconnecting: %s", e)
            self._connected = False
            self._ws = None
            await asyncio.sleep(3)

    async def _ping_loop(self, ws: Any) -> None:
        import json as _json
        while True:
            await asyncio.sleep(15)
            try:
                await ws.send(_json.dumps({"id": str(int(time.time() * 1000)), "type": "ping"}))
            except Exception:
                return

    async def _resub_loop(self, ws: Any) -> None:
        """Keep subscriptions in sync with open positions."""
        import json as _json
        while True:
            try:
                want = set(await self.desired_symbols())
                to_add = want - self._subscribed
                to_remove = self._subscribed - want
                for sym in to_add:
                    await ws.send(_json.dumps({
                        "id": str(int(time.time() * 1000)),
                        "type": "subscribe",
                        "topic": f"/market/ticker:{sym}",
                        "response": True,
                    }))
                    self._subscribed.add(sym)
                for sym in to_remove:
                    await ws.send(_json.dumps({
                        "id": str(int(time.time() * 1000)),
                        "type": "unsubscribe",
                        "topic": f"/market/ticker:{sym}",
                        "response": True,
                    }))
                    self._subscribed.discard(sym)
            except Exception:
                return
            await asyncio.sleep(3)

    def _handle(self, raw: str) -> None:
        import json as _json
        try:
            msg = _json.loads(raw)
        except (ValueError, TypeError):
            return
        if msg.get("type") != "message":
            return
        topic = msg.get("topic", "")
        if not topic.startswith("/market/ticker:"):
            return
        symbol = topic.split(":", 1)[1]
        data = msg.get("data", {})
        price = data.get("price") or data.get("bestBid")
        try:
            self.prices[symbol] = float(price)
            self.updated_at[symbol] = time.time()
        except (TypeError, ValueError):
            pass

    async def price_or_rest(self, symbol: str) -> Optional[float]:
        """Return live WS price if fresh (<10s), else REST fallback."""
        p = self.prices.get(symbol)
        ts = self.updated_at.get(symbol, 0)
        if p and (time.time() - ts) < 10:
            return p
        # REST fallback
        try:
            async with httpx.AsyncClient(base_url=KUCOIN_BASE, timeout=8.0) as c:
                r = await c.get("/api/v1/market/orderbook/level1", params={"symbol": symbol})
                d = r.json()
                if d.get("code") == "200000" and d.get("data"):
                    return float(d["data"]["price"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            pass
        return None


price_feed = PriceFeed()



# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
CONFIG_ID = "singleton"


async def get_config() -> Config:
    doc = await db.config.find_one({"_id": CONFIG_ID}, {"_id": 0})
    if not doc:
        cfg = Config()
        await db.config.update_one(
            {"_id": CONFIG_ID}, {"$set": cfg.model_dump()}, upsert=True
        )
        return cfg
    return Config(**doc)


async def save_config(cfg: Config) -> Config:
    await db.config.update_one(
        {"_id": CONFIG_ID}, {"$set": cfg.model_dump()}, upsert=True
    )
    return cfg


# ---------------------------------------------------------------------------
# Paper trading helpers
# ---------------------------------------------------------------------------
PAPER_CFG_ID = "singleton"
PAPER_STATE_ID = "singleton"


async def get_paper_config() -> PaperConfig:
    doc = await db.paper_config.find_one({"_id": PAPER_CFG_ID}, {"_id": 0})
    if not doc:
        cfg = PaperConfig()
        await db.paper_config.update_one(
            {"_id": PAPER_CFG_ID}, {"$set": cfg.model_dump()}, upsert=True
        )
        return cfg
    return PaperConfig(**doc)


async def save_paper_config(cfg: PaperConfig) -> PaperConfig:
    await db.paper_config.update_one(
        {"_id": PAPER_CFG_ID}, {"$set": cfg.model_dump()}, upsert=True
    )
    return cfg


async def get_paper_cash() -> float:
    """Return current cash (initial + realized PnL)."""
    doc = await db.paper_state.find_one({"_id": PAPER_STATE_ID}, {"_id": 0})
    if doc and "cash" in doc:
        return float(doc["cash"])
    cfg = await get_paper_config()
    await db.paper_state.update_one(
        {"_id": PAPER_STATE_ID},
        {"$set": {"cash": cfg.initial_capital}},
        upsert=True,
    )
    return cfg.initial_capital


async def set_paper_cash(cash: float) -> None:
    await db.paper_state.update_one(
        {"_id": PAPER_STATE_ID}, {"$set": {"cash": cash}}, upsert=True
    )


async def open_paper_position(signal: dict[str, Any]) -> Optional[PaperPosition]:
    pcfg = await get_paper_config()
    open_count = await db.paper_positions.count_documents({})
    if open_count >= pcfg.max_open_positions:
        return None
    # Prevent duplicates on same signal
    if await db.paper_positions.find_one({"signal_id": signal["id"]}):
        return None
    # SAFETY: never more than one position on the same pair at once
    if pcfg.one_position_per_pair and await db.paper_positions.find_one(
        {"symbol": signal["symbol"]}
    ):
        return None
    # Spot mode restrictions: no shorts (spot cannot short natively)
    if pcfg.trading_mode == "spot" and signal["side"] == "short":
        return None
    cash = await get_paper_cash()

    # IMMEDIATE EXECUTION: fill at the current live market price (WS or REST),
    # not the planned signal price — this is what produces real slippage.
    fill_price = await price_feed.price_or_rest(signal["symbol"])
    if not fill_price or fill_price <= 0:
        fill_price = signal["entry"]

    risk_usdt = max(1.0, cash * pcfg.risk_per_trade_pct / 100)
    risk_per_unit = abs(signal["entry"] - signal["stop_loss"])
    if risk_per_unit <= 0:
        return None
    qty = risk_usdt / risk_per_unit

    # SAFETY: hard cap the notional per single trade (e.g. 10 USDT)
    notional = qty * fill_price
    cap = pcfg.max_position_size_usdt
    if cap > 0 and notional > cap:
        qty = cap / fill_price
        notional = qty * fill_price
        risk_usdt = qty * risk_per_unit

    if pcfg.trading_mode == "spot":
        if notional > cash:
            qty = cash / fill_price
            notional = qty * fill_price
        if qty <= 0 or notional < 1.0:
            return None
        await set_paper_cash(cash - notional)  # lock cash on open

    # Slippage = actual fill vs planned signal entry
    slip_usdt = (fill_price - signal["entry"]) * qty
    slip_pct = ((fill_price - signal["entry"]) / signal["entry"]) * 100 if signal["entry"] else 0.0

    pos = PaperPosition(
        signal_id=signal["id"],
        symbol=signal["symbol"],
        timeframe=signal["timeframe"],
        side=signal["side"],
        entry=signal["entry"],
        fill_price=round(fill_price, 8),
        slippage_usdt=round(slip_usdt, 4),
        slippage_pct=round(slip_pct, 4),
        stop_loss=signal["stop_loss"],
        take_profit=signal["take_profit"],
        quantity=round(qty, 8),
        risk_usdt=round(risk_usdt, 2),
        opened_at=datetime.now(timezone.utc).isoformat(),
        current_stop=round(signal["stop_loss"], 8),
        initial_risk=round(abs(fill_price - signal["stop_loss"]), 8),
        strategy=signal.get("strategy", "scoring"),
        tp1=float(signal.get("tp1", 0.0)),
        tp2=float(signal.get("tp2", 0.0)),
    )
    await db.paper_positions.insert_one(pos.model_dump())
    # Persist slippage log entry
    await db.slippage_log.insert_one({
        "id": str(uuid.uuid4()),
        "position_id": pos.id,
        "signal_id": signal["id"],
        "symbol": signal["symbol"],
        "side": signal["side"],
        "signal_price": signal["entry"],
        "fill_price": round(fill_price, 8),
        "slippage_usdt": round(slip_usdt, 4),
        "slippage_pct": round(slip_pct, 4),
        "quantity": round(qty, 8),
        "source": "ws" if price_feed.get(signal["symbol"]) else "rest",
        "at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info(
        "Opened paper[%s] %s %s qty=%.6f fill=%.8f slip=%.4f%%",
        pcfg.trading_mode, pos.symbol, pos.side, pos.quantity, fill_price, slip_pct,
    )
    return pos


async def close_paper_position(pos: dict[str, Any], exit_price: float, outcome: str) -> PaperTrade:
    entry = float(pos.get("fill_price") or pos["entry"])  # real fill for PnL
    qty = float(pos["quantity"])
    pcfg = await get_paper_config()
    if pos["side"] == "long":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    pnl_pct = (pnl / (entry * qty)) * 100 if entry > 0 and qty > 0 else 0.0
    trade = PaperTrade(
        signal_id=pos["signal_id"],
        symbol=pos["symbol"],
        side=pos["side"],
        entry=entry,
        exit=round(exit_price, 8),
        quantity=qty,
        pnl_usdt=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        outcome=outcome,
        opened_at=pos["opened_at"],
        closed_at=datetime.now(timezone.utc).isoformat(),
    )
    await db.paper_trades.insert_one(trade.model_dump())
    await db.paper_positions.delete_one({"id": pos["id"]})
    cash = await get_paper_cash()
    if pcfg.trading_mode == "spot" and pos["side"] == "long":
        # Unlock notional and add PnL: cash += exit * qty
        await set_paper_cash(cash + exit_price * qty)
    else:
        await set_paper_cash(cash + pnl)
    await db.signals.update_one(
        {"id": pos["signal_id"]},
        {"$set": {"outcome": outcome, "status": "closed"}},
    )
    # DEBUG: on stop-loss, record data for premature-stop analysis. A background
    # checker will later look ahead N candles to see if the ORIGINAL target
    # would have been reached with a wider stop.
    if outcome == "loss":
        sig = await db.signals.find_one({"id": pos["signal_id"]}, {"_id": 0}) or {}
        await db.stop_debug_log.insert_one({
            "id": str(uuid.uuid4()),
            "signal_id": pos["signal_id"],
            "symbol": pos["symbol"],
            "timeframe": pos.get("timeframe") or sig.get("timeframe", "1h"),
            "side": pos["side"],
            "entry": entry,
            "stop_loss": float(pos["stop_loss"]),
            "take_profit": float(pos["take_profit"]),
            "atr_at_entry": float(sig.get("atr", 0.0)),
            "atr_multiplier": float(sig.get("atr_multiplier", 0.0)),
            "stop_distance": round(abs(entry - float(pos["stop_loss"])), 8),
            "stop_distance_in_atr": round(
                abs(entry - float(pos["stop_loss"])) / sig["atr"], 3
            ) if sig.get("atr") else None,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "premature_status": "pending",  # pending | premature | valid
            "would_hit_target": None,
            "candles_to_target": None,
        })
    logger.info(
        "Closed paper %s %s pnl=%.2f (%s)",
        pos["symbol"], pos["side"], pnl, outcome,
    )
    return trade


# ===========================================================================
# POSITION MANAGEMENT: Timeout + Breakeven + Trailing Stop (additive modules)
# ===========================================================================
_fee_cache: dict[str, tuple[float, float]] = {}
_funding_cache: dict[str, float] = {}


async def get_trade_fees(symbol: str, cfg: Config) -> tuple[float, float]:
    """Maker/taker fee rates via KuCoin (authenticated). Falls back to
    default_fee_rate with a warning if credentials/endpoint unavailable."""
    if symbol in _fee_cache:
        return _fee_cache[symbol]
    maker = taker = cfg.default_fee_rate
    try:
        r = await _kucoin_signed_get(f"/api/v1/trade-fees?symbols={symbol}")
        data = r.json()
        if data.get("code") == "200000" and data.get("data"):
            d = data["data"][0]
            maker = float(d.get("makerFeeRate", maker))
            taker = float(d.get("takerFeeRate", taker))
        else:
            logger.warning("Fee API for %s returned %s; using default", symbol, data.get("code"))
    except Exception as e:  # noqa: BLE001
        logger.warning("Fee API unavailable for %s (%s); using default fee", symbol, e)
    _fee_cache[symbol] = (maker, taker)
    return maker, taker


async def get_funding_rate(symbol: str, cfg: Config) -> float:
    """Current funding rate via KuCoin Futures public API. Falls back to last
    known value (or 0) with a warning; never blocks execution."""
    contract = symbol.replace("-", "")  # e.g. BTC-USDT -> BTCUSDT (best effort)
    try:
        async with httpx.AsyncClient(base_url="https://api-futures.kucoin.com", timeout=8.0) as c:
            r = await c.get(f"/api/v1/funding-rate/{contract}/current")
            data = r.json()
            if data.get("code") == "200000" and data.get("data"):
                val = float(data["data"].get("value", 0.0))
                _funding_cache[symbol] = val
                return val
    except Exception as e:  # noqa: BLE001
        logger.warning("Funding rate unavailable for %s (%s); using last known", symbol, e)
    return _funding_cache.get(symbol, 0.0)


async def _current_spread(symbol: str) -> float:
    """Approx average bid-ask spread from KuCoin level1 (proxy for last-20-tick avg)."""
    try:
        async with httpx.AsyncClient(base_url=KUCOIN_BASE, timeout=8.0) as c:
            r = await c.get("/api/v1/market/orderbook/level1", params={"symbol": symbol})
            d = r.json()
            if d.get("code") == "200000" and d.get("data"):
                bid = float(d["data"].get("bestBid") or 0)
                ask = float(d["data"].get("bestAsk") or 0)
                if bid > 0 and ask > 0:
                    return max(0.0, ask - bid)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


async def compute_breakeven(pos: dict[str, Any], cfg: Config, trading_mode: str) -> float:
    """BreakevenCalculator (spot & leverage). Returns the breakeven price."""
    entry = float(pos.get("fill_price") or pos["entry"])
    symbol = pos["symbol"]
    maker, taker = await get_trade_fees(symbol, cfg)
    spread = await _current_spread(symbol)
    fee_cost = entry * (maker + taker)
    safety = entry * (cfg.breakeven_safety_pct / 100)
    cost = fee_cost + spread + safety
    if trading_mode == "leverage":
        funding_rate = await get_funding_rate(symbol, cfg)
        opened = datetime.fromisoformat(pos["opened_at"]).timestamp()
        hours_open = max(0.0, (time.time() - opened) / 3600)
        funding_accumulato = entry * funding_rate * (hours_open / 8)
        cost += funding_accumulato
    if pos["side"] == "long":
        return entry + cost
    return entry - cost


def _profit_in_r(pos: dict[str, Any], price: float) -> float:
    entry = float(pos.get("fill_price") or pos["entry"])
    risk = float(pos.get("initial_risk") or abs(entry - float(pos["stop_loss"])))
    if risk <= 0:
        return 0.0
    if pos["side"] == "long":
        return (price - entry) / risk
    return (entry - price) / risk


def _timeout_candles(tf: str, cfg: Config) -> int:
    return {
        "15m": cfg.timeout_15m,
        "1h": cfg.timeout_1h,
        "4h": cfg.timeout_4h,
        "1d": cfg.timeout_1d,
    }.get(tf, cfg.timeout_1h)


async def apply_timeout_manager(pos: dict[str, Any], price: float, cfg: Config) -> Optional[float]:
    """TimeoutManager: if enough candles elapsed with profit < timeout_min_r,
    move stop to breakeven. Returns the new stop or None."""
    if pos.get("breakeven_active"):
        return None
    tf = pos.get("timeframe", "1h")
    tf_sec = TF_SECONDS.get(tf, 3600)
    opened = datetime.fromisoformat(pos["opened_at"]).timestamp()
    candles_elapsed = (time.time() - opened) / tf_sec
    if candles_elapsed < _timeout_candles(tf, cfg):
        return None
    if _profit_in_r(pos, price) >= cfg.timeout_min_r:
        return None
    pcfg = await get_paper_config()
    be = await compute_breakeven(pos, cfg, pcfg.trading_mode)
    return be


def _recent_swing(candles: list[list[float]], side: str, window: int) -> Optional[float]:
    """Reuse pivot logic to get the last significant swing low (long) / high (short)."""
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    low_idx, high_idx = detect_pivots(lows if side == "long" else highs, window)
    if side == "long":
        pivots = low_idx
        series = lows
    else:
        pivots = high_idx
        series = highs
    if not pivots:
        return None
    return series[pivots[-1]]


async def apply_trailing_manager(pos: dict[str, Any], price: float, cfg: Config) -> Optional[float]:
    """TrailingStopManager: activate at trailing_activation_r; recompute only on
    a NEW candle close of the trade timeframe; never move against the position.
    Leverage-only liquidation distance clamp when liquidation_price known."""
    if _profit_in_r(pos, price) < cfg.trailing_activation_r:
        return None
    tf = pos.get("timeframe", "1h")
    tf_sec = TF_SECONDS.get(tf, 3600)
    current_candle = (time.time() // tf_sec) * tf_sec
    if current_candle <= float(pos.get("last_trail_candle_t") or 0):
        return None  # only recompute on new candle close
    candles = await kucoin.get_klines(pos["symbol"], tf)
    if len(candles) < 20:
        return None
    ref = _recent_swing(candles, pos["side"], cfg.pivot_window)
    if ref is None:
        return None
    atr = atr_wilder([c[3] for c in candles], [c[4] for c in candles],
                     [c[2] for c in candles], cfg.atr_period)
    if not atr or atr <= 0:
        return None
    buffer = atr * cfg.trailing_atr_mult
    if pos["side"] == "long":
        trailing = ref - buffer
    else:
        trailing = ref + buffer

    # Leverage-only: keep the stop at least liq_min_distance_pct away from liquidation
    liq = float(pos.get("liquidation_price") or 0)
    if liq > 0:
        min_dist = liq * (cfg.liq_min_distance_pct / 100)
        if pos["side"] == "long" and trailing < liq + min_dist:
            trailing = liq + min_dist
        elif pos["side"] == "short" and trailing > liq - min_dist:
            trailing = liq - min_dist

    # Never move against the position
    cur = float(pos.get("current_stop") or pos["stop_loss"])
    if pos["side"] == "long":
        new_stop = max(cur, trailing)
    else:
        new_stop = min(cur, trailing)
    await db.paper_positions.update_one(
        {"id": pos["id"]}, {"$set": {"last_trail_candle_t": current_candle}}
    )
    if new_stop != cur:
        return new_stop
    return None


async def _close_fraction(pos: dict[str, Any], price: float, frac: float,
                          outcome: str, set_partial: bool = True) -> float:
    """Close `frac` of the position at `price`, log a trade, settle cash.
    Returns the remaining quantity."""
    close_qty = float(pos["quantity"]) * frac
    remain_qty = float(pos["quantity"]) - close_qty
    if close_qty <= 0:
        return float(pos["quantity"])
    entry = float(pos.get("fill_price") or pos["entry"])
    pnl = (price - entry) * close_qty if pos["side"] == "long" else (entry - price) * close_qty
    pnl_pct = (pnl / (entry * close_qty)) * 100 if entry > 0 else 0.0
    pcfg = await get_paper_config()
    trade = PaperTrade(
        signal_id=pos["signal_id"], symbol=pos["symbol"], side=pos["side"],
        entry=entry, exit=round(price, 8), quantity=round(close_qty, 8),
        pnl_usdt=round(pnl, 2), pnl_pct=round(pnl_pct, 2), outcome=outcome,
        opened_at=pos["opened_at"], closed_at=datetime.now(timezone.utc).isoformat(),
    )
    await db.paper_trades.insert_one(trade.model_dump())
    cash = await get_paper_cash()
    if pcfg.trading_mode == "spot" and pos["side"] == "long":
        await set_paper_cash(cash + price * close_qty)
    else:
        await set_paper_cash(cash + pnl)
    upd: dict[str, Any] = {"quantity": round(remain_qty, 8)}
    if set_partial:
        upd["partial_closed"] = True
    await db.paper_positions.update_one({"id": pos["id"]}, {"$set": upd})
    logger.info("Fraction close %s %.0f%% qty=%.6f pnl=%.2f (%s)",
                pos["symbol"], frac * 100, close_qty, pnl, outcome)
    return remain_qty


async def maybe_partial_close(pos: dict[str, Any], price: float, cfg: Config) -> None:
    """Close partial_close_pct of the position once profit reaches partial_close_r."""
    if not cfg.partial_close_enabled or pos.get("partial_closed"):
        return
    if _profit_in_r(pos, price) < cfg.partial_close_r:
        return
    await _close_fraction(pos, price, cfg.partial_close_pct / 100, "partial")


async def _last_closed_close(symbol: str, tf: str) -> Optional[float]:
    """Close of the last FULLY closed candle (index -2) for close-confirmation."""
    candles = await kucoin.get_klines(symbol, tf)
    if len(candles) < 2:
        return None
    return candles[-2][2]


async def manage_impulse_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """Impulse strategy execution: TP1 (close tp1_pct on candle-close beyond TP1,
    then move remainder to breakeven), TP2 for the rest, trailing fallback if
    no TP2. Uses close-confirmation (not wick)."""
    tf = pos.get("timeframe", "1h")
    tp1 = float(pos.get("tp1") or 0)
    tp2 = float(pos.get("tp2") or 0)
    long = pos["side"] == "long"
    last_close = await _last_closed_close(pos["symbol"], tf)
    if last_close is None:
        return pos

    if not pos.get("partial_closed"):
        hit_tp1 = last_close >= tp1 if long else last_close <= tp1
        if tp1 > 0 and hit_tp1:
            remain = await _close_fraction(pos, tp1, cfg.tp1_pct / 100, "tp1")
            pcfg = await get_paper_config()
            be = await compute_breakeven(pos, cfg, pcfg.trading_mode)
            await db.paper_positions.update_one(
                {"id": pos["id"]},
                {"$set": {"current_stop": round(be, 8), "breakeven_active": True,
                          "quantity": round(remain, 8)}},
            )
            pos = {**pos, "current_stop": be, "breakeven_active": True,
                   "partial_closed": True, "quantity": remain}
        return pos

    # After TP1: manage the remainder
    if tp2 > 0:
        hit_tp2 = last_close >= tp2 if long else last_close <= tp2
        if hit_tp2:
            await close_paper_position(pos, tp2, "win")
            return {**pos, "quantity": 0}
    else:
        trail = await apply_trailing_manager(pos, price, cfg)
        if trail is not None:
            await db.paper_positions.update_one(
                {"id": pos["id"]}, {"$set": {"current_stop": round(trail, 8), "trailing_active": True}}
            )
            pos = {**pos, "current_stop": trail, "trailing_active": True}
    return pos




async def manage_open_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """Run the 3 additive managers + partial close. Returns the possibly-updated
    position dict (with fresh current_stop)."""
    updates: dict[str, Any] = {}
    # Impulse strategy has its own TP1/TP2 execution path.
    if pos.get("strategy") == "impulse_fvg":
        return await manage_impulse_position(pos, price, cfg)
    # Partial close first (does not affect stop)
    await maybe_partial_close(pos, price, cfg)
    # Timeout -> breakeven
    be = await apply_timeout_manager(pos, price, cfg)
    if be is not None:
        cur = float(pos.get("current_stop") or pos["stop_loss"])
        # never move against position
        improved = be > cur if pos["side"] == "long" else be < cur
        if improved or cur == float(pos["stop_loss"]):
            updates["current_stop"] = be
            updates["breakeven_active"] = True
    # Trailing
    working = {**pos, **updates}
    trail = await apply_trailing_manager(working, price, cfg)
    if trail is not None:
        updates["current_stop"] = trail
        updates["trailing_active"] = True
    if updates:
        await db.paper_positions.update_one({"id": pos["id"]}, {"$set": updates})
        pos = {**pos, **updates}
    return pos



async def monitor_paper_positions() -> None:
    """Close positions if SL/TP hit, using real-time WS prices when available."""
    positions = await db.paper_positions.find({}, {"_id": 0}).to_list(1000)
    if not positions:
        return
    # Build price map: prefer live WS cache, fallback to REST tickers once
    rest_map: dict[str, float] = {}
    need_rest = any(price_feed.get(p["symbol"]) is None for p in positions)
    if need_rest:
        tickers = await kucoin.get_tickers()
        for t in tickers:
            try:
                rest_map[t["symbol"]] = float(t.get("last") or 0)
            except (TypeError, ValueError):
                continue
    cfg = await get_config()
    for pos in positions:
        price = price_feed.get(pos["symbol"]) or rest_map.get(pos["symbol"], 0.0)
        if price <= 0:
            continue
        # Additive position management (timeout/breakeven/trailing/partial).
        pos = await manage_open_position(pos, price, cfg)
        if float(pos.get("quantity") or 0) <= 0:
            continue  # position fully closed by the manager (e.g. TP2)
        active_stop = float(pos.get("current_stop") or pos["stop_loss"])
        # For impulse strategy the primary fixed target is TP2 (if any); the base
        # take_profit equals TP1 which the manager already handles on candle close.
        tp_check = pos["take_profit"]
        if pos.get("strategy") == "impulse_fvg":
            # Fixed TP2 close only AFTER TP1 partial is taken; before that the
            # manager handles TP1 on candle close. If no TP2, rely on trailing.
            if pos.get("partial_closed") and float(pos.get("tp2") or 0) > 0:
                tp_check = float(pos["tp2"])
            else:
                tp_check = 0
        if pos["side"] == "long":
            if price <= active_stop:
                await close_paper_position(pos, active_stop, "loss")
            elif tp_check and price >= tp_check:
                await close_paper_position(pos, tp_check, "win")
        else:
            if price >= active_stop:
                await close_paper_position(pos, active_stop, "loss")
            elif tp_check and price <= tp_check:
                await close_paper_position(pos, tp_check, "win")


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------
async def analyze_pair(symbol: str, tf: str, cfg: Config) -> Optional[Signal]:
    candles = await kucoin.get_klines(symbol, tf)
    if len(candles) < 60:
        return None

    closes = [c[2] for c in candles]
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    vols = [c[5] for c in candles]
    opens = [c[1] for c in candles]

    rsis = rsi_wilder(closes, cfg.rsi_period)

    # An FVG is required for the trade STRUCTURE (entry/SL levels). It does not
    # need to form on the current candle: detect_fvg looks back over fvg_lookback
    # bars and only returns zones that are still open (unmitigated).
    fvg = detect_fvg(highs, lows, lookback=cfg.fvg_lookback)
    if fvg is None:
        return None

    side = "long" if fvg["kind"] == "bullish" else "short"
    price = closes[-1]

    # ATR on the entry timeframe — basis for a volatility-aware stop.
    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if atr is None or atr <= 0:
        return None

    # SL buffer = max(atr_multiplier * ATR, sl_padding_pct % of price).
    atr_buffer = cfg.atr_sl_multiplier * atr
    pct_buffer = price * (cfg.sl_padding_pct / 100)
    sl_buffer = max(atr_buffer, pct_buffer)

    if side == "long":
        entry = fvg["top"]
        if price < fvg["bottom"] or price > entry * 1.05:
            return None
        stop_loss = fvg["bottom"] - sl_buffer
        risk = entry - stop_loss
        if risk <= 0:
            return None
        take_profit = entry + risk * cfg.rr_ratio
    else:
        entry = fvg["bottom"]
        if price > fvg["top"] or price < entry * 0.95:
            return None
        stop_loss = fvg["top"] + sl_buffer
        risk = stop_loss - entry
        if risk <= 0:
            return None
        take_profit = entry - risk * cfg.rr_ratio

    # Minimum estimated R:R gate — independent of the confluence score.
    reward = abs(take_profit - entry)
    est_rr = reward / risk if risk > 0 else 0
    if est_rr < cfg.min_rr_ratio:
        return None

    # ---------------- SCORING SYSTEM (replaces rigid AND) ----------------
    # Each satisfied condition adds its configurable weight. A trade opens only
    # if the total reaches min_score_threshold — no longer requiring ALL filters.
    score = 0.0
    passed: list[str] = []
    failed: list[str] = []
    breakdown: dict[str, dict[str, Any]] = {}

    def add(name: str, ok: bool, weight: float) -> None:
        nonlocal score
        breakdown[name] = {"passed": ok, "weight": weight}
        if ok:
            score += weight
            passed.append(name)
        else:
            failed.append(name)

    # FVG valid & unmitigated (aligned with side by construction)
    add("FVG Zone", True, cfg.score_fvg)

    # RSI divergence confirmed in the FVG direction, within the validity window
    divergence = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    div_ok = (divergence == "bullish" and side == "long") or (
        divergence == "bearish" and side == "short"
    )
    add("RSI Divergence", div_ok, cfg.score_rsi_divergence)

    # Volume above its moving average
    vol_ratio = volume_spike_ratio(vols, cfg.volume_ma_period)
    add("Volume Spike", vol_ratio >= cfg.volume_spike_multiplier, cfg.score_volume)

    # EMA cross aligned with trade direction
    ema_f = ema(closes, cfg.ema_fast)
    ema_s = ema(closes, cfg.ema_slow)
    ma_ok = False
    if ema_f[-1] is not None and ema_s[-1] is not None:
        ma_ok = (side == "long" and ema_f[-1] > ema_s[-1]) or (
            side == "short" and ema_f[-1] < ema_s[-1]
        )
    add("EMA Trend", ma_ok, cfg.score_ma_cross)

    # NEW scored condition: confirmed reversal INSIDE the FVG zone. Reuses the
    # existing RSI-divergence detector; contributes into THIS same score.
    reversal = detect_fvg_reversal(
        opens, highs, lows, closes, vols, rsis, fvg, side, cfg
    )
    add("FVG Reversal", reversal["confirmed"], cfg.score_fvg_reversal)

    max_score = (
        cfg.score_fvg
        + cfg.score_rsi_divergence
        + cfg.score_volume
        + cfg.score_ma_cross
        + cfg.score_fvg_reversal
    )

    if score < cfg.min_score_threshold:
        # DEBUG: record the discarded setup so the user can see which filter is
        # the real bottleneck blocking trades.
        try:
            await db.setup_debug_log.insert_one({
                "id": str(uuid.uuid4()),
                "symbol": symbol,
                "timeframe": tf,
                "side": side,
                "score": round(score, 2),
                "max_score": round(max_score, 2),
                "threshold": cfg.min_score_threshold,
                "passed": passed,
                "failed": failed,
                "breakdown": breakdown,
                "reversal_signals": reversal["signals"],
                "at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:  # noqa: BLE001
            pass
        return None

    # ---- FVG-fill target override (only when reversal is an ACTIVE condition) ----
    # The ATR/structural stop is left UNCHANGED. Only entry/target adapt to the
    # reversal-to-fill scenario, then R:R is re-validated with the same stop.
    reversal_signals: list[str] = reversal["signals"] if reversal["confirmed"] else []
    if reversal["confirmed"]:
        span = fvg["top"] - fvg["bottom"]
        midpoint = fvg["bottom"] + span * 0.5
        if side == "long":
            fill_target = fvg["top"] if cfg.fvg_fill_mode == "opposite_edge" else midpoint
            # Invalidation: price swept the FVG on the opposite side of the fill.
            if closes[-1] < fvg["bottom"]:
                return None
            # Target already reached or too close → discard.
            if price >= fill_target:
                return None
            entry = price  # enter at the rejection close, inside the zone
            stop_loss = fvg["bottom"] - sl_buffer  # UNCHANGED ATR/structural stop
            risk = entry - stop_loss
            take_profit = fill_target
        else:
            fill_target = fvg["bottom"] if cfg.fvg_fill_mode == "opposite_edge" else midpoint
            if closes[-1] > fvg["top"]:
                return None
            if price <= fill_target:
                return None
            entry = price
            stop_loss = fvg["top"] + sl_buffer  # UNCHANGED ATR/structural stop
            risk = stop_loss - entry
            take_profit = fill_target

        if risk <= 0:
            return None
        reward = abs(take_profit - entry)
        est_rr = reward / risk if risk > 0 else 0
        # Re-apply the SAME configured minimum R:R gate.
        if est_rr < cfg.min_rr_ratio:
            return None

    return Signal(
        symbol=symbol,
        timeframe=tf,
        side=side,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8),
        rr_ratio=cfg.rr_ratio,
        confirmations=passed,
        strength=len(passed),
        score=round(score, 2),
        max_score=round(max_score, 2),
        reversal_signals=reversal_signals,
        rsi_value=round(rsis[-1] or 0, 2),
        volume_ratio=round(vol_ratio, 2),
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=round(fvg["top"], 8),
        fvg_bottom=round(fvg["bottom"], 8),
        atr=round(atr, 8),
        atr_multiplier=cfg.atr_sl_multiplier,
    )


# ===========================================================================
# STRATEGY 2: Impulse FVG + Consolidation + Multi-TP (additive, selectable)
# ===========================================================================
def detect_market_structure(candles: list[list[float]], window: int) -> str:
    """Return 'up' (HH+HL), 'down' (LH+LL) or 'range' from swing structure."""
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    low_idx, high_idx = detect_pivots(highs, window)  # highs pivots
    lo2, hi2 = detect_pivots(lows, window)
    swing_highs = [highs[i] for i in high_idx][-2:]
    swing_lows = [lows[i] for i in lo2][-2:]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "range"
    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]
    if hh and hl:
        return "up"
    if lh and ll:
        return "down"
    return "range"


def detect_all_fvgs(highs: list[float], lows: list[float], lookback: int) -> list[dict[str, Any]]:
    """All still-open FVGs within lookback, each with kind/top/bottom/index/gap."""
    n = len(highs)
    start = max(2, n - lookback)
    out: list[dict[str, Any]] = []
    for i in range(start, n):
        if highs[i - 2] < lows[i]:  # bullish gap
            top, bottom = lows[i], highs[i - 2]
            if not any(lows[j] <= bottom for j in range(i + 1, n)):
                out.append({"kind": "bullish", "top": top, "bottom": bottom,
                            "index": i, "gap": top - bottom})
        if lows[i - 2] > highs[i]:  # bearish gap
            top, bottom = lows[i - 2], highs[i]
            if not any(highs[j] >= top for j in range(i + 1, n)):
                out.append({"kind": "bearish", "top": top, "bottom": bottom,
                            "index": i, "gap": top - bottom})
    return out


def find_consolidation(candles: list[list[float]], cfg: Config, side: str, atr: float
                       ) -> Optional[dict[str, float]]:
    """Rectangle from the K candles BEFORE the last (breakout) candle, tight
    within consolidation_max_atr*ATR. Confirms a CLOSE breakout in trade side."""
    k = cfg.consolidation_min_candles
    if len(candles) < k + 2:
        return None
    prior = candles[-(k + 1):-1]  # exclude the breakout candle (last)
    rect_high = max(c[3] for c in prior)
    rect_low = min(c[4] for c in prior)
    if (rect_high - rect_low) > cfg.consolidation_max_atr * atr:
        return None
    bo_close = candles[-1][2]
    if side == "long" and bo_close > rect_high:
        return {"high": rect_high, "low": rect_low}
    if side == "short" and bo_close < rect_low:
        return {"high": rect_high, "low": rect_low}
    return None


async def analyze_pair_impulse(symbol: str, tf: str, cfg: Config) -> Optional[Signal]:
    """Impulse-FVG strategy: trend -> origin impulse FVG (TP2) -> consolidation
    breakout (entry) -> SL under/over the box -> TP1 nearest FVG, TP2 origin FVG."""
    candles = await kucoin.get_klines(symbol, tf)
    if len(candles) < 60:
        return None
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    closes = [c[2] for c in candles]

    structure = detect_market_structure(candles, cfg.pivot_window)
    if structure == "range":
        return None
    side = "long" if structure == "up" else "short"

    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if not atr or atr <= 0:
        return None

    # Consolidation breakout in the trend direction (entry trigger).
    rect = find_consolidation(candles, cfg, side, atr)
    if rect is None:
        return None

    entry = closes[-1]
    sl_buffer = max(cfg.atr_sl_multiplier * atr, entry * (cfg.sl_padding_pct / 100))
    if side == "long":
        stop_loss = rect["low"] - sl_buffer
    else:
        stop_loss = rect["high"] + sl_buffer
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None

    # Unfilled FVGs in trade direction, positioned as targets beyond entry.
    fvgs = detect_all_fvgs(highs, lows, cfg.fvg_lookback)
    if side == "long":
        targets = [f for f in fvgs if f["kind"] == "bullish" and f["bottom"] > entry]
        targets.sort(key=lambda f: f["bottom"])  # nearest first
        tp_edge = lambda f: f["bottom"]
    else:
        targets = [f for f in fvgs if f["kind"] == "bearish" and f["top"] < entry]
        targets.sort(key=lambda f: -f["top"])  # nearest first
        tp_edge = lambda f: f["top"]

    if not targets:
        return None  # need at least TP1
    tp1 = tp_edge(targets[0])  # nearest unfilled FVG edge
    # Origin/important FVG = the largest-gap unfilled FVG in trend direction.
    origin = max(targets, key=lambda f: f["gap"])
    tp2 = tp_edge(origin) if origin is not targets[0] else 0.0
    take_profit = tp1  # primary target for charting/base flow

    return Signal(
        symbol=symbol, timeframe=tf, side=side,
        entry=round(entry, 8), stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8), rr_ratio=cfg.rr_ratio,
        confirmations=["Market Structure", "Impulse FVG", "Consolidation Breakout"],
        strength=3, score=0.0, max_score=0.0,
        strategy="impulse_fvg",
        tp1=round(tp1, 8), tp2=round(tp2, 8),
        consolidation_high=round(rect["high"], 8),
        consolidation_low=round(rect["low"], 8),
        rsi_value=0.0, volume_ratio=0.0,
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=round(origin["top"], 8), fvg_bottom=round(origin["bottom"], 8),
        atr=round(atr, 8), atr_multiplier=cfg.atr_sl_multiplier,
    )




# ===========================================================================
# STRATEGY 3: Counter-trend reversal at consolidation (spec-exact)
# impulse -> FVG (with trend) -> consolidation -> reversal pattern AGAINST
# trend -> enter AGAINST trend, target = impulse FVG edge. + strict RSI filters.
# ===========================================================================
def _higher_tf(tf: str) -> str:
    return {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1d"}.get(tf, "1h")


def detect_reversal_pattern(opens, highs, lows, closes, against: str) -> Optional[str]:
    """Detect engulfing or star pattern oriented `against` ('bearish'|'bullish')."""
    if len(closes) < 3:
        return None
    o1, c1 = opens[-2], closes[-2]
    o0, c0 = opens[-1], closes[-1]
    if against == "bearish":  # trend up -> want bearish reversal
        # Bearish engulfing
        if c1 > o1 and c0 < o0 and c0 <= o1 and o0 >= c1:
            return "Bearish Engulfing"
        # Evening star (3 candles)
        o2, c2 = opens[-3], closes[-3]
        mid_small = abs(c1 - o1) < abs(c2 - o2) * 0.5
        if c2 > o2 and mid_small and c0 < o0 and c0 < (o2 + c2) / 2:
            return "Evening Star"
    else:  # trend down -> want bullish reversal
        if c1 < o1 and c0 > o0 and c0 >= o1 and o0 <= c1:
            return "Bullish Engulfing"
        o2, c2 = opens[-3], closes[-3]
        mid_small = abs(c1 - o1) < abs(c2 - o2) * 0.5
        if c2 < o2 and mid_small and c0 > o0 and c0 > (o2 + c2) / 2:
            return "Morning Star"
    return None


def _rsi_momentum_turn(rsis: list[Optional[float]], side: str, ob: float, os_: float) -> bool:
    vals = [r for r in rsis[-4:] if r is not None]
    if len(vals) < 2:
        return False
    prev, cur = vals[-2], vals[-1]
    if side == "long":
        return prev < os_ and cur > os_  # was <30, turning up
    return prev > ob and cur < ob  # was >70, turning down


async def analyze_pair_counter(symbol: str, tf: str, cfg: Config) -> Optional[Signal]:
    candles = await kucoin.get_klines(symbol, tf)
    if len(candles) < 60:
        return None
    opens = [c[1] for c in candles]
    closes = [c[2] for c in candles]
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]

    # Step 1: trend
    structure = detect_market_structure(candles, cfg.pivot_window)
    if structure == "range":
        return None
    trend = structure  # 'up' or 'down'
    entry_side = "short" if trend == "up" else "long"  # AGAINST the trend

    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if not atr or atr <= 0:
        return None

    # Step 2: impulse FVG in the TREND direction (most significant = largest gap)
    fvgs = detect_all_fvgs(highs, lows, cfg.fvg_lookback)
    trend_kind = "bullish" if trend == "up" else "bearish"
    impulse_fvgs = [f for f in fvgs if f["kind"] == trend_kind]
    if not impulse_fvgs:
        return None
    origin = max(impulse_fvgs, key=lambda f: f["gap"])

    # Step 3: consolidation (tight range) in the recent candles
    k = cfg.consolidation_min_candles
    box = candles[-(k + 1):-1]
    if len(box) < k:
        return None
    if (max(c[3] for c in box) - min(c[4] for c in box)) > cfg.consolidation_max_atr * atr:
        return None

    # Step 4: reversal pattern AGAINST the trend
    against = "bearish" if trend == "up" else "bullish"
    pattern = detect_reversal_pattern(opens, highs, lows, closes, against)
    if pattern is None:
        return None  # continuation-oriented or none -> NEVER open

    # Step 1bis: RSI filters (ALL three required)
    rsis = rsi_wilder(closes, cfg.rsi_period)
    # (a) higher-TF extreme
    htf = _higher_tf(tf)
    hcandles = await kucoin.get_klines(symbol, htf)
    hrsis = rsi_wilder([c[2] for c in hcandles], cfg.rsi_period) if len(hcandles) > cfg.rsi_period else []
    hval = next((r for r in reversed(hrsis) if r is not None), None)
    if hval is None:
        return None
    if entry_side == "long" and hval > cfg.rsi_high_tf_os:
        return None
    if entry_side == "short" and hval < cfg.rsi_high_tf_ob:
        return None
    # (b) momentum turn on trade TF
    if not _rsi_momentum_turn(rsis, entry_side, cfg.rsi_overbought, cfg.rsi_oversold):
        return None
    # (c) divergence coherent with entry
    div = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if (entry_side == "long" and div != "bullish") or (entry_side == "short" and div != "bearish"):
        return None

    # Step 5/6: entry immediate; target = impulse FVG edge
    entry = closes[-1]
    # SL beyond the IMPULSE EXTREME (not the FVG edge)
    i = origin["index"]
    seg_hi = max(highs[max(0, i - 2):i + 1])
    seg_lo = min(lows[max(0, i - 2):i + 1])
    sl_buffer = max(cfg.atr_sl_multiplier * atr, entry * (cfg.sl_padding_pct / 100))
    mid = (origin["top"] + origin["bottom"]) / 2  # 50% of the FVG zone
    if entry_side == "long":
        stop_loss = seg_lo - sl_buffer
        tp1 = origin["bottom"]  # TP1 = touch of the FVG zone (near edge)
        tp2 = mid               # TP2 = 50% of the FVG zone
    else:
        stop_loss = seg_hi + sl_buffer
        tp1 = origin["top"]     # TP1 = touch of the FVG zone (near edge)
        tp2 = mid               # TP2 = 50% of the FVG zone
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    # target must be beyond entry in trade direction
    if (entry_side == "long" and tp1 <= entry) or (entry_side == "short" and tp1 >= entry):
        return None

    return Signal(
        symbol=symbol, timeframe=tf, side=entry_side,
        entry=round(entry, 8), stop_loss=round(stop_loss, 8),
        take_profit=round(tp1, 8), rr_ratio=cfg.rr_ratio,
        confirmations=["Counter-Trend", pattern, "RSI HTF Extreme",
                       "RSI Momentum Turn", "RSI Divergence"],
        strength=5, score=0.0, max_score=0.0,
        strategy="counter_trend",
        tp1=round(tp1, 8), tp2=round(tp2, 8),
        consolidation_high=round(max(c[3] for c in box), 8),
        consolidation_low=round(min(c[4] for c in box), 8),
        rsi_value=round(next((r for r in reversed(rsis) if r is not None), 0) or 0, 2),
        volume_ratio=0.0,
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=round(origin["top"], 8), fvg_bottom=round(origin["bottom"], 8),
        atr=round(atr, 8), atr_multiplier=cfg.atr_sl_multiplier,
    )


scan_state = ScanState()


async def run_scan() -> dict[str, Any]:
    if scan_state.is_scanning:
        return {"skipped": True, "reason": "already scanning"}
    scan_state.is_scanning = True
    started = datetime.now(timezone.utc)
    try:
        cfg = await get_config()
        # 1) Fetch tickers with volume for filtering
        tickers = await kucoin.get_tickers()
        # Build map symbol -> volValue (24h quote volume)
        vol_map: dict[str, float] = {}
        for t in tickers:
            try:
                vol_map[t["symbol"]] = float(t.get("volValue") or 0)
            except (TypeError, ValueError):
                continue

        symbols = await kucoin.get_symbols()
        pairs: list[str] = []
        for s in symbols:
            if not s.get("enableTrading"):
                continue
            sym = s.get("symbol")
            if not sym:
                continue
            if cfg.quote_filter and s.get("quoteCurrency") != cfg.quote_filter:
                continue
            if cfg.excluded_pairs and sym in cfg.excluded_pairs:
                continue
            if cfg.enabled_pairs and sym not in cfg.enabled_pairs:
                continue
            if vol_map.get(sym, 0) < cfg.min_24h_volume_usdt:
                continue
            pairs.append(sym)

        # Sort by volume descending and cap
        pairs.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
        pairs = pairs[: cfg.max_pairs_per_scan]

        logger.info("Scanning %d pairs across %s", len(pairs), cfg.timeframes)
        signals_found: list[Signal] = []
        # Reset discarded-setup log so bottleneck analysis reflects this scan.
        await db.setup_debug_log.delete_many({})

        async def process(sym: str) -> None:
            for tf in cfg.timeframes:
                try:
                    if cfg.strategy_mode in ("scoring", "both"):
                        sig = await analyze_pair(sym, tf, cfg)
                        if sig:
                            signals_found.append(sig)
                    if cfg.strategy_mode in ("impulse_fvg", "both"):
                        sig2 = await analyze_pair_impulse(sym, tf, cfg)
                        if sig2:
                            signals_found.append(sig2)
                    if cfg.strategy_mode in ("counter_trend", "both"):
                        sig3 = await analyze_pair_counter(sym, tf, cfg)
                        if sig3:
                            signals_found.append(sig3)
                except Exception as e:  # noqa: BLE001
                    logger.debug("analyze %s %s failed: %s", sym, tf, e)

        # Batch concurrency to respect rate limits
        BATCH = 20
        for i in range(0, len(pairs), BATCH):
            await asyncio.gather(*(process(p) for p in pairs[i : i + BATCH]))

        # Deduplicate: replace existing active signal for same symbol+timeframe+side
        if signals_found:
            for sig in signals_found:
                await db.signals.update_many(
                    {
                        "symbol": sig.symbol,
                        "timeframe": sig.timeframe,
                        "side": sig.side,
                        "status": "active",
                    },
                    {"$set": {"status": "expired"}},
                )
            await db.signals.insert_many([s.model_dump() for s in signals_found])

            # Auto-execute paper trades if enabled
            pcfg = await get_paper_config()
            if pcfg.auto_execute:
                # Best signals first (higher strength)
                for sig in sorted(signals_found, key=lambda s: -s.strength):
                    await open_paper_position(sig.model_dump())

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        scan_state.last_scan_at = datetime.now(timezone.utc).isoformat()
        scan_state.last_scan_duration_s = round(duration, 2)
        scan_state.last_scanned_pairs = len(pairs)
        scan_state.last_signals_found = len(signals_found)
        logger.info(
            "Scan complete: %d pairs, %d signals in %.1fs",
            len(pairs),
            len(signals_found),
            duration,
        )
        return {
            "scanned_pairs": len(pairs),
            "signals_found": len(signals_found),
            "duration_s": round(duration, 2),
        }
    finally:
        scan_state.is_scanning = False


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------
async def scheduler_loop() -> None:
    # small warm-up delay
    await asyncio.sleep(5)
    while True:
        cfg = await get_config()
        try:
            await run_scan()
        except Exception as e:  # noqa: BLE001
            logger.exception("Scan loop error: %s", e)
        await asyncio.sleep(max(60, cfg.scan_interval_minutes * 60))


async def paper_monitor_loop() -> None:
    """Check SL/TP hits every 3s using the real-time WS price cache."""
    await asyncio.sleep(8)
    while True:
        try:
            await monitor_paper_positions()
        except Exception as e:  # noqa: BLE001
            logger.exception("Paper monitor error: %s", e)
        await asyncio.sleep(3)


async def resolve_premature_stops() -> None:
    """For each pending SL log, look ahead N candles to see if the ORIGINAL
    target would have been reached — i.e. whether the stop was premature."""
    cfg = await get_config()
    lookahead = cfg.premature_lookahead
    pending = await db.stop_debug_log.find(
        {"premature_status": "pending"}, {"_id": 0}
    ).to_list(500)
    for log in pending:
        tf = log.get("timeframe", "1h")
        tf_sec = TF_SECONDS.get(tf, 3600)
        closed_epoch = datetime.fromisoformat(log["closed_at"]).timestamp()
        candles = await kucoin.get_klines(log["symbol"], tf)
        # candles: [t, o, c, h, l, v] ascending, t in seconds
        after = [c for c in candles if c[0] >= closed_epoch]
        if not after:
            continue
        window = after[:lookahead]
        tp = log["take_profit"]
        side = log["side"]
        hit_idx = None
        for i, c in enumerate(window):
            high, low = c[3], c[4]
            if side == "long" and high >= tp:
                hit_idx = i + 1
                break
            if side == "short" and low <= tp:
                hit_idx = i + 1
                break
        # Only conclude once we either found a hit or the full window elapsed
        elapsed = time.time() - closed_epoch
        window_complete = elapsed >= lookahead * tf_sec
        if hit_idx is not None:
            await db.stop_debug_log.update_one(
                {"id": log["id"]},
                {"$set": {
                    "premature_status": "premature",
                    "would_hit_target": True,
                    "candles_to_target": hit_idx,
                }},
            )
        elif window_complete:
            await db.stop_debug_log.update_one(
                {"id": log["id"]},
                {"$set": {
                    "premature_status": "valid",
                    "would_hit_target": False,
                    "candles_to_target": None,
                }},
            )


async def premature_stop_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await resolve_premature_stops()
        except Exception as e:  # noqa: BLE001
            logger.exception("Premature stop checker error: %s", e)
        await asyncio.sleep(120)


# ---------------------------------------------------------------------------
# FastAPI app & routes
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    scan_task = asyncio.create_task(scheduler_loop())
    monitor_task = asyncio.create_task(paper_monitor_loop())
    ws_task = asyncio.create_task(price_feed.run())
    premature_task = asyncio.create_task(premature_stop_loop())
    yield
    scan_task.cancel()
    monitor_task.cancel()
    ws_task.cancel()
    premature_task.cancel()
    await kucoin.close()
    client.close()


app = FastAPI(lifespan=lifespan)
api = APIRouter(prefix="/api")


@api.get("/")
async def root() -> dict[str, str]:
    return {"service": "kusignal-bot", "status": "ok"}


@api.get("/status", response_model=ScanState)
async def status() -> ScanState:
    return scan_state


@api.get("/config", response_model=Config)
async def read_config() -> Config:
    return await get_config()


@api.put("/config", response_model=Config)
async def update_config(cfg: Config) -> Config:
    return await save_config(cfg)


@api.get("/pairs")
async def list_pairs(limit: int = 200) -> dict[str, Any]:
    cfg = await get_config()
    tickers = await kucoin.get_tickers()
    out: list[dict[str, Any]] = []
    for t in tickers:
        sym = t.get("symbol", "")
        if cfg.quote_filter and not sym.endswith("-" + cfg.quote_filter):
            continue
        try:
            vol = float(t.get("volValue") or 0)
            price = float(t.get("last") or 0)
            change = float(t.get("changeRate") or 0)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "symbol": sym,
                "price": price,
                "change_pct": round(change * 100, 2),
                "volume_24h_usdt": vol,
            }
        )
    out.sort(key=lambda x: x["volume_24h_usdt"], reverse=True)
    return {"pairs": out[:limit]}


@api.delete("/signals")
async def clear_signals() -> dict[str, Any]:
    """Reset the Signal History: delete all stored signals. Does not affect
    signal generation, Portfolio, or Settings."""
    res = await db.signals.delete_many({})
    return {"ok": True, "deleted": res.deleted_count}



@api.get("/signals")
async def list_signals(
    side: Optional[str] = Query(None, pattern="^(long|short)$"),
    timeframe: Optional[str] = None,
    status: str = "active",
    limit: int = 100,
) -> dict[str, Any]:
    q: dict[str, Any] = {}
    if status != "all":
        q["status"] = status
    if side:
        q["side"] = side
    if timeframe:
        q["timeframe"] = timeframe
    cursor = db.signals.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cursor.to_list(length=limit)
    return {"signals": items, "count": len(items)}


@api.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict[str, Any]:
    doc = await db.signals.find_one({"id": signal_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Signal not found")
    return doc


@api.get("/candles/{symbol}")
async def get_candles(symbol: str, timeframe: str = "1h") -> dict[str, Any]:
    candles = await kucoin.get_klines(symbol, timeframe)
    closes = [c[2] for c in candles]
    cfg = await get_config()
    rsis = rsi_wilder(closes, cfg.rsi_period) if closes else []
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {"t": c[0], "o": c[1], "c": c[2], "h": c[3], "l": c[4], "v": c[5]}
            for c in candles
        ],
        "rsi": [r if r is not None else 0 for r in rsis],
    }


@api.post("/scan")
async def trigger_scan() -> dict[str, Any]:
    # Fire-and-forget so client isn't blocked for minutes
    asyncio.create_task(run_scan())
    return {"started": True}


@api.get("/history/stats")
async def history_stats() -> dict[str, Any]:
    total = await db.signals.count_documents({})
    active = await db.signals.count_documents({"status": "active"})
    wins = await db.signals.count_documents({"outcome": "win"})
    losses = await db.signals.count_documents({"outcome": "loss"})
    settled = wins + losses
    win_rate = round((wins / settled) * 100, 1) if settled else 0.0
    return {
        "total": total,
        "active": active,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
    }


# ---------------------------------------------------------------------------
# Paper Trading endpoints
# ---------------------------------------------------------------------------
@api.get("/paper/config", response_model=PaperConfig)
async def read_paper_config() -> PaperConfig:
    return await get_paper_config()


@api.put("/paper/config", response_model=PaperConfig)
async def update_paper_config(cfg: PaperConfig) -> PaperConfig:
    return await save_paper_config(cfg)


@api.get("/paper/portfolio")
async def paper_portfolio() -> dict[str, Any]:
    pcfg = await get_paper_config()
    cash = await get_paper_cash()
    positions = await db.paper_positions.find({}, {"_id": 0}).to_list(1000)
    # Mark to market
    tickers = await kucoin.get_tickers()
    price_map: dict[str, float] = {}
    for t in tickers:
        try:
            price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue
    unrealized = 0.0
    spot_positions_value = 0.0
    enriched: list[dict[str, Any]] = []
    for p in positions:
        cur = price_map.get(p["symbol"], p["entry"])
        if p["side"] == "long":
            pnl = (cur - p["entry"]) * p["quantity"]
        else:
            pnl = (p["entry"] - cur) * p["quantity"]
        pnl_pct = (pnl / (p["entry"] * p["quantity"])) * 100 if p["entry"] * p["quantity"] > 0 else 0
        unrealized += pnl
        spot_positions_value += cur * p["quantity"]
        enriched.append(
            {
                **p,
                "current_price": round(cur, 8),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
            }
        )
    trades = await db.paper_trades.find({}, {"_id": 0}).to_list(1000)
    realized = sum(t.get("pnl_usdt", 0.0) for t in trades)
    wins = sum(1 for t in trades if t.get("outcome") == "win")
    losses = sum(1 for t in trades if t.get("outcome") == "loss")
    settled = wins + losses
    win_rate = round((wins / settled) * 100, 1) if settled else 0.0
    # In spot mode, cash is already locked when a position is opened,
    # so equity = cash + market value of open positions.
    # In leverage mode, positions carry no locked cash, equity = cash + unrealized PnL.
    if pcfg.trading_mode == "spot":
        equity = cash + spot_positions_value
    else:
        equity = cash + unrealized
    return {
        "initial_capital": pcfg.initial_capital,
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "unrealized_pnl": round(unrealized, 2),
        "realized_pnl": round(realized, 2),
        "total_return_pct": round(((equity - pcfg.initial_capital) / pcfg.initial_capital) * 100, 2)
        if pcfg.initial_capital > 0
        else 0,
        "open_positions_count": len(enriched),
        "closed_trades_count": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "auto_execute": pcfg.auto_execute,
        "trading_mode": pcfg.trading_mode,
        "positions": enriched,
    }


@api.get("/paper/trades")
async def paper_trades(limit: int = 100) -> dict[str, Any]:
    cursor = db.paper_trades.find({}, {"_id": 0}).sort("closed_at", -1).limit(limit)
    trades = await cursor.to_list(length=limit)
    return {"trades": trades, "count": len(trades)}


@api.get("/slippage/log")
async def slippage_log(limit: int = 100) -> dict[str, Any]:
    cursor = db.slippage_log.find({}, {"_id": 0}).sort("at", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    total_abs = sum(abs(l.get("slippage_usdt", 0.0)) for l in logs)
    avg_pct = (
        round(sum(l.get("slippage_pct", 0.0) for l in logs) / len(logs), 4)
        if logs
        else 0.0
    )
    return {
        "logs": logs,
        "count": len(logs),
        "total_abs_slippage_usdt": round(total_abs, 4),
        "avg_slippage_pct": avg_pct,
    }


@api.get("/feed/status")
async def feed_status() -> dict[str, Any]:
    return {
        "ws_connected": price_feed._connected,
        "subscribed": sorted(price_feed._subscribed),
        "cached_symbols": len(price_feed.prices),
    }


@api.get("/stop-debug/log")
async def stop_debug_log(limit: int = 100) -> dict[str, Any]:
    cursor = db.stop_debug_log.find({}, {"_id": 0}).sort("closed_at", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    total = len(logs)
    premature = sum(1 for l in logs if l.get("premature_status") == "premature")
    valid = sum(1 for l in logs if l.get("premature_status") == "valid")
    pending = sum(1 for l in logs if l.get("premature_status") == "pending")
    resolved = premature + valid
    premature_rate = round((premature / resolved) * 100, 1) if resolved else 0.0
    avg_atr_dist = [
        l["stop_distance_in_atr"] for l in logs if l.get("stop_distance_in_atr")
    ]
    return {
        "logs": logs,
        "count": total,
        "premature": premature,
        "valid": valid,
        "pending": pending,
        "premature_rate": premature_rate,
        "avg_stop_distance_atr": round(sum(avg_atr_dist) / len(avg_atr_dist), 3)
        if avg_atr_dist
        else 0.0,
    }


@api.get("/setup-debug/log")
async def setup_debug_log(limit: int = 200) -> dict[str, Any]:
    """Discarded setups (score below threshold) + bottleneck analysis:
    which filter fails most often across rejected setups."""
    cursor = db.setup_debug_log.find({}, {"_id": 0}).sort("at", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    fail_counts: dict[str, int] = {}
    pass_counts: dict[str, int] = {}
    for l in logs:
        for f in l.get("failed", []):
            fail_counts[f] = fail_counts.get(f, 0) + 1
        for p in l.get("passed", []):
            pass_counts[p] = pass_counts.get(p, 0) + 1
    # The bottleneck = the filter that fails most among near-miss setups
    bottleneck = max(fail_counts, key=fail_counts.get) if fail_counts else None
    return {
        "logs": logs,
        "count": len(logs),
        "fail_counts": fail_counts,
        "pass_counts": pass_counts,
        "bottleneck": bottleneck,
    }


@api.delete("/setup-debug/log")
async def clear_setup_debug_log() -> dict[str, Any]:
    await db.setup_debug_log.delete_many({})
    return {"ok": True}






@api.post("/paper/execute/{signal_id}")
async def paper_execute(signal_id: str) -> dict[str, Any]:
    signal = await db.signals.find_one({"id": signal_id}, {"_id": 0})
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    pos = await open_paper_position(signal)
    if not pos:
        raise HTTPException(
            status_code=400,
            detail="Cannot open (max positions reached or duplicate)",
        )
    return {"position": pos.model_dump()}


@api.post("/paper/positions/{position_id}/close")
async def paper_close_manual(position_id: str) -> dict[str, Any]:
    pos = await db.paper_positions.find_one({"id": position_id}, {"_id": 0})
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    tickers = await kucoin.get_tickers()
    price = 0.0
    for t in tickers:
        if t.get("symbol") == pos["symbol"]:
            try:
                price = float(t.get("last") or 0)
            except (TypeError, ValueError):
                pass
            break
    if price <= 0:
        price = float(pos["entry"])
    # Outcome purely by PnL sign
    if pos["side"] == "long":
        outcome = "win" if price >= pos["entry"] else "loss"
    else:
        outcome = "win" if price <= pos["entry"] else "loss"
    trade = await close_paper_position(pos, price, outcome)
    return {"trade": trade.model_dump()}


@api.post("/paper/reset")
async def paper_reset() -> dict[str, Any]:
    pcfg = await get_paper_config()
    await db.paper_positions.delete_many({})
    await db.paper_trades.delete_many({})
    await set_paper_cash(pcfg.initial_capital)
    return {"ok": True, "cash": pcfg.initial_capital}


@api.post("/paper/set-capital")
async def paper_set_capital(payload: dict[str, float]) -> dict[str, Any]:
    """Set new initial capital AND reset the paper portfolio."""
    amount = float(payload.get("initial_capital", 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be > 0")
    pcfg = await get_paper_config()
    pcfg.initial_capital = amount
    await save_paper_config(pcfg)
    await db.paper_positions.delete_many({})
    await db.paper_trades.delete_many({})
    await set_paper_cash(amount)
    return {"ok": True, "initial_capital": amount, "cash": amount}


@api.post("/paper/mode")
async def paper_set_mode(payload: dict[str, str]) -> dict[str, Any]:
    """Simple toggle between manual and auto execution."""
    mode = payload.get("mode", "").lower()
    if mode not in ("manual", "auto"):
        raise HTTPException(status_code=400, detail="mode must be 'manual' or 'auto'")
    pcfg = await get_paper_config()
    pcfg.auto_execute = mode == "auto"
    await save_paper_config(pcfg)
    return {"ok": True, "mode": mode, "auto_execute": pcfg.auto_execute}


@api.post("/paper/trading-mode")
async def paper_set_trading_mode(payload: dict[str, str]) -> dict[str, Any]:
    """Switch between spot (cash-locked, no shorts) and leverage (futures-style PnL)."""
    mode = payload.get("trading_mode", "").lower()
    if mode not in ("spot", "leverage"):
        raise HTTPException(
            status_code=400, detail="trading_mode must be 'spot' or 'leverage'"
        )
    # Refuse to switch while there are open positions to avoid inconsistent cash accounting
    open_count = await db.paper_positions.count_documents({})
    if open_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Close {open_count} open positions before switching mode",
        )
    pcfg = await get_paper_config()
    pcfg.trading_mode = mode
    await save_paper_config(pcfg)
    return {"ok": True, "trading_mode": mode}


@api.post("/strategy-mode")
async def set_strategy_mode(payload: dict[str, str]) -> dict[str, Any]:
    """Select signal strategy: 'scoring', 'impulse_fvg' or 'both'."""
    mode = payload.get("strategy_mode", "").lower()
    if mode not in ("scoring", "impulse_fvg", "counter_trend", "both"):
        raise HTTPException(status_code=400, detail="invalid strategy_mode")
    cfg = await get_config()
    cfg.strategy_mode = mode
    await save_config(cfg)
    return {"ok": True, "strategy_mode": mode}



# ---------------------------------------------------------------------------
# KuCoin authenticated integration (connection test only, no order execution)
# ---------------------------------------------------------------------------
async def _kucoin_signed_get(path: str) -> httpx.Response:
    doc = await db.exchange_creds.find_one({"_id": "kucoin"}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=400, detail="No KuCoin credentials stored")
    api_key = decrypt_str(doc["api_key"])
    api_secret = decrypt_str(doc["api_secret"])
    api_passphrase = decrypt_str(doc["api_passphrase"])
    ts = str(int(time.time() * 1000))
    str_to_sign = ts + "GET" + path
    sig = base64.b64encode(
        hmac.new(api_secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    passphrase = base64.b64encode(
        hmac.new(api_secret.encode(), api_passphrase.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "KC-API-KEY": api_key,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": passphrase,
        "KC-API-KEY-VERSION": "2",
    }
    async with httpx.AsyncClient(base_url=KUCOIN_BASE, timeout=10.0) as c:
        return await c.get(path, headers=headers)


@api.get("/exchange/status")
async def exchange_status() -> dict[str, Any]:
    doc = await db.exchange_creds.find_one({"_id": "kucoin"}, {"_id": 0})
    if not doc:
        return {"connected": False, "exchange": "kucoin"}
    try:
        r = await _kucoin_signed_get("/api/v1/accounts")
        if r.status_code != 200:
            return {
                "connected": False,
                "exchange": "kucoin",
                "error": f"HTTP {r.status_code}",
                "api_key_masked": doc.get("api_key_masked", ""),
            }
        data = r.json()
        if data.get("code") != "200000":
            return {
                "connected": False,
                "exchange": "kucoin",
                "error": data.get("msg", "unknown"),
                "api_key_masked": doc.get("api_key_masked", ""),
            }
        # Sum USDT balances across accounts
        usdt_total = 0.0
        for acc in data.get("data", []):
            if acc.get("currency") == "USDT":
                try:
                    usdt_total += float(acc.get("balance") or 0)
                except (TypeError, ValueError):
                    continue
        return {
            "connected": True,
            "exchange": "kucoin",
            "api_key_masked": doc.get("api_key_masked", ""),
            "usdt_balance": round(usdt_total, 2),
            "connected_at": doc.get("connected_at"),
        }
    except (httpx.HTTPError, InvalidToken) as e:
        return {"connected": False, "exchange": "kucoin", "error": str(e)}


@api.post("/exchange/connect")
async def exchange_connect(req: ExchangeConnectRequest) -> dict[str, Any]:
    if not req.api_key or not req.api_secret or not req.api_passphrase:
        raise HTTPException(status_code=400, detail="All fields required")
    doc = {
        "api_key": encrypt_str(req.api_key),
        "api_secret": encrypt_str(req.api_secret),
        "api_passphrase": encrypt_str(req.api_passphrase),
        "api_key_masked": (req.api_key[:4] + "…" + req.api_key[-4:])
        if len(req.api_key) > 8
        else "***",
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.exchange_creds.update_one(
        {"_id": "kucoin"}, {"$set": doc}, upsert=True
    )
    # Test connection immediately
    status_res = await exchange_status()
    if not status_res.get("connected"):
        # Roll back on failure to avoid storing invalid creds silently
        await db.exchange_creds.delete_one({"_id": "kucoin"})
        raise HTTPException(
            status_code=400,
            detail=f"Connection failed: {status_res.get('error', 'unknown')}",
        )
    return status_res


@api.post("/exchange/disconnect")
async def exchange_disconnect() -> dict[str, Any]:
    await db.exchange_creds.delete_one({"_id": "kucoin"})
    return {"ok": True}


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
