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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
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
# ---------------------------------------------------------------------------
# Bybit EU (MiCA) v5 — market data endpoints
# ---------------------------------------------------------------------------
BYBIT_BASE = "https://api.bybit.eu"
BYBIT_WS_PUBLIC = "wss://stream.bybit.eu/v5/public"
TF_MAP = {
 "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}
TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
DEFAULT_TIMEFRAMES = ["1h", "4h"]
CANDLE_LIMIT = 200  # candles fetched per pair/tf

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class Config(BaseModel):
    scan_interval_minutes: int = 1
    timeframes: list[str] = Field(default_factory=lambda: DEFAULT_TIMEFRAMES.copy())
    quote_filter: str = "USDC,EUR"  # Bybit EU spot quotes (comma-separated)
    min_24h_volume_usdt: float = 100_000.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    pivot_window: int = 5
    volume_ma_period: int = 20
    volume_spike_multiplier: float = 1.5
    rr_ratio: float = 2.0
    sl_padding_pct: float = 0.3  # min % buffer beyond FVG edge (fallback)
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5  # SL buffer = max(atr_mult*ATR, sl_padding_pct)
    min_rr_ratio: float = 1.5  # reject setups below this estimated R:R
    premature_lookahead: int = 20  # candles to check if target would've been hit

    # --- Scalping Bot (independent strategy) ---
    scalping_enabled: bool = True
    scalping_timeframe: str = "5m"
    scalping_rsi_period: int = 9
    scalping_bb_period: int = 20
    scalping_bb_std: float = 2.0
    scalping_ema_fast: int = 9
    scalping_ema_slow: int = 21
    scalping_volume_multiplier: float = 1.5
    scalping_min_hold_seconds: int = 60  # min time before the invalidation check can close early
    scalping_cooldown_minutes: int = 10  # pause on a symbol after a losing scalping trade
    scalping_invalidation_buffer_pct: float = 0.15  # min % beyond VWAP required, on top of the EMA cross, to count as invalidated
    signal_validity_candles: int = 5  # a condition counts if it happened within N bars
    fvg_lookback: int = 40  # how far back to look for an open FVG
    reversal_rejection_wick_ratio: float = 1.5  # wick/body ratio for rejection candle
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
    # --- Consolidation-breakout params (shared by counter_trend) ---
    consolidation_min_candles: int = 3
    consolidation_max_atr: float = 1.5  # channel width <= this * ATR
    tp1_pct: float = 65.0  # % closed at TP1
    # --- Counter-trend strategy RSI filters ---
    rsi_high_tf_ob: float = 80.0  # higher-TF overbought (short)
    rsi_high_tf_os: float = 20.0  # higher-TF oversold (long)
    trailing_pct_from_entry: float = 1.0  # counter-trend trailing distance %
    post_tp1_advance_pct: float = 0.5  # % beyond TP1 before moving SL to TP1 (net fees)
    # --- Parallel strategy selection ---
    enabled_strategies: list[str] = Field(default_factory=list)  # empty = ["counter_trend", "fvg_reversal"]
    # --- FVG Reversal strategy (independent params, contro-trend on retracement) ---
    fvgr_rsi_high_tf_ob: float = 80.0
    fvgr_rsi_high_tf_os: float = 20.0
    fvgr_tp1_pct: float = 65.0
    fvgr_tp2_pct: float = 35.0
    fvgr_post_tp1_advance_pct: float = 0.5
    fvgr_trailing_pct: float = 1.0  # trailing distance % (from best price), when in profit
    fvgr_atr_sl_multiplier: float = 1.5
    fvgr_min_rr_ratio: float = 1.5
    # --- Trend Exhaustion Score (additive, COUNTER-TREND strategies only:
    # "counter_trend" / Rev Pre-FVG and "fvg_reversal" / FVG Reversal). Not
    # used by "scoring" or "impulse_fvg". ---
    exhaustion_lookback: int = 10
    exhaustion_min_score: float = 1.0  # was 2.0 — lowered so it stops being an extra hard gate; raise back to 2.0 (or more) in Settings any time
    trend_structure_strict: bool = False  # False = only ONE of higher-high/higher-low (or the "down" mirror) is needed to call a trend, not both — set True in Settings to go back to the strict textbook definition
    # --- RSI Reversion strategy (independent, simple): RSI extreme -> confirmed
    # reentry -> target back near RSI-50 (proxied by price returning to its own
    # N-period average). Deliberately no tight stop, only a wide catastrophic
    # one, on the assumption overbought/oversold eventually rebalances. ---
    rsi_rev_overbought: float = 80.0
    rsi_rev_oversold: float = 20.0
    rsi_rev_min_extreme_candles: int = 3  # min candles RSI must stay beyond 80/20 before reentry counts
    rsi_rev_catastrophic_atr_mult: float = 6.0  # wide safety stop, only for extreme/structural cases
    # --- Grid Bot (independent strategy: range/laterale trading) ---
    grid_enabled: bool = True
    grid_timeframe: str = "1h"  # timeframe used for range detection, ATR and spacing
    grid_bb_period: int = 20
    grid_bb_std: float = 2.0
    grid_max_bb_width_pct: float = 3.0  # market considered "laterale" if BB width <= this % of price
    grid_ema_fast: int = 9
    grid_ema_slow: int = 21
    grid_max_ema_gap_pct: float = 0.5  # EMA fast/slow must be within this % of each other (flat trend)
    grid_atr_period: int = 14
    grid_num_levels: int = 4  # wide/sparse grid: few levels below the center price
    grid_atr_spacing_mult: float = 1.8  # distance between grid levels = this * ATR
    grid_range_break_atr_mult: float = 3.0  # emergency stop: price this many ATR below the lowest level
    grid_max_pairs: int = 4  # max symbols with an active grid at once
    grid_replace_improvement_pct: float = 20.0  # candidate must be this much MORE lateral (lower bb_width+ema_gap) than the worst idle active grid to replace it


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
    strategy: str = "counter_trend"  # "counter_trend" | "fvg_reversal"
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
    strategy: str = "counter_trend"
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
    strategy: str = "counter_trend"


class ExchangeConnectRequest(BaseModel):
    api_key: str
    api_secret: str
    api_passphrase: Optional[str] = None  # unused for Bybit; kept for compatibility


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


def detect_trend_exhaustion(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rsis: list[Optional[float]],
    trend: str,  # "up" or "down"
    cfg: Config,
) -> dict[str, Any]:
    """Trend Exhaustion Score (additive check, COUNTER-TREND strategies only:
    "counter_trend" / Rev Pre-FVG and "fvg_reversal" / FVG Reversal). Measures
    whether the trend is genuinely running out of steam, BEFORE the reversal
    candle pattern is searched. Does not touch "scoring" or "impulse_fvg".

    Checks 4 independent conditions over the last `cfg.exhaustion_lookback`
    candles (1 point each), speculare for "up" (looking for bearish exhaustion)
    and "down" (looking for bullish exhaustion):
      1) Candles shrinking (avg body last 3 < avg body of the 3 before that).
      2) Volume fading while price still advances in the trend direction.
      3) RSI swings losing strength (lower highs on "up", higher lows on "down").
      4) A long rejection wick against the trend on one of the last 2 candles.

    Returns {"confirmed": bool, "score": float, "signals": [str]}.
    """
    signals: list[str] = []
    n = len(closes)
    lb = max(6, cfg.exhaustion_lookback)
    if n < lb + 2:
        return {"confirmed": False, "score": 0.0, "signals": signals}

    w_opens = opens[-lb:]
    w_highs = highs[-lb:]
    w_lows = lows[-lb:]
    w_closes = closes[-lb:]
    w_vols = volumes[-lb:]
    w_rsis = rsis[-lb:]

    # 1) Candles shrinking: avg body of the last 3 vs the 3 candles before that.
    bodies = [abs(w_closes[i] - w_opens[i]) for i in range(len(w_opens))]
    if len(bodies) >= 6:
        recent_avg = sum(bodies[-3:]) / 3
        prior_avg = sum(bodies[-6:-3]) / 3
        if prior_avg > 0 and recent_avg < prior_avg * 0.7:
            signals.append("Candele in Contrazione")

    # 2) Volume fading while price still advances in the trend direction.
    if len(w_vols) >= 6 and len(w_closes) >= 4:
        recent_vol_avg = sum(w_vols[-3:]) / 3
        prior_vol_avg = sum(w_vols[-6:-3]) / 3
        price_advancing = (
            w_closes[-1] > w_closes[-4] if trend == "up" else w_closes[-1] < w_closes[-4]
        )
        if price_advancing and prior_vol_avg > 0 and recent_vol_avg < prior_vol_avg:
            signals.append("Volume in Calo")

    # 3) RSI swings losing strength: lower highs (up) / higher lows (down),
    # reusing detect_pivots on the closes within this same window.
    sub_window = max(2, min(cfg.pivot_window, lb // 2 - 1))
    if sub_window >= 2 and len(w_closes) >= (2 * sub_window + 1):
        lows_idx, highs_idx = detect_pivots(w_closes, sub_window)
        if trend == "up" and len(highs_idx) >= 2:
            i1, i2 = highs_idx[-2], highs_idx[-1]
            r1, r2 = w_rsis[i1], w_rsis[i2]
            if r1 is not None and r2 is not None and w_closes[i2] >= w_closes[i1] and r2 < r1:
                signals.append("RSI Picchi Decrescenti")
        elif trend == "down" and len(lows_idx) >= 2:
            i1, i2 = lows_idx[-2], lows_idx[-1]
            r1, r2 = w_rsis[i1], w_rsis[i2]
            if r1 is not None and r2 is not None and w_closes[i2] <= w_closes[i1] and r2 > r1:
                signals.append("RSI Minimi Crescenti")

    # 4) Long rejection wick against the trend, on one of the last 2 candles.
    ratio = cfg.reversal_rejection_wick_ratio
    for i in range(max(0, len(w_opens) - 2), len(w_opens)):
        o, h, l, c = w_opens[i], w_highs[i], w_lows[i], w_closes[i]
        body = abs(c - o)
        if body <= 0:
            body = (h - l) * 0.25 or 1e-9
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        if trend == "up" and upper_wick >= ratio * body and upper_wick > lower_wick:
            signals.append("Candela di Rifiuto")
            break
        if trend == "down" and lower_wick >= ratio * body and lower_wick > upper_wick:
            signals.append("Candela di Rifiuto")
            break

    score = float(len(signals))
    confirmed = score >= cfg.exhaustion_min_score
    return {"confirmed": confirmed, "score": score, "signals": signals}




# ---------------------------------------------------------------------------
# KuCoin client
# ---------------------------------------------------------------------------
def _bybit_category(trading_mode: str) -> str:
    """Map the bot's trading_mode to a Bybit v5 market category."""
    return "linear" if trading_mode == "leverage" else "spot"


class BybitClient:
    """Bybit EU v5 public market-data client. Returns data normalized to the
    same shapes the rest of the bot expects (drop-in for the old KuCoin client):
      - get_symbols(): [{symbol, quoteCurrency, enableTrading}]
      - get_tickers(): [{symbol, last, volValue}]
      - get_klines():  [[time_s, open, close, high, low, volume], ...] ascending
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=BYBIT_BASE, timeout=15.0)
        self._sema = asyncio.Semaphore(15)
        self.category = "spot"  # updated from paper trading_mode

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
                        logger.warning("Bybit GET failed %s: %s", path, e)
                        return None
                    await asyncio.sleep(0.5)
            return None

    async def get_symbols(self) -> list[dict[str, Any]]:
        data = await self._get(
            "/v5/market/instruments-info", params={"category": self.category}
        )
        if not data or data.get("retCode") != 0:
            return []
        out: list[dict[str, Any]] = []
        for it in data.get("result", {}).get("list", []):
            out.append({
                "symbol": it.get("symbol"),
                "quoteCurrency": it.get("quoteCoin"),
                "enableTrading": it.get("status") == "Trading",
            })
        return out

    async def get_tickers(self) -> list[dict[str, Any]]:
        data = await self._get(
            "/v5/market/tickers", params={"category": self.category}
        )
        if not data or data.get("retCode") != 0:
            return []
        out: list[dict[str, Any]] = []
        for t in data.get("result", {}).get("list", []):
            out.append({
                "symbol": t.get("symbol"),
                "last": t.get("lastPrice"),
                "volValue": t.get("turnover24h"),  # 24h quote volume
                "changeRate": t.get("price24hPcnt"),  # 24h change (fraction)
            })
        return out

    async def get_klines(self, symbol: str, tf: str) -> list[list[float]]:
        bybit_tf = TF_MAP.get(tf)
        if not bybit_tf:
            return []
        data = await self._get(
            "/v5/market/kline",
            params={
                "category": self.category,
                "symbol": symbol,
                "interval": bybit_tf,
                "limit": CANDLE_LIMIT + 1,
            },
        )
        if not data or data.get("retCode") != 0:
            return []
        # Bybit returns newest-first: [start_ms, open, high, low, close, volume, turnover]
        raw = data.get("result", {}).get("list", [])
        raw = list(reversed(raw))  # ascending by time
        # Drop the last, still-forming candle so signal logic and chart rendering
        # both reference the same CLOSED candles.
        if len(raw) > 1:
            raw = raw[:-1]
        candles: list[list[float]] = []
        for row in raw[-CANDLE_LIMIT:]:
            try:
                candles.append([
                    float(row[0]) / 1000.0,  # time (ms -> s)
                    float(row[1]),           # open
                    float(row[4]),           # close
                    float(row[2]),           # high
                    float(row[3]),           # low
                    float(row[5]),           # volume
                ])
            except (ValueError, IndexError):
                continue
        return candles


exchange = BybitClient()


# ---------------------------------------------------------------------------
# Real-time price feed via Bybit v5 public WebSocket
# ---------------------------------------------------------------------------
class PriceFeed:
    """Maintains a live cache of last prices via Bybit v5 public WS.

    Subscribes to the tickers of currently open paper positions so SL/TP can
    trigger with minimal delay. Falls back to REST if the socket drops.
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

    async def desired_symbols(self) -> list[str]:
        docs = await db.paper_positions.find({}, {"symbol": 1, "_id": 0}).to_list(1000)
        scalp_docs = await db.scalping_positions.find(
            {"status": "open"}, {"symbol": 1, "_id": 0}
        ).to_list(1000)
        grid_docs = await db.grid_instances.find(
            {"status": "active"}, {"symbol": 1, "_id": 0}
        ).to_list(1000)
        return sorted(
            {d["symbol"] for d in docs}
            | {d["symbol"] for d in scalp_docs}
            | {d["symbol"] for d in grid_docs}
        )

    def _ws_url(self) -> str:
        return f"{BYBIT_WS_PUBLIC}/{exchange.category}"

    async def run(self) -> None:
        import websockets  # local import, dep added

        while True:
            url = self._ws_url()
            try:
                async with websockets.connect(url, ping_interval=None) as ws:
                    self._ws = ws
                    self._connected = True
                    self._subscribed.clear()
                    logger.info("Bybit WS connected: %s", url)
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    sub_task = asyncio.create_task(self._resub_loop(ws))
                    try:
                        async for raw in ws:
                            self._handle(raw)
                    finally:
                        ping_task.cancel()
                        sub_task.cancel()
            except Exception as e:  # noqa: BLE001
                logger.warning("Bybit WS error, reconnecting: %s", e)
            self._connected = False
            self._ws = None
            await asyncio.sleep(3)

    async def _ping_loop(self, ws: Any) -> None:
        import json as _json
        while True:
            await asyncio.sleep(20)
            try:
                await ws.send(_json.dumps({"op": "ping"}))
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
                if to_add:
                    await ws.send(_json.dumps({
                        "op": "subscribe",
                        "args": [f"tickers.{s}" for s in to_add],
                    }))
                    self._subscribed |= to_add
                if to_remove:
                    await ws.send(_json.dumps({
                        "op": "unsubscribe",
                        "args": [f"tickers.{s}" for s in to_remove],
                    }))
                    self._subscribed -= to_remove
            except Exception:
                return
            await asyncio.sleep(3)

    def _handle(self, raw: str) -> None:
        import json as _json
        try:
            msg = _json.loads(raw)
        except (ValueError, TypeError):
            return
        topic = msg.get("topic", "")
        if not topic.startswith("tickers."):
            return
        symbol = topic.split(".", 1)[1]
        data = msg.get("data", {})
        # spot pushes full snapshots; linear pushes deltas that may omit lastPrice
        price = data.get("lastPrice")
        if price is None:
            return
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
        try:
            async with httpx.AsyncClient(base_url=BYBIT_BASE, timeout=8.0) as c:
                r = await c.get(
                    "/v5/market/tickers",
                    params={"category": exchange.category, "symbol": symbol},
                )
                d = r.json()
                if d.get("retCode") == 0 and d.get("result", {}).get("list"):
                    return float(d["result"]["list"][0]["lastPrice"])
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
    else:
        cfg = PaperConfig(**doc)
    # Keep the Bybit market category in sync with the bot's trading mode.
    exchange.category = _bybit_category(cfg.trading_mode)
    return cfg


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


# ---------------------------------------------------------------------------
# Per-strategy fund isolation ("cross" by default, "isolated" on request) for
# the three traditional (non-Scalping, non-Grid) strategies. By default a
# strategy has no dedicated wallet and draws from/settles to the SHARED main
# paper wallet (get_paper_cash/set_paper_cash above) exactly as before. If the
# user allocates funds to a specific strategy, that strategy gets its own
# isolated cash pool and stops touching the shared one, until deallocated.
# ---------------------------------------------------------------------------
STRATEGY_WALLET_NAMES = ("counter_trend", "fvg_reversal", "rsi_reversion")


async def get_strategy_wallet(strategy: str) -> Optional[dict[str, Any]]:
    """Returns None if this strategy has no dedicated wallet (i.e. it's on
    the shared pool). Returns the wallet doc (with 'cash') if isolated."""
    doc = await db.strategy_wallets.find_one({"_id": strategy}, {"_id": 0})
    if not doc or not doc.get("allocated"):
        return None
    return doc


async def get_effective_cash(strategy: Optional[str]) -> float:
    if strategy in STRATEGY_WALLET_NAMES:
        w = await get_strategy_wallet(strategy)
        if w is not None:
            return w.get("cash", 0.0)
    return await get_paper_cash()


async def adjust_effective_cash(strategy: Optional[str], delta: float) -> None:
    """Credit/debit the right pool for this strategy: its isolated wallet if
    it has one, otherwise the shared main paper wallet."""
    if strategy in STRATEGY_WALLET_NAMES:
        w = await get_strategy_wallet(strategy)
        if w is not None:
            new_cash = w.get("cash", 0.0) + delta
            await db.strategy_wallets.update_one(
                {"_id": strategy}, {"$set": {"cash": new_cash}}
            )
            return
    cash = await get_paper_cash()
    await set_paper_cash(cash + delta)


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
    strategy = signal.get("strategy")
    cash = await get_effective_cash(strategy)

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
        await adjust_effective_cash(strategy, -notional)  # lock cash on open

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
        strategy=signal.get("strategy", "counter_trend"),
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
    strategy = pos.get("strategy", "counter_trend")
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
        strategy=strategy,
    )
    await db.paper_trades.insert_one(trade.model_dump())
    await db.paper_positions.delete_one({"id": pos["id"]})
    if pcfg.trading_mode == "spot" and pos["side"] == "long":
        # Unlock notional and add PnL: cash += exit * qty
        await adjust_effective_cash(strategy, exit_price * qty)
    else:
        await adjust_effective_cash(strategy, pnl)
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
    """Maker/taker fee rates. Bybit spot/linear default taker is ~0.1% / 0.055%.
    For paper trading we use the configured default_fee_rate; real per-symbol
    fees via the signed Bybit API are wired in the execution phase."""
    if symbol in _fee_cache:
        return _fee_cache[symbol]
    maker = taker = cfg.default_fee_rate
    _fee_cache[symbol] = (maker, taker)
    return maker, taker


async def get_funding_rate(symbol: str, cfg: Config) -> float:
    """Current funding rate via Bybit v5 (linear only). Spot has no funding.
    Falls back to last known value (or 0); never blocks execution."""
    if exchange.category != "linear":
        return 0.0
    try:
        async with httpx.AsyncClient(base_url=BYBIT_BASE, timeout=8.0) as c:
            r = await c.get(
                "/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
            )
            data = r.json()
            if data.get("retCode") == 0 and data.get("result", {}).get("list"):
                val = float(data["result"]["list"][0].get("fundingRate") or 0.0)
                _funding_cache[symbol] = val
                return val
    except Exception as e:  # noqa: BLE001
        logger.warning("Funding rate unavailable for %s (%s); using last known", symbol, e)
    return _funding_cache.get(symbol, 0.0)


async def _current_spread(symbol: str) -> float:
    """Approx bid-ask spread from Bybit v5 orderbook (best bid/ask)."""
    try:
        async with httpx.AsyncClient(base_url=BYBIT_BASE, timeout=8.0) as c:
            r = await c.get(
                "/v5/market/orderbook",
                params={"category": exchange.category, "symbol": symbol, "limit": 1},
            )
            d = r.json()
            if d.get("retCode") == 0 and d.get("result"):
                res = d["result"]
                bid = float(res["b"][0][0]) if res.get("b") else 0.0
                ask = float(res["a"][0][0]) if res.get("a") else 0.0
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
    candles = await exchange.get_klines(pos["symbol"], tf)
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
    strategy = pos.get("strategy", "counter_trend")
    trade = PaperTrade(
        signal_id=pos["signal_id"], symbol=pos["symbol"], side=pos["side"],
        entry=entry, exit=round(price, 8), quantity=round(close_qty, 8),
        pnl_usdt=round(pnl, 2), pnl_pct=round(pnl_pct, 2), outcome=outcome,
        opened_at=pos["opened_at"], closed_at=datetime.now(timezone.utc).isoformat(),
        strategy=strategy,
    )
    await db.paper_trades.insert_one(trade.model_dump())
    if pcfg.trading_mode == "spot" and pos["side"] == "long":
        await adjust_effective_cash(strategy, price * close_qty)
    else:
        await adjust_effective_cash(strategy, pnl)
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
    candles = await exchange.get_klines(symbol, tf)
    if len(candles) < 2:
        return None
    return candles[-2][2]






async def manage_counter_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """Pre-FVG reversal execution: close tp1_pct at TP1 on candle-close; keep the
    ORIGINAL ATR stop until price advances post_tp1_advance_pct beyond TP1, then
    move the stop to TP1 (net of fees); TP2 closes the remainder."""
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
            # Keep the ORIGINAL ATR stop (do NOT move to breakeven yet).
            await db.paper_positions.update_one(
                {"id": pos["id"]}, {"$set": {"quantity": round(remain, 8)}}
            )
            pos = {**pos, "partial_closed": True, "quantity": remain}
        return pos

    # After TP1: move the stop to TP1 (net fees) only after +post_tp1_advance_pct
    # beyond TP1. If that advance never happens, the original ATR stop stays.
    if not pos.get("breakeven_active"):
        advance = tp1 * (cfg.post_tp1_advance_pct / 100)
        reached = (price >= tp1 + advance) if long else (price <= tp1 - advance)
        if reached:
            maker, taker = await get_trade_fees(pos["symbol"], cfg)
            fee_cost = tp1 * (maker + taker)
            new_stop = tp1 + fee_cost if long else tp1 - fee_cost
            await db.paper_positions.update_one(
                {"id": pos["id"]},
                {"$set": {"current_stop": round(new_stop, 8), "breakeven_active": True}},
            )
            pos = {**pos, "current_stop": new_stop, "breakeven_active": True}

    # TP2 closes the remainder (also handled on live price in the monitor).
    if tp2 > 0:
        hit_tp2 = last_close >= tp2 if long else last_close <= tp2
        if hit_tp2:
            await close_paper_position(pos, tp2, "win")
            return {**pos, "quantity": 0}
    return pos




async def manage_fvg_reversal_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """FVG Reversal execution: TP1 (fvgr_tp1_pct) partial on candle close; keep
    the structure stop until price advances fvgr_post_tp1_advance_pct beyond TP1,
    then move stop to TP1 (net fees); trailing fvgr_trailing_pct once in profit;
    TP2 closes the remainder."""
    tf = pos.get("timeframe", "1h")
    tp1 = float(pos.get("tp1") or 0)
    tp2 = float(pos.get("tp2") or 0)
    long = pos["side"] == "long"
    last_close = await _last_closed_close(pos["symbol"], tf)
    if last_close is None:
        return pos

    if not pos.get("partial_closed"):
        hit = last_close >= tp1 if long else last_close <= tp1
        if tp1 > 0 and hit:
            remain = await _close_fraction(pos, tp1, cfg.fvgr_tp1_pct / 100, "tp1")
            await db.paper_positions.update_one(
                {"id": pos["id"]}, {"$set": {"quantity": round(remain, 8)}}
            )
            pos = {**pos, "partial_closed": True, "quantity": remain}
    else:
        if not pos.get("breakeven_active"):
            adv = tp1 * (cfg.fvgr_post_tp1_advance_pct / 100)
            reached = (price >= tp1 + adv) if long else (price <= tp1 - adv)
            if reached:
                maker, taker = await get_trade_fees(pos["symbol"], cfg)
                fee = tp1 * (maker + taker)
                ns = tp1 + fee if long else tp1 - fee
                await db.paper_positions.update_one(
                    {"id": pos["id"]},
                    {"$set": {"current_stop": round(ns, 8), "breakeven_active": True}},
                )
                pos = {**pos, "current_stop": ns, "breakeven_active": True}

    # Trailing stop fvgr_trailing_pct from price, active once in profit.
    entry = float(pos.get("fill_price") or pos.get("entry") or 0)
    in_profit = (price > entry) if long else (price < entry)
    if entry > 0 and in_profit and cfg.fvgr_trailing_pct > 0:
        cur = float(pos.get("current_stop") or pos.get("stop_loss") or 0)
        trail = price * (1 - cfg.fvgr_trailing_pct / 100) if long else price * (1 + cfg.fvgr_trailing_pct / 100)
        ns = max(cur, trail) if long else min(cur, trail)
        if (long and ns > cur) or ((not long) and ns < cur):
            await db.paper_positions.update_one(
                {"id": pos["id"]}, {"$set": {"current_stop": round(ns, 8)}}
            )
            pos = {**pos, "current_stop": ns}

    if tp2 > 0 and pos.get("partial_closed"):
        hit2 = last_close >= tp2 if long else last_close <= tp2
        if hit2:
            await close_paper_position(pos, tp2, "win")
            return {**pos, "quantity": 0}
    return pos


async def manage_open_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """Run the 3 additive managers + partial close. Returns the possibly-updated
    position dict (with fresh current_stop)."""
    updates: dict[str, Any] = {}
    # Pre-FVG reversal strategy has its own TP1/TP2 + post-TP1 stop path.
    if pos.get("strategy") == "counter_trend":
        return await manage_counter_position(pos, price, cfg)
    # FVG Reversal strategy: TP1/TP2 + post-TP1 stop + trailing.
    if pos.get("strategy") == "fvg_reversal":
        return await manage_fvg_reversal_position(pos, price, cfg)
    # RSI Reversion: plain SL/TP only, on purpose — no partial close, no
    # timeout-to-breakeven, no trailing. The strategy's whole premise is
    # "wait for the rebalance"; the additive managers would fight that by
    # cutting the trade early. Only the wide catastrophic stop protects it.
    if pos.get("strategy") == "rsi_reversion":
        return pos
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
        tickers = await exchange.get_tickers()
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
        if pos.get("strategy") in ("counter_trend", "fvg_reversal"):
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


# ===========================================================================
# STRATEGY 2: Impulse FVG + Consolidation + Multi-TP (additive, selectable)
# ===========================================================================
def detect_market_structure(candles: list[list[float]], window: int, strict: bool = True) -> str:
    """Return 'up' (HH+HL, or just HH/HL alone when `strict=False`), 'down'
    (LH+LL, or just LH/LL alone when `strict=False`) or 'range' from swing
    structure. Non-strict mode exists because requiring BOTH conditions at
    once is the textbook definition, but real markets constantly have one
    leg confirmed before the other (e.g. a fresh higher high during a
    shallow pullback that hasn't yet made a higher low) — which the strict
    version classifies as 'range' even though the market is clearly still
    trending."""
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
    if strict:
        if hh and hl:
            return "up"
        if lh and ll:
            return "down"
    else:
        if hh or hl:
            return "up"
        if lh or ll:
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


def detect_stepped_rejection_pattern(opens, highs, lows, closes, against: str) -> Optional[str]:
    """Alternative reversal signal (checked alongside detect_reversal_pattern,
    not instead of it): the last 3 candles each show a genuine, long rejection
    wick against the trend (sellers/buyers still pushing hard each time), BUT
    each candle's extreme is less far than the last — higher lows during a
    downtrend (bullish case) or lower highs during an uptrend (bearish case).
    This is buyers/sellers stepping in progressively earlier each time, a
    concrete footprint of strengthening opposition right before a reversal —
    distinct from a pattern of merely shrinking wicks, which would also
    accept fading effort with no clear directional structure."""
    if len(closes) < 3:
        return None
    idx = (-3, -2, -1)
    if against == "bearish":  # trend UP -> want LOWER HIGHS with long upper-wick rejection
        extremes = [highs[i] for i in idx]
        spikes = [highs[i] - max(opens[i], closes[i]) for i in idx]
        bodies = [abs(closes[i] - opens[i]) for i in idx]
        stepping = extremes[0] > extremes[1] > extremes[2]
        label = "Massimi Decrescenti con Rifiuto"
    else:  # trend DOWN -> want HIGHER LOWS with long lower-wick rejection
        extremes = [lows[i] for i in idx]
        spikes = [min(opens[i], closes[i]) - lows[i] for i in idx]
        bodies = [abs(closes[i] - opens[i]) for i in idx]
        stepping = extremes[0] < extremes[1] < extremes[2]
        label = "Minimi Crescenti con Rifiuto"
    # "long" wick = at least as long as the candle's own body (avoids
    # accepting doji-like candles with a barely-there wick as "rejection").
    long_enough = all(s > 0 and s >= b for s, b in zip(spikes, bodies))
    if stepping and long_enough:
        return label
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
    """Pre-FVG reversal strategy (spec-exact, replaces old counter-trend).

    Sequence: an impulse leaves an unfilled FVG -> price consolidates in a
    tight box -> a reversal pattern (candlestick, OR three candles with
    higher-lows/lower-highs + rejection wicks) forms at the end of the box.
    Trade direction is AGAINST the higher-timeframe trend (same reasoning as
    FVG Reversal) — the reversal pattern itself is the trigger; no separate
    wait for price to close beyond the box is required anymore, since the
    stepped-rejection pattern already IS the directional signal, and waiting
    for an extra breakout candle on top of it was rarely satisfied.
    Target = the OPPOSITE (far) edge of the FVG left behind by the impulse
    the reversal contradicts. Confirmations: volume spike + RSI filters
    (HTF extreme, momentum turn, divergence coherent with entry).
    """
    STRAT = "counter_trend"
    candles = await exchange.get_klines(symbol, tf)
    if len(candles) < 60:
        await log_reject(symbol, tf, STRAT, "dati insufficienti")
        return None
    opens = [c[1] for c in candles]
    closes = [c[2] for c in candles]
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    vols = [c[5] for c in candles]

    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if not atr or atr <= 0:
        await log_reject(symbol, tf, STRAT, "ATR non calcolabile")
        return None

    # Trend context from the HIGHER timeframe (same reasoning as FVG
    # Reversal): the box's direction is judged one level up, not on the
    # scanning timeframe itself, which reads as "range" far too often.
    htf = _higher_tf(tf)
    hcandles = await exchange.get_klines(symbol, htf)
    if len(hcandles) < 20:
        await log_reject(symbol, tf, STRAT, "dati insufficienti sul timeframe alto")
        return None
    structure = detect_market_structure(hcandles, cfg.pivot_window, strict=cfg.trend_structure_strict)
    if structure == "range":
        await log_reject(symbol, tf, STRAT, "trend non definito (mercato laterale)")
        return None
    trend = structure  # 'up' or 'down'
    side = "short" if trend == "up" else "long"  # AGAINST the trend

    # Step 1: consolidation box = the last K candles (tightness context).
    k = cfg.consolidation_min_candles
    if len(candles) < k + 1:
        await log_reject(symbol, tf, STRAT, "dati insufficienti")
        return None
    box = candles[-k:]
    box_high = max(c[3] for c in box)
    box_low = min(c[4] for c in box)
    if (box_high - box_low) > cfg.consolidation_max_atr * atr:
        await log_reject(symbol, tf, STRAT, "consolidamento troppo ampio")
        return None

    # --- Trend Exhaustion Score (additive, before the reversal-pattern search) ---
    rsis = rsi_wilder(closes, cfg.rsi_period)
    exhaustion = detect_trend_exhaustion(
        opens, highs, lows, closes, vols, rsis, trend, cfg
    )
    if not exhaustion["confirmed"]:
        await log_reject(symbol, tf, STRAT, "esaurimento trend non confermato")
        return None

    # Step 2: reversal pattern AT THE END OF the box — this is now the
    # trigger itself, not a confirmation of an already-happened breakout.
    # Either the classic candlestick pattern, OR three consecutive candles
    # with higher-lows/lower-highs + a genuine rejection wick each time.
    against = "bullish" if side == "long" else "bearish"
    box_opens, box_highs = [c[1] for c in box], [c[3] for c in box]
    box_lows, box_closes = [c[4] for c in box], [c[2] for c in box]
    pattern = detect_reversal_pattern(box_opens, box_highs, box_lows, box_closes, against)
    if pattern is None:
        pattern = detect_stepped_rejection_pattern(box_opens, box_highs, box_lows, box_closes, against)
    if pattern is None:
        await log_reject(symbol, tf, STRAT, "pattern di inversione non trovato")
        return None

    # Step 3: the impulse FVG the reversal contradicts, in the trade direction.
    #   long  -> a BEARISH FVG above  (impulse was down; price fills upward)
    #   short -> a BULLISH FVG below  (impulse was up; price fills downward)
    entry = closes[-1]
    fvgs = detect_all_fvgs(highs, lows, cfg.fvg_lookback)
    if side == "long":
        targets = [f for f in fvgs if f["kind"] == "bearish" and f["top"] > entry]
        targets.sort(key=lambda f: f["top"])  # nearest far-edge first
    else:
        targets = [f for f in fvgs if f["kind"] == "bullish" and f["bottom"] < entry]
        targets.sort(key=lambda f: -f["bottom"])
    if not targets:
        await log_reject(symbol, tf, STRAT, "nessuna FVG target trovata")
        return None
    target_fvg = targets[0]
    far_edge = target_fvg["top"] if side == "long" else target_fvg["bottom"]
    midpoint = (target_fvg["top"] + target_fvg["bottom"]) / 2

    # TP2 = far (opposite) edge of the FVG; TP1 = an intermediate level inside it.
    tp2 = far_edge
    tp1 = midpoint
    if side == "long":
        if tp1 <= entry:
            tp1 = entry + (tp2 - entry) * 0.5
        if tp2 <= entry:
            await log_reject(symbol, tf, STRAT, "target non valido")
            return None
    else:
        if tp1 >= entry:
            tp1 = entry - (entry - tp2) * 0.5
        if tp2 >= entry:
            await log_reject(symbol, tf, STRAT, "target non valido")
            return None

    # Step 4: stop beyond the consolidation box (ATR/structure buffer).
    sl_buffer = max(cfg.atr_sl_multiplier * atr, entry * (cfg.sl_padding_pct / 100))
    stop_loss = (box_low - sl_buffer) if side == "long" else (box_high + sl_buffer)
    risk = abs(entry - stop_loss)
    if risk <= 0:
        await log_reject(symbol, tf, STRAT, "rischio non valido")
        return None
    est_rr = abs(tp2 - entry) / risk
    if est_rr < cfg.min_rr_ratio:
        await log_reject(symbol, tf, STRAT, "R:R insufficiente")
        return None

    # Step 5: confirmations — volume spike + RSI filters.
    vol_ratio = volume_spike_ratio(vols, cfg.volume_ma_period)
    if vol_ratio < cfg.volume_spike_multiplier:
        await log_reject(symbol, tf, STRAT, "volume insufficiente")
        return None
    # (a) higher-TF extreme (oversold for long / overbought for short) —
    # reuses the hcandles already fetched above for the trend read.
    hrsis = rsi_wilder([c[2] for c in hcandles], cfg.rsi_period) if len(hcandles) > cfg.rsi_period else []
    hval = next((r for r in reversed(hrsis) if r is not None), None)
    if hval is None:
        await log_reject(symbol, tf, STRAT, "RSI timeframe alto non disponibile")
        return None
    if side == "long" and hval > cfg.rsi_high_tf_os:
        await log_reject(symbol, tf, STRAT, "RSI timeframe alto non in ipervenduto")
        return None
    if side == "short" and hval < cfg.rsi_high_tf_ob:
        await log_reject(symbol, tf, STRAT, "RSI timeframe alto non in ipercomprato")
        return None
    # (b) momentum turn on the trade timeframe
    if not _rsi_momentum_turn(rsis, side, cfg.rsi_overbought, cfg.rsi_oversold):
        await log_reject(symbol, tf, STRAT, "RSI non in inversione di momentum")
        return None
    # (c) divergence coherent with the trade direction
    div = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if (side == "long" and div != "bullish") or (side == "short" and div != "bearish"):
        await log_reject(symbol, tf, STRAT, "divergenza RSI non coerente")
        return None

    return Signal(
        symbol=symbol, timeframe=tf, side=side,
        entry=round(entry, 8), stop_loss=round(stop_loss, 8),
        take_profit=round(tp1, 8), rr_ratio=cfg.rr_ratio,
        confirmations=["Box Ristretto", pattern, "Volume Spike",
                       "RSI HTF Extreme", "RSI Momentum Turn", "RSI Divergence"]
                       + exhaustion["signals"],
        strength=6, score=0.0, max_score=0.0,
        strategy="counter_trend",
        tp1=round(tp1, 8), tp2=round(tp2, 8),
        consolidation_high=round(box_high, 8),
        consolidation_low=round(box_low, 8),
        rsi_value=round(next((r for r in reversed(rsis) if r is not None), 0) or 0, 2),
        volume_ratio=round(vol_ratio, 2),
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=round(target_fvg["top"], 8), fvg_bottom=round(target_fvg["bottom"], 8),
        atr=round(atr, 8), atr_multiplier=cfg.atr_sl_multiplier,
    )


async def log_reject(symbol: str, tf: str, strategy: str, reason: str) -> None:
    """Records why a candidate setup was discarded, WITHOUT waiting for it —
    fire-and-forget so it never slows down the scan. Used to find the real
    bottleneck across the three strategies instead of guessing at thresholds."""
    try:
        await db.strategy_debug_log.insert_one({
            "id": str(uuid.uuid4()),
            "symbol": symbol,
            "timeframe": tf,
            "strategy": strategy,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:  # noqa: BLE001
        pass


async def prune_diagnostic_logs() -> None:
    """Keep the diagnostic logs bounded — they exist to spot RECENT
    bottlenecks (the debug log only ever reports on a rolling 3h window
    anyway), not to be kept forever. `log_reject` fires on EVERY rejected
    candidate on EVERY 1-minute scan (~hundreds of writes/minute across all
    pairs/timeframes/strategies) — left unchecked this filled the entire
    database quota and blocked ALL writes app-wide (paper trades, wallets,
    Settings — everything). Timestamps are stored as ISO-8601 strings, not
    native dates, so this uses a string-range delete rather than a MongoDB
    TTL index (which only works on native Date fields)."""
    now = datetime.now(timezone.utc)
    try:
        cutoff = (now - timedelta(hours=6)).isoformat()
        await db.strategy_debug_log.delete_many({"at": {"$lt": cutoff}})
    except Exception as e:  # noqa: BLE001
        logger.warning("prune strategy_debug_log failed: %s", e)
    try:
        cutoff2 = (now - timedelta(days=3)).isoformat()
        await db.entry_timing_log.delete_many({"signal_created_at": {"$lt": cutoff2}})
    except Exception as e:  # noqa: BLE001
        logger.warning("prune entry_timing_log failed: %s", e)


async def log_prune_loop() -> None:
    """Runs the diagnostic-log cleanup every 30 minutes, forever."""
    while True:
        try:
            await prune_diagnostic_logs()
        except Exception as e:  # noqa: BLE001
            logger.warning("log_prune_loop error: %s", e)
        await asyncio.sleep(1800)


scan_state = ScanState()


async def analyze_pair_fvg_reversal(symbol: str, tf: str, cfg: Config) -> Optional[Signal]:
    """FVG Reversal (independent strategy): the FVG forms WITH the trend from a
    strong impulse; the bot trades AGAINST the trend on the retracement back
    toward that FVG. Entry = a reversal candle pattern during the retracement;
    target = inside the trend FVG. Uses its own `fvgr_*` parameters.

    Trend context is read from the HIGHER timeframe (e.g. scanning on 1h ->
    trend judged on 4h), not the scanning timeframe itself: on a short
    timeframe the market reads as "range" far more often even when there is
    a real trend one step up, which was starving this strategy of setups."""
    STRAT = "fvg_reversal"
    candles = await exchange.get_klines(symbol, tf)
    if len(candles) < 60:
        await log_reject(symbol, tf, STRAT, "dati insufficienti")
        return None
    opens = [c[1] for c in candles]
    closes = [c[2] for c in candles]
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]

    # Fetch the higher-timeframe candles once, upfront — used both for the
    # trend read below AND the RSI HTF filters further down.
    htf = _higher_tf(tf)
    hcandles = await exchange.get_klines(symbol, htf)
    if len(hcandles) < 20:
        await log_reject(symbol, tf, STRAT, "dati insufficienti sul timeframe alto")
        return None

    structure = detect_market_structure(hcandles, cfg.pivot_window, strict=cfg.trend_structure_strict)
    if structure == "range":
        await log_reject(symbol, tf, STRAT, "trend non definito (mercato laterale)")
        return None
    trend = structure  # 'up' or 'down'
    entry_side = "short" if trend == "up" else "long"  # AGAINST the trend

    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if not atr or atr <= 0:
        await log_reject(symbol, tf, STRAT, "ATR non calcolabile")
        return None

    # Impulse FVG in the TREND direction (most significant = largest gap).
    fvgs = detect_all_fvgs(highs, lows, cfg.fvg_lookback)
    trend_kind = "bullish" if trend == "up" else "bearish"
    impulse_fvgs = [f for f in fvgs if f["kind"] == trend_kind]
    if not impulse_fvgs:
        await log_reject(symbol, tf, STRAT, "nessuna FVG di impulso trovata")
        return None
    origin = max(impulse_fvgs, key=lambda f: f["gap"])

    # --- Trend Exhaustion Score (additive, before the reversal-pattern search) ---
    rsis = rsi_wilder(closes, cfg.rsi_period)
    exhaustion = detect_trend_exhaustion(
        opens, highs, lows, closes, [c[5] for c in candles], rsis, trend, cfg
    )
    if not exhaustion["confirmed"]:
        await log_reject(symbol, tf, STRAT, "esaurimento trend non confermato")
        return None

    # Reversal pattern AGAINST the trend during the retracement — either the
    # classic candlestick pattern, or three consecutive shrinking wicks
    # against the trend (fading momentum), same as Rev Pre-FVG.
    against = "bearish" if trend == "up" else "bullish"
    pattern = detect_reversal_pattern(opens, highs, lows, closes, against)
    if pattern is None:
        pattern = detect_stepped_rejection_pattern(opens, highs, lows, closes, against)
    if pattern is None:
        await log_reject(symbol, tf, STRAT, "pattern di inversione non trovato")
        return None

    # RSI filters (independent thresholds) — reuses the HTF candles fetched above.
    hrsis = rsi_wilder([c[2] for c in hcandles], cfg.rsi_period) if len(hcandles) > cfg.rsi_period else []
    hval = next((r for r in reversed(hrsis) if r is not None), None)
    if hval is None:
        await log_reject(symbol, tf, STRAT, "RSI timeframe alto non disponibile")
        return None
    if entry_side == "long" and hval > cfg.fvgr_rsi_high_tf_os:
        await log_reject(symbol, tf, STRAT, "RSI timeframe alto non in ipervenduto")
        return None
    if entry_side == "short" and hval < cfg.fvgr_rsi_high_tf_ob:
        await log_reject(symbol, tf, STRAT, "RSI timeframe alto non in ipercomprato")
        return None
    if not _rsi_momentum_turn(rsis, entry_side, cfg.rsi_overbought, cfg.rsi_oversold):
        await log_reject(symbol, tf, STRAT, "RSI non in inversione di momentum")
        return None
    div = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if (entry_side == "long" and div != "bullish") or (entry_side == "short" and div != "bearish"):
        await log_reject(symbol, tf, STRAT, "divergenza RSI non coerente")
        return None

    entry = closes[-1]
    # SL beyond the IMPULSE EXTREME (never before the FVG zone).
    i = origin["index"]
    seg_hi = max(highs[max(0, i - 2):i + 1])
    seg_lo = min(lows[max(0, i - 2):i + 1])
    sl_buffer = max(cfg.fvgr_atr_sl_multiplier * atr, entry * (cfg.sl_padding_pct / 100))
    mid = (origin["top"] + origin["bottom"]) / 2  # internal FVG sub-zone
    if entry_side == "long":
        stop_loss = seg_lo - sl_buffer
        tp1 = origin["bottom"]  # near edge (first touch of the FVG)
        tp2 = mid               # deeper internal sub-zone
    else:
        stop_loss = seg_hi + sl_buffer
        tp1 = origin["top"]
        tp2 = mid
    risk = abs(entry - stop_loss)
    if risk <= 0:
        await log_reject(symbol, tf, STRAT, "rischio non valido")
        return None
    if (entry_side == "long" and tp1 <= entry) or (entry_side == "short" and tp1 >= entry):
        await log_reject(symbol, tf, STRAT, "target non valido")
        return None
    est_rr = abs(tp1 - entry) / risk
    if est_rr < cfg.fvgr_min_rr_ratio:
        await log_reject(symbol, tf, STRAT, "R:R insufficiente")
        return None

    vol_ratio = volume_spike_ratio([c[5] for c in candles], cfg.volume_ma_period)
    return Signal(
        symbol=symbol, timeframe=tf, side=entry_side,
        entry=round(entry, 8), stop_loss=round(stop_loss, 8),
        take_profit=round(tp1, 8), rr_ratio=cfg.rr_ratio,
        confirmations=["FVG Reversal", pattern, "RSI HTF Extreme",
                       "RSI Momentum Turn", "RSI Divergence"] + exhaustion["signals"],
        strength=5, score=0.0, max_score=0.0,
        strategy="fvg_reversal",
        tp1=round(tp1, 8), tp2=round(tp2, 8),
        consolidation_high=0.0, consolidation_low=0.0,
        rsi_value=round(next((r for r in reversed(rsis) if r is not None), 0) or 0, 2),
        volume_ratio=round(vol_ratio, 2),
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=round(origin["top"], 8), fvg_bottom=round(origin["bottom"], 8),
        atr=round(atr, 8), atr_multiplier=cfg.fvgr_atr_sl_multiplier,
    )


# ===========================================================================
# STRATEGY: RSI Reversion (independent, simple mean-reversion on RSI extremes)
# ===========================================================================
async def analyze_pair_rsi_reversion(symbol: str, tf: str, cfg: Config) -> Optional[Signal]:
    """RSI Reversion: when RSI comes back from an extreme (>80 or <20) after
    spending at least `rsi_rev_min_extreme_candles` candles there, AND the
    reversal is backed by a genuine RSI/price divergence (not just noise),
    enter counter-trend targeting the RSI-50 zone — proxied here by price
    returning to its own N-period average, N = rsi_period, since a full RSI
    inversion has no closed-form price target. Deliberately has NO tight
    stop: only a wide ATR-based catastrophic stop, on the assumption that
    overbought/oversold in spot eventually rebalances — the catastrophic
    stop exists only to cap the rare case where it doesn't (e.g. a
    structural breakdown, not just ordinary volatility)."""
    STRAT = "rsi_reversion"
    candles = await exchange.get_klines(symbol, tf)
    if len(candles) < 60:
        await log_reject(symbol, tf, STRAT, "dati insufficienti")
        return None
    closes = [c[2] for c in candles]
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]

    rsis = rsi_wilder(closes, cfg.rsi_period)
    if rsis[-1] is None:
        await log_reject(symbol, tf, STRAT, "RSI non calcolabile")
        return None

    ob = cfg.rsi_rev_overbought
    os_ = cfg.rsi_rev_oversold
    min_extreme = cfg.rsi_rev_min_extreme_candles

    def consecutive_extreme_before_last(threshold: float, above: bool) -> int:
        """Count consecutive candles, going backwards from the one right
        before the last, that stayed beyond `threshold`."""
        count = 0
        i = len(rsis) - 2
        while i >= 0 and rsis[i] is not None:
            beyond = (rsis[i] >= threshold) if above else (rsis[i] <= threshold)
            if not beyond:
                break
            count += 1
            i -= 1
        return count

    side: Optional[str] = None
    # Filter 1 (candle-close confirmation) + Filter 3 (min time in extreme
    # zone) are both checked here: the reentry must be on THIS closed candle,
    # preceded by enough consecutive candles genuinely beyond the threshold.
    if rsis[-1] < ob and consecutive_extreme_before_last(ob, above=True) >= min_extreme:
        side = "short"
    elif rsis[-1] > os_ and consecutive_extreme_before_last(os_, above=False) >= min_extreme:
        side = "long"
    if side is None:
        await log_reject(symbol, tf, STRAT, "nessun rientro da zona estrema")
        return None

    # Filter 2: genuine RSI/price divergence, not just a brief dip back inside
    # the bands (reuses the same detector as the other strategies).
    divergence = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if side == "short" and divergence != "bearish":
        await log_reject(symbol, tf, STRAT, "divergenza RSI non coerente")
        return None
    if side == "long" and divergence != "bullish":
        await log_reject(symbol, tf, STRAT, "divergenza RSI non coerente")
        return None

    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if not atr or atr <= 0:
        await log_reject(symbol, tf, STRAT, "ATR non calcolabile")
        return None

    entry = closes[-1]
    sma = sum(closes[-cfg.rsi_period:]) / cfg.rsi_period  # proxy for "RSI back to 50"
    catastrophic_buffer = cfg.rsi_rev_catastrophic_atr_mult * atr

    if side == "short":
        if sma >= entry:
            await log_reject(symbol, tf, STRAT, "nessun margine di rientro (prezzo già sulla media)")
            return None  # price already at/below its average, no reversion left to capture
        take_profit = sma
        stop_loss = entry + catastrophic_buffer
    else:
        if sma <= entry:
            await log_reject(symbol, tf, STRAT, "nessun margine di rientro (prezzo già sulla media)")
            return None
        take_profit = sma
        stop_loss = entry - catastrophic_buffer

    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    if risk <= 0 or reward <= 0:
        await log_reject(symbol, tf, STRAT, "target/stop non validi")
        return None

    vol_ratio = volume_spike_ratio([c[5] for c in candles], cfg.volume_ma_period)
    return Signal(
        symbol=symbol, timeframe=tf, side=side,
        entry=round(entry, 8), stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8), rr_ratio=round(reward / risk, 2),
        confirmations=[
            "RSI Ipercomprato" if side == "short" else "RSI Ipervenduto",
            f"{min_extreme}+ candele in zona estrema",
            "Rientro confermato su chiusura",
            "Divergenza RSI/Prezzo",
        ],
        strength=4, score=0.0, max_score=0.0,
        strategy="rsi_reversion",
        tp1=0.0, tp2=0.0,
        consolidation_high=0.0, consolidation_low=0.0,
        rsi_value=round(rsis[-1], 2),
        volume_ratio=round(vol_ratio, 2),
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=0.0, fvg_bottom=0.0,
        atr=round(atr, 8), atr_multiplier=cfg.rsi_rev_catastrophic_atr_mult,
    )


def active_strategies(cfg: Config) -> set[str]:
    """Set of strategies to run this scan, via `enabled_strategies`. Empty
    defaults to both counter-trend strategies ("counter_trend", "fvg_reversal")
    — the "scoring" and "impulse_fvg" strategies were removed."""
    if cfg.enabled_strategies:
        return set(cfg.enabled_strategies)
    return {"counter_trend", "fvg_reversal", "rsi_reversion"}


async def run_scan() -> dict[str, Any]:
    if scan_state.is_scanning:
        return {"skipped": True, "reason": "already scanning"}
    scan_state.is_scanning = True
    started = datetime.now(timezone.utc)
    try:
        cfg = await get_config()
        # 1) Fetch tickers with volume for filtering
        tickers = await exchange.get_tickers()
        # Build map symbol -> volValue (24h quote volume)
        vol_map: dict[str, float] = {}
        for t in tickers:
            try:
                vol_map[t["symbol"]] = float(t.get("volValue") or 0)
            except (TypeError, ValueError):
                continue

        symbols = await exchange.get_symbols()
        quotes = {q.strip() for q in (cfg.quote_filter or "").split(",") if q.strip()}
        pairs: list[str] = []
        for s in symbols:
            if not s.get("enableTrading"):
                continue
            sym = s.get("symbol")
            if not sym:
                continue
            if quotes and s.get("quoteCurrency") not in quotes:
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

        active = active_strategies(cfg)

        async def process(sym: str) -> None:
            for tf in cfg.timeframes:
                try:
                    if "counter_trend" in active:
                        sig3 = await analyze_pair_counter(sym, tf, cfg)
                        if sig3:
                            signals_found.append(sig3)
                    if "fvg_reversal" in active:
                        sig4 = await analyze_pair_fvg_reversal(sym, tf, cfg)
                        if sig4:
                            signals_found.append(sig4)
                    if "rsi_reversion" in active:
                        sig5 = await analyze_pair_rsi_reversion(sym, tf, cfg)
                        if sig5:
                            signals_found.append(sig5)
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
            # Instrumentation: track how many candles it actually takes for
            # price to reach the entry zone after a signal fires, so the
            # generation/expiration timing can later be calibrated on real
            # data instead of a guessed number of candles.
            await db.entry_timing_log.insert_many([
                {
                    "id": str(uuid.uuid4()),
                    "signal_id": s.id,
                    "symbol": s.symbol,
                    "timeframe": s.timeframe,
                    "side": s.side,
                    "strategy": s.strategy,
                    "entry": s.entry,
                    "signal_created_at": s.created_at,
                    "status": "pending",  # pending | reached | not_reached
                    "candles_to_entry": None,
                }
                for s in signals_found
            ])

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
async def expire_stale_signals(cfg: Config) -> None:
    """Expire a signal after `signal_validity_candles` candles of its OWN
    timeframe have elapsed (not a fixed hour count) — e.g. 5 candles on 1h
    means 5 hours of validity, giving price real room to reach the entry
    zone instead of expiring after essentially a single candle."""
    now = datetime.now(timezone.utc)
    cursor = db.signals.find({"status": "active"})
    async for sig in cursor:
        try:
            created = datetime.fromisoformat(sig["created_at"])
        except Exception:  # noqa: BLE001
            continue
        tf_sec = TF_SECONDS.get(sig.get("timeframe"), 3600)
        max_age_sec = tf_sec * max(1, cfg.signal_validity_candles)
        age_sec = (now - created).total_seconds()
        if age_sec > max_age_sec:
            await db.signals.update_one(
                {"_id": sig["_id"]}, {"$set": {"status": "expired"}}
            )


async def scheduler_loop() -> None:
    # small warm-up delay
    await asyncio.sleep(5)
    while True:
        cfg = await get_config()
        try:
            await expire_stale_signals(cfg)
            await run_scan()
            await run_scalping_scan()
            await run_grid_scan()
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


async def scalping_monitor_loop() -> None:
    """Check Scalping Bot SL/TP/invalidation every 3s using the real-time WS
    price cache — decoupled from the (much slower) scan cycle so stop losses
    trigger close to the intended level instead of drifting on fast moves."""
    await asyncio.sleep(8)
    while True:
        try:
            await close_scalping_positions()
        except Exception as e:  # noqa: BLE001
            logger.exception("Scalping monitor error: %s", e)
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
        candles = await exchange.get_klines(log["symbol"], tf)
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


ENTRY_TIMING_MAX_LOOKAHEAD = 50  # candles to check before giving up on "reached"


async def resolve_entry_timing() -> None:
    """For each pending entry-timing log, look at the candles that closed
    AFTER the signal, and find how many candles it took for price to first
    touch the entry level. Marks 'not_reached' once the lookahead window
    elapses without a touch. This builds real data on how long price
    typically takes to reach the entry zone, per symbol/timeframe/strategy —
    used to calibrate signal generation/expiration timing later."""
    pending = await db.entry_timing_log.find(
        {"status": "pending"}, {"_id": 0}
    ).to_list(1000)
    for log in pending:
        tf = log.get("timeframe", "1h")
        tf_sec = TF_SECONDS.get(tf, 3600)
        try:
            created_epoch = datetime.fromisoformat(log["signal_created_at"]).timestamp()
        except (ValueError, TypeError):
            continue
        candles = await exchange.get_klines(log["symbol"], tf)
        # candles: [t, o, c, h, l, v] ascending, t in seconds
        after = [c for c in candles if c[0] >= created_epoch]
        if not after:
            continue
        window = after[:ENTRY_TIMING_MAX_LOOKAHEAD]
        entry = log["entry"]
        side = log["side"]
        hit_idx = None
        for i, c in enumerate(window):
            high, low = c[3], c[4]
            if side == "long" and low <= entry:
                hit_idx = i + 1
                break
            if side == "short" and high >= entry:
                hit_idx = i + 1
                break
        elapsed = time.time() - created_epoch
        window_complete = elapsed >= ENTRY_TIMING_MAX_LOOKAHEAD * tf_sec
        if hit_idx is not None:
            await db.entry_timing_log.update_one(
                {"id": log["id"]},
                {"$set": {"status": "reached", "candles_to_entry": hit_idx}},
            )
        elif window_complete:
            await db.entry_timing_log.update_one(
                {"id": log["id"]},
                {"$set": {"status": "not_reached", "candles_to_entry": None}},
            )


async def entry_timing_loop() -> None:
    await asyncio.sleep(40)
    while True:
        try:
            await resolve_entry_timing()
        except Exception as e:  # noqa: BLE001
            logger.exception("Entry timing checker error: %s", e)
        await asyncio.sleep(120)


# ---------------------------------------------------------------------------
# FastAPI app & routes
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    await get_paper_config()  # sync exchange.category from trading_mode
    scan_task = asyncio.create_task(scheduler_loop())
    monitor_task = asyncio.create_task(paper_monitor_loop())
    scalping_monitor_task = asyncio.create_task(scalping_monitor_loop())
    grid_monitor_task = asyncio.create_task(grid_monitor_loop())
    ws_task = asyncio.create_task(price_feed.run())
    premature_task = asyncio.create_task(premature_stop_loop())
    entry_timing_task = asyncio.create_task(entry_timing_loop())
    log_prune_task = asyncio.create_task(log_prune_loop())
    yield
    scan_task.cancel()
    monitor_task.cancel()
    scalping_monitor_task.cancel()
    grid_monitor_task.cancel()
    ws_task.cancel()
    premature_task.cancel()
    entry_timing_task.cancel()
    log_prune_task.cancel()
    await exchange.close()
    client.close()


app = FastAPI(lifespan=lifespan)
api = APIRouter(prefix="/api")


@api.get("/")
async def root() -> dict[str, str]:
    return {"service": "bitsignal-bot", "status": "ok"}


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
    tickers = await exchange.get_tickers()
    quotes = {q.strip() for q in (cfg.quote_filter or "").split(",") if q.strip()}
    out: list[dict[str, Any]] = []
    for t in tickers:
        sym = t.get("symbol", "")
        if quotes and not any(sym.endswith(q) for q in quotes):
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
    candles = await exchange.get_klines(symbol, timeframe)
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
    tickers = await exchange.get_tickers()
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


@api.get("/entry-timing/log")
async def entry_timing_log(limit: int = 300) -> dict[str, Any]:
    """Real data on how many candles it took price to reach the entry zone
    after a signal fired — broken down by timeframe, to calibrate signal
    generation/expiration timing instead of guessing."""
    cursor = db.entry_timing_log.find({}, {"_id": 0}).sort("signal_created_at", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    total = len(logs)
    reached = [l for l in logs if l.get("status") == "reached"]
    not_reached = sum(1 for l in logs if l.get("status") == "not_reached")
    pending = sum(1 for l in logs if l.get("status") == "pending")
    reach_rate = round((len(reached) / (len(reached) + not_reached)) * 100, 1) if (reached or not_reached) else 0.0

    by_timeframe: dict[str, list[int]] = {}
    for l in reached:
        by_timeframe.setdefault(l["timeframe"], []).append(l["candles_to_entry"])
    avg_candles_by_tf = {
        tf: round(sum(vals) / len(vals), 2) for tf, vals in by_timeframe.items()
    }

    return {
        "logs": logs,
        "count": total,
        "reached": len(reached),
        "not_reached": not_reached,
        "pending": pending,
        "reach_rate": reach_rate,
        "avg_candles_to_entry_by_timeframe": avg_candles_by_tf,
    }


@api.get("/strategy-debug/log")
async def strategy_debug_log(limit: int = 1000, minutes: int = 180) -> dict[str, Any]:
    """Bottleneck analysis for the 3 traditional strategies: which specific
    check rejects the most candidates, broken down per strategy. Looks at
    the last `minutes` of scans (default 3h) so it reflects current market
    conditions rather than the whole history."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    cursor = db.strategy_debug_log.find(
        {"at": {"$gte": since}}, {"_id": 0}
    ).sort("at", -1).limit(limit)
    logs = await cursor.to_list(length=limit)

    by_strategy: dict[str, dict[str, int]] = {}
    for l in logs:
        strat = l.get("strategy", "unknown")
        reason = l.get("reason", "unknown")
        by_strategy.setdefault(strat, {})
        by_strategy[strat][reason] = by_strategy[strat].get(reason, 0) + 1

    bottleneck_by_strategy = {
        strat: max(reasons, key=reasons.get) if reasons else None
        for strat, reasons in by_strategy.items()
    }

    return {
        "window_minutes": minutes,
        "count": len(logs),
        "reason_counts_by_strategy": by_strategy,
        "bottleneck_by_strategy": bottleneck_by_strategy,
    }


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
    tickers = await exchange.get_tickers()
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


class AddFundsRequest(BaseModel):
    amount: float


@api.post("/paper/add-funds")
async def paper_add_funds(req: AddFundsRequest) -> dict[str, Any]:
    """Ricarica il portafoglio principale SENZA azzerare posizioni o storico
    (a differenza di /paper/set-capital, che resetta tutto). Aumenta anche il
    capitale iniziale della stessa cifra, così il rendimento % percentuale
    resta corretto (non scambia una ricarica per un guadagno). Incremento
    atomico: mai a rischio di sovrascrivere un cambio di saldo concorrente
    (es. un'operazione che si chiude proprio in quell'istante)."""
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")

    updated_state = await db.paper_state.find_one_and_update(
        {"_id": PAPER_STATE_ID},
        {"$inc": {"cash": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    updated_cfg = await db.paper_config.find_one_and_update(
        {"_id": PAPER_CFG_ID},
        {"$inc": {"initial_capital": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return {
        "ok": True,
        "cash": updated_state.get("cash", amount),
        "initial_capital": updated_cfg.get("initial_capital", amount),
    }


class StrategyAllocateRequest(BaseModel):
    amount: float


@api.get("/strategy-wallets")
async def strategy_wallets_status() -> dict[str, Any]:
    """Allocation status of the three traditional strategies: whether each
    has isolated funds or is still on the shared main pool."""
    main_cash = await get_paper_cash()
    out = []
    for name in STRATEGY_WALLET_NAMES:
        w = await get_strategy_wallet(name)
        open_count = await db.paper_positions.count_documents({"strategy": name})
        out.append({
            "strategy": name,
            "allocated": w is not None,
            "cash": w.get("cash", 0.0) if w else None,
            "open_positions": open_count,
        })
    return {"shared_cash": round(main_cash, 2), "strategies": out}


@api.post("/strategy-wallets/{strategy}/allocate")
async def strategy_wallet_allocate(strategy: str, req: StrategyAllocateRequest) -> dict[str, Any]:
    """Move `amount` from the shared main wallet into a dedicated ('isolated')
    wallet for this strategy. Refused while the strategy has open positions,
    to avoid mixing up which pool they were opened against."""
    if strategy not in STRATEGY_WALLET_NAMES:
        raise HTTPException(status_code=400, detail="invalid strategy")
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")
    open_count = await db.paper_positions.count_documents({"strategy": strategy})
    if open_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Chiudi prima le {open_count} posizioni aperte di questa strategia",
        )
    main_cash = await get_paper_cash()
    if amount > main_cash:
        raise HTTPException(
            status_code=400,
            detail=f"Fondi insufficienti nel portafoglio principale (disponibili: {round(main_cash, 2)})",
        )
    await set_paper_cash(main_cash - amount)
    existing = await get_strategy_wallet(strategy)
    new_cash = (existing.get("cash", 0.0) if existing else 0.0) + amount
    await db.strategy_wallets.update_one(
        {"_id": strategy},
        {"$set": {"cash": new_cash, "allocated": True}},
        upsert=True,
    )
    return {"ok": True, "strategy": strategy, "cash": new_cash, "main_cash": main_cash - amount}


@api.post("/strategy-wallets/{strategy}/deallocate")
async def strategy_wallet_deallocate(strategy: str) -> dict[str, Any]:
    """Move all of this strategy's isolated cash back to the shared main
    wallet, and put the strategy back on the shared pool. Refused while the
    strategy has open positions."""
    if strategy not in STRATEGY_WALLET_NAMES:
        raise HTTPException(status_code=400, detail="invalid strategy")
    open_count = await db.paper_positions.count_documents({"strategy": strategy})
    if open_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Chiudi prima le {open_count} posizioni aperte di questa strategia",
        )
    w = await get_strategy_wallet(strategy)
    amount = w.get("cash", 0.0) if w else 0.0
    await db.strategy_wallets.update_one(
        {"_id": strategy}, {"$set": {"cash": 0.0, "allocated": False}}, upsert=True
    )
    main_cash = await get_paper_cash()
    await set_paper_cash(main_cash + amount)
    return {"ok": True, "strategy": strategy, "main_cash": main_cash + amount}


@api.post("/strategy-wallets/{strategy}/withdraw")
async def strategy_wallet_withdraw(strategy: str, req: StrategyAllocateRequest) -> dict[str, Any]:
    """Withdraw a SPECIFIC amount from this strategy's isolated wallet back
    to the shared main wallet (unlike /deallocate, which always takes
    everything). If the withdrawal empties the isolated wallet, the strategy
    automatically goes back on the shared pool. Refused while the strategy
    has open positions. Atomic: the debit only applies if the isolated
    wallet still has at least `amount` at the moment of the write, so it can
    never go negative even under concurrent activity."""
    if strategy not in STRATEGY_WALLET_NAMES:
        raise HTTPException(status_code=400, detail="invalid strategy")
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")
    open_count = await db.paper_positions.count_documents({"strategy": strategy})
    if open_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Chiudi prima le {open_count} posizioni aperte di questa strategia",
        )
    updated = await db.strategy_wallets.find_one_and_update(
        {"_id": strategy, "allocated": True, "cash": {"$gte": amount}},
        {"$inc": {"cash": -amount}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        w = await get_strategy_wallet(strategy)
        avail = w.get("cash", 0.0) if w else 0.0
        raise HTTPException(
            status_code=400,
            detail=f"Fondi insufficienti nel portafoglio isolato (disponibili: {round(avail, 2)})",
        )
    new_cash = updated.get("cash", 0.0)
    if new_cash <= 0.0001:
        # Fully emptied: revert to the shared pool automatically, same end
        # state as /deallocate.
        await db.strategy_wallets.update_one(
            {"_id": strategy}, {"$set": {"cash": 0.0, "allocated": False}}
        )
        new_cash = 0.0
    main_updated = await db.paper_state.find_one_and_update(
        {"_id": PAPER_STATE_ID},
        {"$inc": {"cash": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return {
        "ok": True,
        "strategy": strategy,
        "cash": round(new_cash, 4),
        "main_cash": round(main_updated.get("cash", amount), 4),
    }


@api.get("/strategy/{strategy}/portfolio")
async def strategy_portfolio(strategy: str) -> dict[str, Any]:
    """Portfolio view scoped to a single strategy: its positions/trades, and
    either its own isolated cash or the shared main cash (labeled as such)."""
    if strategy not in STRATEGY_WALLET_NAMES:
        raise HTTPException(status_code=400, detail="invalid strategy")
    w = await get_strategy_wallet(strategy)
    is_isolated = w is not None
    cash = w.get("cash", 0.0) if w else await get_paper_cash()

    open_positions = await db.paper_positions.find(
        {"strategy": strategy}, {"_id": 0}
    ).to_list(1000)
    tickers = await exchange.get_tickers()
    price_map: dict[str, float] = {}
    for t in tickers:
        try:
            price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue
    unrealized = 0.0
    allocated = 0.0
    enriched: list[dict[str, Any]] = []
    for p in open_positions:
        cur = price_map.get(p["symbol"], p["entry"])
        if p["side"] == "long":
            pnl = (cur - p["entry"]) * p["quantity"]
        else:
            pnl = (p["entry"] - cur) * p["quantity"]
        notional = p["entry"] * p["quantity"]
        pnl_pct = (pnl / notional) * 100 if notional > 0 else 0
        unrealized += pnl
        allocated += cur * p["quantity"]
        enriched.append({
            **p, "current_price": round(cur, 8),
            "unrealized_pnl": round(pnl, 2), "unrealized_pnl_pct": round(pnl_pct, 2),
        })

    closed = await db.paper_trades.find(
        {"strategy": strategy}, {"_id": 0}
    ).sort("closed_at", -1).to_list(500)
    realized = sum(t.get("pnl_usdt", 0.0) for t in closed)
    wins = sum(1 for t in closed if t.get("outcome") == "win")
    losses = sum(1 for t in closed if t.get("outcome") == "loss")
    settled = wins + losses
    win_rate = round((wins / settled) * 100, 1) if settled else 0.0
    equity = cash + allocated if is_isolated else cash

    return {
        "strategy": strategy,
        "wallet_type": "isolated" if is_isolated else "shared",
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "unrealized_pnl": round(unrealized, 2),
        "realized_pnl": round(realized, 2),
        "open_positions": enriched,
        "closed_trades": closed,
        "open_count": len(enriched),
        "closed_count": len(closed),
        "win_rate": win_rate,
    }


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


# ---------------------------------------------------------------------------
# Bybit EU authenticated integration (connection test + balance; execution
# lives in the execution phase). Bybit v5 HMAC-SHA256 signing.
# ---------------------------------------------------------------------------
async def _bybit_signed_get(path: str, query: str = "") -> httpx.Response:
    doc = await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=400, detail="No Bybit credentials stored")
    api_key = decrypt_str(doc["api_key"])
    api_secret = decrypt_str(doc["api_secret"])
    ts = str(int(time.time() * 1000))
    recv = "5000"
    pre_sign = ts + api_key + recv + query
    sig = hmac.new(api_secret.encode(), pre_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
    }
    url = path + (("?" + query) if query else "")
    async with httpx.AsyncClient(base_url=BYBIT_BASE, timeout=10.0) as c:
        return await c.get(url, headers=headers)


@api.get("/exchange/status")
async def exchange_status() -> dict[str, Any]:
    doc = await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 0})
    if not doc:
        return {"connected": False, "exchange": "bybit"}
    try:
        r = await _bybit_signed_get(
            "/v5/account/wallet-balance", "accountType=UNIFIED"
        )
        data = r.json()
        if r.status_code != 200 or data.get("retCode") != 0:
            return {
                "connected": False,
                "exchange": "bybit",
                "error": data.get("retMsg", f"HTTP {r.status_code}"),
                "api_key_masked": doc.get("api_key_masked", ""),
            }
        usdt_total = 0.0
        for acc in data.get("result", {}).get("list", []):
            for coin in acc.get("coin", []):
                if coin.get("coin") == "USDT":
                    try:
                        usdt_total += float(coin.get("walletBalance") or 0)
                    except (TypeError, ValueError):
                        continue
        return {
            "connected": True,
            "exchange": "bybit",
            "api_key_masked": doc.get("api_key_masked", ""),
            "usdt_balance": round(usdt_total, 2),
            "connected_at": doc.get("connected_at"),
        }
    except (httpx.HTTPError, InvalidToken) as e:
        return {"connected": False, "exchange": "bybit", "error": str(e)}


@api.post("/exchange/connect")
async def exchange_connect(req: ExchangeConnectRequest) -> dict[str, Any]:
    if not req.api_key or not req.api_secret:
        raise HTTPException(status_code=400, detail="API key and secret required")
    doc = {
        "api_key": encrypt_str(req.api_key),
        "api_secret": encrypt_str(req.api_secret),
        "api_key_masked": (req.api_key[:4] + "…" + req.api_key[-4:])
        if len(req.api_key) > 8
        else "***",
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.exchange_creds.update_one(
        {"_id": "bybit"}, {"$set": doc}, upsert=True
    )
    # Test connection immediately
    status_res = await exchange_status()
    if not status_res.get("connected"):
        await db.exchange_creds.delete_one({"_id": "bybit"})
        raise HTTPException(
            status_code=400,
            detail=f"Connection failed: {status_res.get('error', 'unknown')}",
        )
    return status_res


@api.post("/exchange/disconnect")
async def exchange_disconnect() -> dict[str, Any]:
    await db.exchange_creds.delete_one({"_id": "bybit"})
    return {"ok": True}


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Scalping Bot (independent strategy): VWAP + RSI(9) + Bollinger Bands + EMA9/21
# ---------------------------------------------------------------------------
def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _vwap(highs, lows, closes, volumes) -> float:
    num = 0.0
    den = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        tp = (h + l + c) / 3
        num += tp * v
        den += v
    return num / den if den else closes[-1]


def _bollinger(closes: list[float], period: int, std_mult: float):
    window = closes[-period:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    std = variance ** 0.5
    return mean - std_mult * std, mean, mean + std_mult * std


def analyze_scalping(highs, lows, closes, volumes, rsis, cfg) -> dict:
    """Scalping setup: EMA9/21 trend + VWAP reclaim/loss + Bollinger touch + volume."""
    ema_fast = _ema(closes, cfg.scalping_ema_fast)
    ema_slow = _ema(closes, cfg.scalping_ema_slow)
    vwap = _vwap(highs, lows, closes, volumes)
    lower_bb, mid_bb, upper_bb = _bollinger(closes, cfg.scalping_bb_period, cfg.scalping_bb_std)
    last_close = closes[-1]
    last_rsi = rsis[-1] if rsis else 50.0
    avg_vol = sum(volumes[-20:]) / max(1, len(volumes[-20:]))
    vol_ok = volumes[-1] >= avg_vol * cfg.scalping_volume_multiplier

    side = None
    reasons = []
    if ema_fast[-1] > ema_slow[-1] and last_close > vwap and last_close <= lower_bb * 1.01:
        side = "long"
        reasons = ["EMA9>EMA21", "Above VWAP", "Near lower BB"]
    elif ema_fast[-1] < ema_slow[-1] and last_close < vwap and last_close >= upper_bb * 0.99:
        side = "short"
        reasons = ["EMA9<EMA21", "Below VWAP", "Near upper BB"]

    if vol_ok and side:
        reasons.append("Volume Spike")

    # Discard flat / dead markets: Bollinger Bands almost touching means
    # the price hasn't really moved, so signals are not reliable.
    bb_width_pct = (upper_bb - lower_bb) / mid_bb * 100 if mid_bb else 0.0
    is_flat = bb_width_pct < 0.05

    confirmed = side is not None and vol_ok and not is_flat
    return {
        "confirmed": confirmed,
        "side": side,
        "reasons": reasons,
        "vwap": round(vwap, 6),
        "rsi": round(last_rsi, 2),
        "bb_lower": round(lower_bb, 6),
        "bb_upper": round(upper_bb, 6),
        "ema_fast": round(ema_fast[-1], 6),
        "ema_slow": round(ema_slow[-1], 6),
    }


# ---------------------------------------------------------------------------
# Scalping Bot: independent scan loop + API endpoints
# ---------------------------------------------------------------------------
async def run_scalping_scan() -> dict[str, Any]:
    cfg = await get_config()
    if not cfg.scalping_enabled:
        return {"skipped": True, "reason": "scalping disabled"}

    tickers = await exchange.get_tickers()
    vol_map: dict[str, float] = {}
    rest_price_map: dict[str, float] = {}
    for t in tickers:
        try:
            vol_map[t["symbol"]] = float(t.get("volValue") or 0)
        except (TypeError, ValueError):
            continue
        try:
            rest_price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue


    symbols = await exchange.get_symbols()
    quotes = {q.strip() for q in (cfg.quote_filter or "").split(",") if q.strip()}
    pairs: list[str] = []
    for s in symbols:
        if not s.get("enableTrading"):
            continue
        sym = s.get("symbol")
        if not sym:
            continue
        if quotes and s.get("quoteCurrency") not in quotes:
            continue
        if cfg.excluded_pairs and sym in cfg.excluded_pairs:
            continue
        if cfg.enabled_pairs and sym not in cfg.enabled_pairs:
            continue
        if vol_map.get(sym, 0) < cfg.min_24h_volume_usdt:
            continue
        pairs.append(sym)
    pairs.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
    pairs = pairs[:20]

    tf = cfg.scalping_timeframe
    signals_found: list[dict[str, Any]] = []
    for symbol in pairs:
        try:
            candles = await exchange.get_klines(symbol, tf)
        except Exception:  # noqa: BLE001
            continue
        min_len = max(cfg.scalping_bb_period, cfg.scalping_ema_slow) + 5
        if len(candles) < min_len:
            continue
        highs = [c[3] for c in candles]
        lows = [c[4] for c in candles]
        closes = [c[2] for c in candles]
        volumes = [c[5] for c in candles]
        rsis = rsi_wilder(closes, cfg.scalping_rsi_period)
        result = analyze_scalping(highs, lows, closes, volumes, rsis, cfg)
        if not result.get("confirmed"):
            continue
        # Use the freshest price available for the fill — NOT the last closed
        # candle's close, which can be up to a full timeframe old (5 min) and
        # was letting positions open already past their own tight SL/TP: (1)
        # the live WebSocket cache, if this symbol already has an open
        # position feeding it; (2) the REST ticker fetched moments ago at the
        # top of this same scan (fresh within seconds, not minutes); (3) the
        # candle close only as a last resort.
        live_price = price_feed.get(symbol) or rest_price_map.get(symbol) or closes[-1]
        doc = {
            "id": str(uuid.uuid4()),
            "symbol": symbol,
            "timeframe": tf,
            "side": result["side"],
            "reasons": result["reasons"],
            "vwap": result["vwap"],
            "rsi": result["rsi"],
            "bb_lower": result["bb_lower"],
            "bb_upper": result["bb_upper"],
            "ema_fast": result["ema_fast"],
            "ema_slow": result["ema_slow"],
            "price": live_price,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        signals_found.append(doc)
        await open_scalping_position(doc, cfg)

    if signals_found:
        for doc in signals_found:
            await db.scalping_signals.update_many(
                {
                    "symbol": doc["symbol"],
                    "timeframe": doc["timeframe"],
                    "status": "active",
                },
                {"$set": {"status": "expired"}},
            )
        await db.scalping_signals.insert_many(signals_found)

    return {"scanned_pairs": len(pairs), "signals_found": len(signals_found)}


@api.get("/scalping/signals")
async def scalping_signals(limit: int = 50) -> dict[str, Any]:
    cursor = (
        db.scalping_signals.find({}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    logs = await cursor.to_list(length=limit)
    active = sum(1 for s in logs if s.get("status") == "active")
    return {"signals": logs, "count": len(logs), "active": active}


@api.get("/scalping/config")
async def scalping_config_get() -> dict[str, Any]:
    cfg = await get_config()
    return {
        "scalping_enabled": cfg.scalping_enabled,
        "scalping_timeframe": cfg.scalping_timeframe,
        "scalping_rsi_period": cfg.scalping_rsi_period,
        "scalping_bb_period": cfg.scalping_bb_period,
        "scalping_bb_std": cfg.scalping_bb_std,
        "scalping_ema_fast": cfg.scalping_ema_fast,
        "scalping_ema_slow": cfg.scalping_ema_slow,
        "scalping_volume_multiplier": cfg.scalping_volume_multiplier,
    }



# ---------------------------------------------------------------------------
# Scalping Bot: separate paper wallet + fund transfer
# ---------------------------------------------------------------------------
SCALPING_WALLET_ID = "scalping_wallet_singleton"


async def get_scalping_wallet() -> dict[str, Any]:
    doc = await db.scalping_wallet.find_one({"_id": SCALPING_WALLET_ID}, {"_id": 0})
    if not doc:
        doc = {"cash": 0.0, "total_transferred_in": 0.0, "reset_seq": 0}
        await db.scalping_wallet.update_one(
            {"_id": SCALPING_WALLET_ID}, {"$set": doc}, upsert=True
        )
    doc.setdefault("reset_seq", 0)
    return doc


async def save_scalping_wallet(doc: dict[str, Any]) -> None:
    await db.scalping_wallet.update_one(
        {"_id": SCALPING_WALLET_ID}, {"$set": doc}, upsert=True
    )


class ScalpingTransferRequest(BaseModel):
    amount: float


@api.post("/scalping/transfer")
async def scalping_transfer(req: ScalpingTransferRequest) -> dict[str, Any]:
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")

    main_cash = await get_paper_cash()
    if amount > main_cash:
        raise HTTPException(
            status_code=400,
            detail=f"Fondi insufficienti nel portafoglio principale (disponibili: {round(main_cash, 2)})",
        )

    await set_paper_cash(main_cash - amount)

    # Atomic increment: adds `amount` to whatever is CURRENTLY in the DB,
    # regardless of any concurrent write (a position opening/closing right
    # now, etc.) — unlike read-then-overwrite-the-whole-doc, this can never
    # lose or duplicate money no matter the timing.
    updated = await db.scalping_wallet.find_one_and_update(
        {"_id": SCALPING_WALLET_ID},
        {"$inc": {"cash": amount, "total_transferred_in": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    return {"ok": True, "scalping_cash": updated.get("cash", amount), "main_cash": main_cash - amount}


@api.get("/scalping/portfolio")
async def scalping_portfolio() -> dict[str, Any]:
    wallet = await get_scalping_wallet()
    cash = wallet.get("cash", 0.0)

    positions = await db.scalping_positions.find(
        {"status": "open"}, {"_id": 0}
    ).to_list(1000)

    tickers = await exchange.get_tickers()
    price_map: dict[str, float] = {}
    for t in tickers:
        try:
            price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue

    unrealized = 0.0
    allocated = 0.0
    enriched: list[dict[str, Any]] = []
    for p in positions:
        cur = price_map.get(p["symbol"], p["entry"])
        if p["side"] == "long":
            gross_pnl = (cur - p["entry"]) * p["quantity"]
        else:
            gross_pnl = (p["entry"] - cur) * p["quantity"]
        notional = p["entry"] * p["quantity"]
        exit_notional = cur * p["quantity"]
        est_fees = (notional + exit_notional) * SCALPING_FEE_PCT
        pnl = gross_pnl - est_fees
        pnl_pct = (pnl / notional) * 100 if notional > 0 else 0.0
        unrealized += pnl
        allocated += notional
        enriched.append(
            {
                **p,
                "current_price": round(cur, 8),
                "unrealized_pnl": round(pnl, 4),
                "unrealized_pnl_pct": round(pnl_pct, 2),
            }
        )

    closed = await db.scalping_positions.find(
        {"status": "closed"}, {"_id": 0}
    ).sort("closed_at", -1).to_list(200)
    realized = sum(c.get("pnl_usdt", 0.0) for c in closed)
    wins = sum(1 for c in closed if c.get("pnl_usdt", 0.0) > 0)
    losses = sum(1 for c in closed if c.get("pnl_usdt", 0.0) <= 0)
    settled = wins + losses
    win_rate = round((wins / settled) * 100, 1) if settled else 0.0

    equity = cash + allocated + unrealized

    # Automatic reconciliation: cash + allocated (the original notional locked
    # into open positions) must always equal exactly what was transferred in
    # plus what's been realized so far — opening/closing a position only
    # moves money between "cash" and "allocated", it never creates or
    # destroys it. A non-zero discrepancy here means something (like a Reset
    # racing with a position being opened) desynced the ledger.
    total_in = wallet.get("total_transferred_in", 0.0)
    expected_cash_plus_allocated = total_in + realized
    discrepancy = round((cash + allocated) - expected_cash_plus_allocated, 4)

    return {
        "cash": round(cash, 4),
        "allocated": round(allocated, 4),
        "unrealized_pnl": round(unrealized, 4),
        "realized_pnl": round(realized, 4),
        "equity": round(equity, 4),
        "total_transferred_in": round(total_in, 4),
        "ledger_discrepancy_usdt": discrepancy,
        "open_positions": enriched,
        "closed_positions": closed,
        "open_count": len(enriched),
        "closed_count": len(closed),
        "win_rate": win_rate,
    }


# ---------------------------------------------------------------------------
# Scalping Bot: auto-execution of real (paper) positions
# ---------------------------------------------------------------------------
SCALPING_SL_PCT = 0.004   # 0.4% gross stop loss (net ~0.6% after fees)
SCALPING_TP_PCT = 0.014   # 1.4% gross take profit (net ~1.2% after fees, 2:1 reward/risk)
SCALPING_WALLET_RISK_PCT = 0.20  # % of scalping cash used per trade
SCALPING_FEE_PCT = 0.001  # 0.10% Bybit spot fee per side (open + close = 0.20% round trip)


async def check_scalping_invalidation(pos: dict[str, Any], cfg: Config) -> bool:
    """Invalidation check (additive, Scalping Bot only): closes a trade EARLY,
    before SL/TP, if the reasons that generated the signal no longer hold —
    i.e. the trade is now going against us. Does not replace SL/TP; it only
    adds an earlier exit when the setup is invalidated.

    A long is invalidated only when BOTH conditions hold together: price
    closes below VWAP by at least `scalping_invalidation_buffer_pct` AND
    EMA9 is below EMA21 (trend flip). A short is invalidated speculare.
    Requiring both conditions (not just one) plus a minimum buffer filters
    out normal 5m noise around VWAP/EMA crosses, which otherwise triggers
    this check on almost every trade before it has a chance to reach TP.
    Only checked after `scalping_min_hold_seconds` have elapsed since the
    position was opened.
    """
    try:
        opened = datetime.fromisoformat(pos["opened_at"]).timestamp()
    except (ValueError, TypeError):
        return False
    if (time.time() - opened) < cfg.scalping_min_hold_seconds:
        return False

    candles = await exchange.get_klines(pos["symbol"], cfg.scalping_timeframe)
    min_len = max(cfg.scalping_bb_period, cfg.scalping_ema_slow) + 5
    if len(candles) < min_len:
        return False

    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    closes = [c[2] for c in candles]
    volumes = [c[5] for c in candles]
    ema_fast = _ema(closes, cfg.scalping_ema_fast)
    ema_slow = _ema(closes, cfg.scalping_ema_slow)
    vwap = _vwap(highs, lows, closes, volumes)
    last_close = closes[-1]
    buffer = cfg.scalping_invalidation_buffer_pct / 100

    if pos["side"] == "long":
        lost_vwap = last_close < vwap * (1 - buffer)
        trend_flipped = ema_fast[-1] < ema_slow[-1]
        return lost_vwap and trend_flipped
    lost_vwap = last_close > vwap * (1 + buffer)
    trend_flipped = ema_fast[-1] > ema_slow[-1]
    return lost_vwap and trend_flipped


# Per-symbol cooldown after a losing scalping trade (additive, in-memory).
_scalping_cooldown: dict[str, float] = {}


async def open_scalping_position(doc: dict[str, Any], cfg: Config) -> None:
    # Cooldown: skip re-opening on this symbol shortly after a losing close.
    last_loss = _scalping_cooldown.get(doc["symbol"])
    if last_loss and (time.time() - last_loss) < cfg.scalping_cooldown_minutes * 60:
        return
    wallet = await get_scalping_wallet()
    cash = wallet.get("cash", 0.0)
    reset_seq = wallet.get("reset_seq", 0)
    if cash <= 1.0:
        return  # not enough funds transferred to the scalping wallet

    existing = await db.scalping_positions.find_one(
        {"symbol": doc["symbol"], "status": "open"}, {"_id": 0}
    )
    if existing:
        return  # already have an open position on this pair

    notional = cash * SCALPING_WALLET_RISK_PCT
    if notional < 1.0:
        notional = min(cash, 1.0)

    fill_price = doc["price"]
    if fill_price <= 0:
        return
    quantity = notional / fill_price

    side = doc["side"]
    if side == "long":
        stop_loss = fill_price * (1 - SCALPING_SL_PCT)
        take_profit = fill_price * (1 + SCALPING_TP_PCT)
    else:
        stop_loss = fill_price * (1 + SCALPING_SL_PCT)
        take_profit = fill_price * (1 - SCALPING_TP_PCT)

    # Debit the cash atomically (an $inc, not a computed $set — immune to any
    # concurrent change to "cash" no matter the timing), guarded on the reset
    # counter unchanged since we read it: if a Reset happened in between
    # (reset_seq bumped), this update matches nothing and we abort WITHOUT
    # opening the position — this is what prevents trading against funds a
    # reset just wiped.
    result = await db.scalping_wallet.update_one(
        {"_id": SCALPING_WALLET_ID, "reset_seq": reset_seq},
        {"$inc": {"cash": -notional}},
    )
    if result.matched_count == 0:
        logger.warning("Scalping open aborted: reset happened concurrently for %s", doc["symbol"])
        return

    position = {
        "id": str(uuid.uuid4()),
        "signal_id": doc["id"],
        "symbol": doc["symbol"],
        "timeframe": doc["timeframe"],
        "side": side,
        "entry": fill_price,
        "fill_price": fill_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "quantity": quantity,
        "notional": notional,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    }
    await db.scalping_positions.insert_one(position)


async def close_scalping_positions() -> None:
    open_positions = await db.scalping_positions.find(
        {"status": "open"}, {"_id": 0}
    ).to_list(1000)
    if not open_positions:
        return

    # Prefer real-time WS prices (same cache used by the main paper monitor);
    # fall back to a single REST call only for symbols with no fresh WS price.
    price_map: dict[str, float] = {}
    need_rest = any(price_feed.get(p["symbol"]) is None for p in open_positions)
    if need_rest:
        tickers = await exchange.get_tickers()
        for t in tickers:
            try:
                price_map[t["symbol"]] = float(t.get("last") or 0)
            except (TypeError, ValueError):
                continue

    cfg = await get_config()

    for p in open_positions:
        cur = price_feed.get(p["symbol"]) or price_map.get(p["symbol"])
        if not cur:
            continue

        hit = None
        if p["side"] == "long":
            if cur <= p["stop_loss"]:
                hit = "stop_loss"
            elif cur >= p["take_profit"]:
                hit = "take_profit"
        else:
            if cur >= p["stop_loss"]:
                hit = "stop_loss"
            elif cur <= p["take_profit"]:
                hit = "take_profit"

        # Additive check: close EARLY if the setup that justified the trade
        # is no longer valid (trade going against us), before SL/TP is hit.
        if not hit and await check_scalping_invalidation(p, cfg):
            hit = "invalidation"

        if not hit:
            continue

        if p["side"] == "long":
            gross_pnl = (cur - p["entry"]) * p["quantity"]
        else:
            gross_pnl = (p["entry"] - cur) * p["quantity"]
        exit_notional = cur * p["quantity"]
        fees = (p["notional"] + exit_notional) * SCALPING_FEE_PCT
        pnl = gross_pnl - fees

        # Atomic credit per position, immediately — not accumulated locally
        # and written once at the end (which would risk clobbering a
        # concurrent deposit/withdraw the same way the old transfer code did).
        await db.scalping_wallet.update_one(
            {"_id": SCALPING_WALLET_ID},
            {"$inc": {"cash": p["notional"] + pnl}},
            upsert=True,
        )

        # Cooldown: pause re-opening on this symbol after a losing close.
        if pnl < 0:
            _scalping_cooldown[p["symbol"]] = time.time()

        await db.scalping_positions.update_one(
            {"id": p["id"]},
            {
                "$set": {
                    "status": "closed",
                    "close_price": cur,
                    "close_reason": hit,
                    "pnl_usdt": round(pnl, 4),
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )



@api.post("/scalping/withdraw")
async def scalping_withdraw(req: ScalpingTransferRequest) -> dict[str, Any]:
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")

    wallet = await get_scalping_wallet()
    scalping_cash = wallet.get("cash", 0.0)
    if amount > scalping_cash:
        raise HTTPException(
            status_code=400,
            detail=f"Fondi insufficienti nel portafoglio scalping (disponibili: {round(scalping_cash, 2)})",
        )

    # Atomic decrement (same reasoning as the deposit above): never clobbers
    # a concurrent change to "cash" made by a position opening/closing.
    updated = await db.scalping_wallet.find_one_and_update(
        {"_id": SCALPING_WALLET_ID},
        {"$inc": {"cash": -amount, "total_transferred_in": -amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    main_cash = await get_paper_cash()
    await set_paper_cash(main_cash + amount)

    return {"ok": True, "scalping_cash": updated.get("cash", 0.0), "main_cash": main_cash + amount}


@api.post("/scalping/reset")
async def scalping_reset() -> dict[str, Any]:
    """Reset dello Scalping Bot: chiude tutte le posizioni aperte, cancella lo
    storico segnali/posizioni chiuse e azzera il portafoglio scalping (cash e
    totale trasferito). NON tocca il portafoglio principale: i fondi già
    trasferiti nello scalping vengono azzerati qui; per riaverli sul
    portafoglio principale, trasferirli PRIMA di fare il reset."""
    current = await get_scalping_wallet()
    next_seq = current.get("reset_seq", 0) + 1
    await db.scalping_positions.delete_many({})
    await db.scalping_signals.delete_many({})
    wallet = {"cash": 0.0, "total_transferred_in": 0.0, "reset_seq": next_seq}
    await save_scalping_wallet(wallet)
    return {"ok": True, "cash": 0.0}


# ---------------------------------------------------------------------------
# Grid Bot (independent strategy): range/laterale trading with ATR-spaced,
# wide/sparse grid levels. Long-only spot-style cells: buy at the lower edge
# of a cell, sell at its upper edge, then re-arm the cell so it can buy again
# on the next dip — this is what produces the repeated small profits typical
# of grid trading, but ONLY while the market stays range-bound.
# ---------------------------------------------------------------------------
GRID_WALLET_ID = "grid_wallet_singleton"
GRID_FEE_PCT = 0.001  # 0.10% Bybit spot fee per side


async def get_grid_wallet() -> dict[str, Any]:
    doc = await db.grid_wallet.find_one({"_id": GRID_WALLET_ID}, {"_id": 0})
    if not doc:
        doc = {"cash": 0.0, "total_transferred_in": 0.0, "reset_seq": 0}
        await db.grid_wallet.update_one(
            {"_id": GRID_WALLET_ID}, {"$set": doc}, upsert=True
        )
    doc.setdefault("reset_seq", 0)
    return doc


async def save_grid_wallet(doc: dict[str, Any]) -> None:
    await db.grid_wallet.update_one(
        {"_id": GRID_WALLET_ID}, {"$set": doc}, upsert=True
    )


def analyze_grid_eligibility(closes: list[float], cfg: Config) -> dict[str, Any]:
    """LEGACY (no longer used to build grids — kept only in case old code
    elsewhere still calls it). A market is 'laterale' (range-bound) when
    both the Bollinger Bands are tight AND the fast/slow EMAs are close."""
    ema_f = _ema(closes, cfg.grid_ema_fast)
    ema_s = _ema(closes, cfg.grid_ema_slow)
    lower, mid, upper = _bollinger(closes, cfg.grid_bb_period, cfg.grid_bb_std)
    last = closes[-1]
    ema_gap_pct = abs(ema_f[-1] - ema_s[-1]) / last * 100 if last else 0.0
    bb_width_pct = (upper - lower) / mid * 100 if mid else 0.0
    ranging = (
        bb_width_pct <= cfg.grid_max_bb_width_pct
        and ema_gap_pct <= cfg.grid_max_ema_gap_pct
    )
    return {
        "ranging": ranging,
        "bb_width_pct": round(bb_width_pct, 3),
        "ema_gap_pct": round(ema_gap_pct, 3),
    }


async def build_grid_plan(symbol: str, cfg: Config) -> Optional[dict[str, Any]]:
    """Look for a live long-only setup: a confirmed UPTREND on the higher
    timeframe, currently retracing DOWN into the bullish FVG left by its own
    impulse — the same structural idea as Rev Pre-FVG / FVG Reversal, but
    executed as a staggered multi-level buy grid across the FVG zone instead
    of one single entry. Returns None if no such live setup exists right now
    for this symbol (no uptrend, no FVG, or price too far from the zone).

    Target: NOT full trend resumption — a 50% recovery of the retracement
    leg (from the FVG's bottom back up toward the swing high that preceded
    it), a modest and more achievable level, shared by every cell in this
    grid. Long-only, matching spot (no shorts)."""
    candles = await exchange.get_klines(symbol, cfg.grid_timeframe)
    if len(candles) < 60:
        return None
    closes = [c[2] for c in candles]
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]

    htf = _higher_tf(cfg.grid_timeframe)
    hcandles = await exchange.get_klines(symbol, htf)
    if len(hcandles) < 20:
        return None
    if detect_market_structure(hcandles, cfg.pivot_window) != "up":
        return None  # long-only: needs a genuinely confirmed uptrend

    atr = atr_wilder(highs, lows, closes, cfg.grid_atr_period)
    if not atr or atr <= 0:
        return None

    fvgs = detect_all_fvgs(highs, lows, cfg.fvg_lookback)
    bullish_fvgs = [f for f in fvgs if f["kind"] == "bullish"]
    if not bullish_fvgs:
        return None
    origin = max(bullish_fvgs, key=lambda f: f["gap"])  # most significant impulse
    fvg_top, fvg_bottom = origin["top"], origin["bottom"]

    current = closes[-1]
    # Only relevant if price is actually retracing at/near the zone right
    # now — not still far above it, and not already broken well below it.
    if current > fvg_top * 1.02 or current < fvg_bottom * 0.97:
        return None

    i = origin["index"]
    lookback_start = max(0, i - 15)
    swing_high = max(highs[lookback_start:i + 1]) if i > lookback_start else fvg_top
    if swing_high <= fvg_top:
        return None  # no real impulse leg above the gap — not a usable setup

    target = fvg_bottom + 0.5 * (swing_high - fvg_bottom)
    if target <= fvg_top:
        target = fvg_top  # keep the target sane in a degenerate case

    n = cfg.grid_num_levels
    step = (fvg_top - fvg_bottom) / max(1, n - 1) if n > 1 else 0.0
    cells = []
    for idx in range(n):
        buy_price = fvg_top - idx * step
        cells.append({
            "index": idx + 1,
            "buy_price": round(buy_price, 8),
            "sell_price": round(target, 8),
            "status": "armed",
        })

    # Score for ranking candidates (lower = better): a bigger impulse
    # relative to volatility is a more significant, more credible setup.
    score = -(origin["gap"] / atr)

    return {
        "symbol": symbol,
        "score": score,
        "center_price": round(current, 8),
        "atr": round(atr, 8),
        "cells": cells,
        "fvg_top": round(fvg_top, 8),
        "fvg_bottom": round(fvg_bottom, 8),
        "swing_high": round(swing_high, 8),
        "target": round(target, 8),
    }


async def create_grid_instance_from_plan(plan: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """Persist a plan already computed by build_grid_plan — kept separate so
    the scan can score many candidates without re-fetching/re-computing
    anything when it's time to actually create the chosen ones."""
    wallet = await get_grid_wallet()
    notional_per_cell = (wallet.get("cash", 0.0) / max(1, cfg.grid_max_pairs)) / cfg.grid_num_levels
    n = cfg.grid_num_levels
    spacing = (plan["fvg_top"] - plan["fvg_bottom"]) / max(1, n - 1) if n > 1 else 0.0
    doc = {
        "id": str(uuid.uuid4()),
        "symbol": plan["symbol"],
        "timeframe": cfg.grid_timeframe,
        "center_price": plan["center_price"],
        "spacing": round(spacing, 8),
        "atr": plan["atr"],
        "cells": plan["cells"],
        "notional_per_cell": round(notional_per_cell, 4),
        "status": "active",
        "stopped_reason": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Legacy fields kept for schema/frontend compatibility — no longer
        # meaningful under the FVG-retracement design (always 0 now).
        "bb_width_pct": 0.0,
        "ema_gap_pct": 0.0,
        # New, informative fields for this design.
        "fvg_top": plan["fvg_top"],
        "fvg_bottom": plan["fvg_bottom"],
        "swing_high": plan["swing_high"],
        "target": plan["target"],
    }
    await db.grid_instances.insert_one(doc)
    return doc


async def check_grid_reversals(cfg: Config) -> int:
    """Close any ACTIVE grid whose higher-timeframe structure has flipped to
    a confirmed downtrend (lower highs + lower lows) — the real invalidation
    for a long-only 'buy the uptrend retracement' grid, checked on the scan
    cadence (not every 3s — a structural reversal doesn't need that
    frequency, and it saves hammering the exchange with extra candle
    fetches). This runs in ADDITION to the fast ATR-distance emergency stop
    in monitor_grid_instances, which stays as a quick circuit-breaker for a
    sudden crash between scan cycles."""
    active = await db.grid_instances.find({"status": "active"}, {"_id": 0}).to_list(100)
    stopped = 0
    for g in active:
        hcandles = await exchange.get_klines(g["symbol"], _higher_tf(cfg.grid_timeframe))
        if len(hcandles) < 20:
            continue
        if detect_market_structure(hcandles, cfg.pivot_window) != "down":
            continue
        cur = price_feed.get(g["symbol"])
        if not cur:
            tickers = await exchange.get_tickers()
            cur = next((float(t.get("last") or 0) for t in tickers if t.get("symbol") == g["symbol"]), None)
        if not cur:
            continue
        holding = await db.grid_positions.find(
            {"grid_id": g["id"], "status": "open"}, {"_id": 0}
        ).to_list(100)
        for pos in holding:
            exit_notional = cur * pos["quantity"]
            fees = (pos["notional"] + exit_notional) * GRID_FEE_PCT
            pnl = (cur - pos["entry"]) * pos["quantity"] - fees
            await db.grid_wallet.update_one(
                {"_id": GRID_WALLET_ID}, {"$inc": {"cash": pos["notional"] + pnl}}, upsert=True
            )
            await db.grid_positions.update_one(
                {"id": pos["id"]},
                {"$set": {
                    "status": "closed", "close_price": cur,
                    "close_reason": "trend_reversal_confirmed", "pnl_usdt": round(pnl, 4),
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        await db.grid_instances.update_one(
            {"id": g["id"]},
            {"$set": {"status": "stopped", "stopped_reason": "trend_reversal_confirmed"}},
        )
        stopped += 1
    return stopped


async def run_grid_scan() -> dict[str, Any]:
    """Score EVERY candidate pair for a live 'buy the uptrend retracement
    into its own FVG' setup first, THEN fill open slots with the best-scored
    ones (biggest impulse relative to volatility) — not just the first ones
    found scanning in volume order. Once slots are full, whatever's left
    over is still considered for a replacement: if a leftover candidate is a
    meaningfully better setup than the worst-scoring currently-active grid
    that has NO cell currently holding (no capital mid-trade gets forced
    out — only idle, still-armed capital is ever reassigned), it swaps in.
    Also closes any active grid whose higher-timeframe trend has confirmed-
    reversed, before doing anything else."""
    cfg = await get_config()
    if not cfg.grid_enabled:
        return {"skipped": True, "reason": "grid disabled"}
    wallet = await get_grid_wallet()

    reversals_closed = await check_grid_reversals(cfg)

    if wallet.get("cash", 0.0) <= 1.0:
        return {"skipped": True, "reason": "no funds in grid wallet", "reversals_closed": reversals_closed}

    active_instances = await db.grid_instances.find(
        {"status": "active"}, {"_id": 0}
    ).to_list(100)
    active_symbols = {g["symbol"] for g in active_instances}
    slots = cfg.grid_max_pairs - len(active_instances)

    tickers = await exchange.get_tickers()
    vol_map: dict[str, float] = {}
    for t in tickers:
        try:
            vol_map[t["symbol"]] = float(t.get("volValue") or 0)
        except (TypeError, ValueError):
            continue

    symbols_info = await exchange.get_symbols()
    quotes = {q.strip() for q in (cfg.quote_filter or "").split(",") if q.strip()}
    candidates: list[str] = []
    for s in symbols_info:
        if not s.get("enableTrading"):
            continue
        sym = s.get("symbol")
        if not sym or sym in active_symbols:
            continue
        if quotes and s.get("quoteCurrency") not in quotes:
            continue
        if cfg.excluded_pairs and sym in cfg.excluded_pairs:
            continue
        if cfg.enabled_pairs and sym not in cfg.enabled_pairs:
            continue
        if vol_map.get(sym, 0) < cfg.min_24h_volume_usdt:
            continue
        candidates.append(sym)
    candidates.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
    candidates = candidates[:30]  # cap scan for performance

    # Score EVERY candidate up front (one pass) — so slot-filling can pick
    # the best live setup, not just the fastest-found-in-volume-order.
    scored: list[tuple[float, dict[str, Any]]] = []  # (score, plan) — lower = better
    for sym in candidates:
        plan = await build_grid_plan(sym, cfg)
        if plan:
            scored.append((plan["score"], plan))
    scored.sort(key=lambda x: x[0])  # best (most significant relative impulse) first

    created = 0
    used = 0
    while slots > 0 and used < len(scored):
        _, plan = scored[used]
        used += 1
        await create_grid_instance_from_plan(plan, cfg)
        created += 1
        slots -= 1
    leftover = scored[used:]  # already scored, not used to fill a slot

    # --- Replacement pass: slots are full, but the leftover candidates are
    # still worth checking against the weakest idle active grids. ---
    replaced = 0
    if leftover:
        holding_grid_ids = {
            p["grid_id"] for p in await db.grid_positions.find(
                {"status": "open"}, {"_id": 0, "grid_id": 1}
            ).to_list(1000)
        }
        idle_actives = [g for g in active_instances if g["id"] not in holding_grid_ids]
        # Re-score every idle active grid's setup RIGHT NOW (not the value
        # from whenever it was created) — if its setup no longer holds at
        # all (trend faded, price moved off the zone), treat it as
        # infinitely bad so any valid candidate can take its place.
        idle_scored: list[tuple[float, dict[str, Any]]] = []
        for g in idle_actives:
            gplan = await build_grid_plan(g["symbol"], cfg)
            score = gplan["score"] if gplan else float("inf")
            idle_scored.append((score, g))
        idle_scored.sort(key=lambda x: x[0], reverse=True)  # worst (highest/inf) first

        for cand_score, plan in leftover:
            if not idle_scored:
                break
            worst_score, worst_grid = idle_scored[0]
            if worst_score == float("inf"):
                should_replace = True  # that active grid's setup is fully gone
            else:
                margin = abs(worst_score) * (cfg.grid_replace_improvement_pct / 100)
                should_replace = cand_score <= worst_score - margin
            if not should_replace:
                continue
            await db.grid_instances.update_one(
                {"id": worst_grid["id"]},
                {"$set": {"status": "stopped", "stopped_reason": "replaced_by_better_pair"}},
            )
            await create_grid_instance_from_plan(plan, cfg)
            replaced += 1
            idle_scored.pop(0)  # that slot is now taken by the new grid

    return {
        "scanned": len(candidates), "created": created, "replaced": replaced,
        "reversals_closed": reversals_closed,
    }


async def monitor_grid_instances() -> None:
    """Check every active grid's cells against the current price: fill armed
    cells that got dipped into, close holding cells that reached their sell
    target (re-arming them), and emergency-stop a grid if price breaks well
    below its lowest cell (the real risk of a long-only range grid)."""
    instances = await db.grid_instances.find({"status": "active"}, {"_id": 0}).to_list(100)
    if not instances:
        return
    cfg = await get_config()
    wallet = await get_grid_wallet()
    reset_seq = wallet.get("reset_seq", 0)
    cash = wallet.get("cash", 0.0)  # used only to check affordability before a fill

    need_rest = any(price_feed.get(g["symbol"]) is None for g in instances)
    rest_map: dict[str, float] = {}
    if need_rest:
        tickers = await exchange.get_tickers()
        for t in tickers:
            try:
                rest_map[t["symbol"]] = float(t.get("last") or 0)
            except (TypeError, ValueError):
                continue

    for grid in instances:
        cur = price_feed.get(grid["symbol"]) or rest_map.get(grid["symbol"])
        if not cur:
            continue
        cells = grid["cells"]

        # Emergency stop: price fell well below the lowest grid cell — this
        # means the market broke out of the range into a real downtrend.
        lowest_buy = min(c["buy_price"] for c in cells)
        if cur < lowest_buy - cfg.grid_range_break_atr_mult * grid["atr"]:
            holding = await db.grid_positions.find(
                {"grid_id": grid["id"], "status": "open"}, {"_id": 0}
            ).to_list(100)
            for pos in holding:
                exit_notional = cur * pos["quantity"]
                fees = (pos["notional"] + exit_notional) * GRID_FEE_PCT
                pnl = (cur - pos["entry"]) * pos["quantity"] - fees
                # Atomic credit, immediately per position (never accumulated
                # locally and written once — that pattern is what let a
                # concurrent deposit/withdraw get silently overwritten).
                await db.grid_wallet.update_one(
                    {"_id": GRID_WALLET_ID},
                    {"$inc": {"cash": pos["notional"] + pnl}},
                    upsert=True,
                )
                await db.grid_positions.update_one(
                    {"id": pos["id"]},
                    {"$set": {
                        "status": "closed", "close_price": cur,
                        "close_reason": "emergency_stop", "pnl_usdt": round(pnl, 4),
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
            await db.grid_instances.update_one(
                {"id": grid["id"]},
                {"$set": {"status": "stopped", "stopped_reason": "range_break"}},
            )
            continue

        changed = False
        for cell in cells:
            if cell["status"] == "armed" and cur <= cell["buy_price"]:
                notional = grid["notional_per_cell"]
                if notional < 1.0 or notional > cash:
                    continue
                qty = notional / cur
                # Debit atomically FIRST, guarded on the reset counter: if a
                # Reset happened since we read the wallet, this matches
                # nothing and we skip the fill entirely (no ghost position).
                result = await db.grid_wallet.update_one(
                    {"_id": GRID_WALLET_ID, "reset_seq": reset_seq},
                    {"$inc": {"cash": -notional}},
                )
                if result.matched_count == 0:
                    logger.warning("Grid fill aborted: reset happened concurrently for %s", grid["symbol"])
                    continue
                cash -= notional  # keep the local affordability check current for this cycle
                await db.grid_positions.insert_one({
                    "id": str(uuid.uuid4()),
                    "grid_id": grid["id"],
                    "symbol": grid["symbol"],
                    "cell_index": cell["index"],
                    "entry": cur,
                    "target": cell["sell_price"],
                    "quantity": qty,
                    "notional": notional,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "status": "open",
                })
                cell["status"] = "holding"
                changed = True
            elif cell["status"] == "holding" and cur >= cell["sell_price"]:
                pos = await db.grid_positions.find_one(
                    {"grid_id": grid["id"], "cell_index": cell["index"], "status": "open"},
                    {"_id": 0},
                )
                if pos:
                    exit_notional = cur * pos["quantity"]
                    fees = (pos["notional"] + exit_notional) * GRID_FEE_PCT
                    pnl = (cur - pos["entry"]) * pos["quantity"] - fees
                    await db.grid_wallet.update_one(
                        {"_id": GRID_WALLET_ID},
                        {"$inc": {"cash": pos["notional"] + pnl}},
                        upsert=True,
                    )
                    await db.grid_positions.update_one(
                        {"id": pos["id"]},
                        {"$set": {
                            "status": "closed", "close_price": cur,
                            "close_reason": "target", "pnl_usdt": round(pnl, 4),
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
                cell["status"] = "armed"
                changed = True

        if changed:
            await db.grid_instances.update_one(
                {"id": grid["id"]}, {"$set": {"cells": cells}}
            )


async def grid_monitor_loop() -> None:
    """Check grid fills/closes every 3s using the real-time WS price cache —
    same reasoning as the scalping monitor: infrequent checks let price slip
    past a level before the bot notices."""
    await asyncio.sleep(8)
    while True:
        try:
            await monitor_grid_instances()
        except Exception as e:  # noqa: BLE001
            logger.exception("Grid monitor error: %s", e)
        await asyncio.sleep(3)


class GridTransferRequest(BaseModel):
    amount: float


@api.post("/grid/transfer")
async def grid_transfer(req: GridTransferRequest) -> dict[str, Any]:
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")
    main_cash = await get_paper_cash()
    if amount > main_cash:
        raise HTTPException(
            status_code=400,
            detail=f"Fondi insufficienti nel portafoglio principale (disponibili: {round(main_cash, 2)})",
        )
    await set_paper_cash(main_cash - amount)
    # Atomic increment — same reasoning as the Scalping Bot's transfer: never
    # clobbers a concurrent change to "cash" made by a cell filling/closing.
    updated = await db.grid_wallet.find_one_and_update(
        {"_id": GRID_WALLET_ID},
        {"$inc": {"cash": amount, "total_transferred_in": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return {"ok": True, "grid_cash": updated.get("cash", amount), "main_cash": main_cash - amount}


@api.post("/grid/withdraw")
async def grid_withdraw(req: GridTransferRequest) -> dict[str, Any]:
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")
    wallet = await get_grid_wallet()
    grid_cash = wallet.get("cash", 0.0)
    if amount > grid_cash:
        raise HTTPException(
            status_code=400,
            detail=f"Fondi insufficienti nel portafoglio griglia (disponibili: {round(grid_cash, 2)})",
        )
    updated = await db.grid_wallet.find_one_and_update(
        {"_id": GRID_WALLET_ID},
        {"$inc": {"cash": -amount, "total_transferred_in": -amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    main_cash = await get_paper_cash()
    await set_paper_cash(main_cash + amount)
    return {"ok": True, "grid_cash": updated.get("cash", 0.0), "main_cash": main_cash + amount}


@api.post("/grid/reset")
async def grid_reset() -> dict[str, Any]:
    """Reset del Grid Bot: cancella tutte le griglie attive/fermate, le
    posizioni aperte/chiuse e azzera il portafoglio griglia. NON tocca il
    portafoglio principale."""
    current = await get_grid_wallet()
    next_seq = current.get("reset_seq", 0) + 1
    await db.grid_positions.delete_many({})
    await db.grid_instances.delete_many({})
    await save_grid_wallet({"cash": 0.0, "total_transferred_in": 0.0, "reset_seq": next_seq})
    return {"ok": True, "cash": 0.0}


@api.get("/grid/portfolio")
async def grid_portfolio() -> dict[str, Any]:
    wallet = await get_grid_wallet()
    cash = wallet.get("cash", 0.0)
    open_positions = await db.grid_positions.find({"status": "open"}, {"_id": 0}).to_list(1000)
    tickers = await exchange.get_tickers()
    price_map: dict[str, float] = {}
    for t in tickers:
        try:
            price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue
    unrealized = 0.0
    allocated = 0.0
    enriched: list[dict[str, Any]] = []
    for p in open_positions:
        cur = price_map.get(p["symbol"], p["entry"])
        gross_pnl = (cur - p["entry"]) * p["quantity"]
        notional = p["entry"] * p["quantity"]
        exit_notional = cur * p["quantity"]
        est_fees = (notional + exit_notional) * GRID_FEE_PCT
        pnl = gross_pnl - est_fees
        pnl_pct = (pnl / notional) * 100 if notional > 0 else 0.0
        unrealized += pnl
        allocated += notional
        enriched.append({
            **p, "current_price": round(cur, 8),
            "unrealized_pnl": round(pnl, 4), "unrealized_pnl_pct": round(pnl_pct, 2),
        })
    closed = await db.grid_positions.find(
        {"status": "closed"}, {"_id": 0}
    ).sort("closed_at", -1).to_list(200)
    realized = sum(c.get("pnl_usdt", 0.0) for c in closed)
    wins = sum(1 for c in closed if c.get("pnl_usdt", 0.0) > 0)
    losses = sum(1 for c in closed if c.get("pnl_usdt", 0.0) <= 0)
    settled = wins + losses
    win_rate = round((wins / settled) * 100, 1) if settled else 0.0
    equity = cash + allocated + unrealized
    total_in = wallet.get("total_transferred_in", 0.0)
    discrepancy = round((cash + allocated) - (total_in + realized), 4)
    return {
        "cash": round(cash, 4),
        "allocated": round(allocated, 4),
        "unrealized_pnl": round(unrealized, 4),
        "realized_pnl": round(realized, 4),
        "equity": round(equity, 4),
        "total_transferred_in": round(total_in, 4),
        "ledger_discrepancy_usdt": discrepancy,
        "open_positions": enriched,
        "closed_positions": closed,
        "open_count": len(enriched),
        "closed_count": len(closed),
        "win_rate": win_rate,
    }


@api.get("/grid/instances")
async def grid_instances_list() -> dict[str, Any]:
    """Active/stopped grids with their cell levels and current price — this
    is what the frontend chart uses to draw the grid lines over the price."""
    instances = await db.grid_instances.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)
    tickers = await exchange.get_tickers()
    price_map: dict[str, float] = {}
    for t in tickers:
        try:
            price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue
    for g in instances:
        g["current_price"] = price_map.get(g["symbol"])
    return {"instances": instances, "count": len(instances)}


app.include_router(api)
