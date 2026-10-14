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
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}
TF_SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
DEFAULT_TIMEFRAMES = ["1h", "4h"]
PAPER_FEE_PCT = 0.001  # 0.10% Bybit spot fee per side (open + close = 0.20% round trip) — same real-world assumption as SCALPING_FEE_PCT/RSI_REBOUND_FEE_PCT, used here only to estimate a safe trailing-activation margin for the traditional strategies (their booked PnL itself doesn't model fees)
CANDLE_LIMIT = 200  # candles fetched per pair/tf

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class Config(BaseModel):
    scan_interval_minutes: int = 1
    timeframes: list[str] = Field(default_factory=lambda: DEFAULT_TIMEFRAMES.copy())  # kept for backward compatibility with old stored configs — no longer read directly by the scan loop, see the three per-strategy lists below
    rsi_reversion_timeframes: list[str] = Field(default_factory=lambda: DEFAULT_TIMEFRAMES.copy())
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
    partial_close_enabled: bool = True
    partial_close_r: float = 1.0
    partial_close_pct: float = 35.0  # % of position closed at partial_close_r
    liq_min_distance_pct: float = 25.0  # leverage: min distance from liquidation (%)
    # --- LEGACY (Rev Pre-FVG was removed; fields kept so the Settings screen and stored configs keep working) ---
    consolidation_min_candles: int = 3
    consolidation_max_atr: float = 1.5  # channel width <= this * ATR
    tp1_pct: float = 65.0  # % closed at TP1
    # --- LEGACY counter-trend RSI filters (strategy removed; kept for compatibility) ---
    rsi_high_tf_ob: float = 80.0  # higher-TF overbought (short)
    rsi_high_tf_os: float = 20.0  # higher-TF oversold (long)
    trailing_pct_from_entry: float = 1.0  # counter-trend trailing distance %
    post_tp1_advance_pct: float = 0.5  # % beyond TP1 before moving SL to TP1 (net fees)
    # --- Parallel strategy selection ---
    enabled_strategies: list[str] = Field(default_factory=list)  # only "rsi_reversion" is acted upon here (the other names are ignored); empty = ["rsi_reversion"]
    live_strategies: list[str] = Field(default_factory=list)  # which strategies currently trade with REAL money via Bybit — empty means ALL of them are in paper mode (the safe default). No trading logic reads this yet; it's wired strategy-by-strategy as each one gets connected to real order placement.
    # --- LEGACY FVG Reversal params (strategy removed; kept for compatibility) ---
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
    exhaustion_lookback: int = 20  # was 10 — too small a window left condition #3 (needs 2 confirmed RSI pivots) almost no room to ever find them
    exhaustion_min_score: float = 1.0  # was 2.0 — lowered so it stops being an extra hard gate; raise back to 2.0 (or more) in Settings any time
    rsi_divergence_check_enabled: bool = True  # Rev Pre-FVG, FVG Reversal, RSI Reversion: require RSI/price divergence coherent with the trade direction
    trend_htf_check_enabled: bool = True  # Rev Pre-FVG, FVG Reversal: require a genuine (non-range) trend on the higher timeframe before considering a setup
    exhaustion_check_enabled: bool = True  # Rev Pre-FVG, FVG Reversal: require the trend-exhaustion score to clear exhaustion_min_score
    trend_structure_strict: bool = False  # False = only ONE of higher-high/higher-low (or the "down" mirror) is needed to call a trend, not both — set True in Settings to go back to the strict textbook definition
    trailing_enabled: bool = True  # used by the 3 traditional strategies (Rev Pre-FVG, FVG Reversal, RSI Reversion): once price reaches the original target, arm a trailing stop instead of closing immediately, to let a strong run continue
    trailing_atr_mult: float = 0.5  # how far (in ATR multiples) price may pull back from its post-target peak before the trailing stop closes the trade
    # --- RSI Reversion strategy (independent, simple): RSI extreme -> confirmed
    # reentry -> target back near RSI-50 (proxied by price returning to its own
    # N-period average). Deliberately no tight stop, only a wide catastrophic
    # one, on the assumption overbought/oversold eventually rebalances. ---
    rsi_rev_overbought: float = 80.0
    rsi_rev_oversold: float = 20.0
    rsi_rev_min_extreme_candles: int = 3  # min candles RSI must stay beyond 80/20 before reentry counts
    rsi_rev_catastrophic_atr_mult: float = 3.0  # wide safety stop, only for extreme/structural cases — lowered from 5-6: combined with the new min R:R gate below, an overly wide floor here was rejecting/mismatching too many otherwise-valid setups
    rsi_rev_structural_lookback: int = 10  # candles used to find the recent swing high/low that now anchors the stop, with the ATR buffer above only as a minimum safety margin
    rsi_rev_min_rr_ratio: float = 1.5  # was 3.0 — zero trades fired in days at that bar; still requires a genuinely favorable setup (reward at least 1.5x the risk), just not an extreme one that almost never occurs naturally
    rsi_rev_trailing_atr_mult: float = 3.0  # wide trailing once in profit — only to catch a genuine sudden reversal, not to lock in small moves
    rsi_rev_trailing_activation_margin_pct: float = 0.5  # trailing now activates only once profit clears an ESTIMATED round-trip fee cost plus this extra % — not at the very first cent of profit, which was too easy to trigger on pure noise
    # --- Shared BTC market regime (built for the removed Top 10 Long strategy; kept on purpose because 33/60 and the position-size trim read it) ---
    top10_universe_size: int = 10  # how many coins to consider each scan, ranked by 24h volume (proxy for market cap)
    top10_ema_fast: int = 20
    top10_ema_medium: int = 50
    top10_ema_slow: int = 200
    s3360_enabled: bool = True
    s3360_timeframes: list[str] = Field(default_factory=lambda: ["1h"])
    s3360_rsi_period: int = 14
    s3360_low_threshold: float = 35.0  # entry: RSI crosses down through this
    s3360_high_threshold: float = 60.0  # exit target: RSI reaching this closes the trade
    s3360_max_open_positions: int = 4  # ALSO doubles as the capital-sizing divisor: each trade gets equity/max_open_positions — e.g. 2 slots = 50% each, 4 slots = 25% each. Not just a cap on count.
    s3360_max_daily_rise_pct: float = 0.0  # entry filter: skip a coin that is already up more than this % since the current UTC day opened (0 = filter off, the old behaviour)
    s3360_min_atr_pct: float = 0.0  # entry filter: skip when the timeframe's ATR (as % of price) is below this — in a dead-flat market the rebounds are tiny (0 = filter off)
    s3360_hold_below_entry: bool = True  # exit rule: when RSI reaches the target but the price is still below the entry (plus the margin below), keep the position open instead of closing at a loss
    s3360_min_exit_gain_pct: float = 0.25  # how far above the entry price the exit must be (0.25 ≈ round-trip fees 0.2% plus a hair, so a close is never a net loss); only used with hold_below_entry
    xrp_acc_enabled: bool = True
    xrp_acc_timeframes: list[str] = Field(default_factory=lambda: ["1h"])
    xrp_acc_rsi_period: int = 14
    xrp_acc_low_threshold: float = 25.0  # entry: buy ALL trading capital when RSI drops to/below this
    xrp_acc_high_threshold: float = 60.0  # exit: sell everything when RSI rises to/above this
    xrp_acc_hold_below_entry: bool = True  # exit rule: when RSI reaches the target but the price is still below the entry (plus the margin below), keep the position open instead of selling at a loss
    xrp_acc_min_exit_gain_pct: float = 0.4  # how far above the entry price the exit must be (0.4 = round-trip fees 0.2% plus a cushion, so a sale is never a net loss); only used with hold_below_entry
    sim_realistic_fills: bool = True  # paper fills of 33/60 and XRP Accumulation use the REAL order book (buy at the ask side, sell at the bid side, walking the depth) instead of the last traded price
    sim_fallback_slippage_pct: float = 0.05  # when the order book cannot be read (or is too thin for the order), the fill is the last price made worse by this % per side

    regime_risk_reduction_pct: float = 50.0  # position size cut applied to RSI Reversion and 33/60 whenever BTC's regime isn't clearly bullish (range or bearish) — trims risk during an uncertain/consolidating phase instead of sizing every trade the same


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
    strategy: str = "counter_trend"  # "counter_trend" | "fvg_reversal" | "rsi_reversion" (the first two only appear in old records)
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
    atr: float = 0.0  # copied from the signal at open — used by RSI Reversion's trailing stop
    peak_price: Optional[float] = None  # RSI Reversion trailing: best price seen since armed


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
    timeframe: Optional[str] = None
    stop_loss: Optional[float] = None  # the ORIGINAL stop set on open — kept for the closed-trade record so screens don't have to fabricate a placeholder
    take_profit: Optional[float] = None  # the ORIGINAL target set on open (may differ from the actual exit price if closed some other way, e.g. trailing)


class ExchangeConnectRequest(BaseModel):
    api_key: str
    api_secret: str
    api_passphrase: Optional[str] = None  # unused for Bybit; kept for compatibility


# ---------------------------------------------------------------------------
# Encryption for exchange credentials (at-rest)
# ---------------------------------------------------------------------------
KEY_PATH = ROOT_DIR / ".fernet_key"


def _load_or_create_fernet() -> Fernet:
    # Preferred: derive the key from the ENCRYPTION_SECRET variable (set on
    # Railway). The container's filesystem is wiped on every deploy, so a key
    # file alone would be regenerated each time and every stored credential
    # would silently become undecryptable. A variable survives deploys.
    secret = os.environ.get("ENCRYPTION_SECRET", "").strip()
    if secret:
        derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(derived)
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

    async def get_klines(self, symbol: str, tf: str, limit: int = CANDLE_LIMIT) -> list[list[float]]:
        bybit_tf = TF_MAP.get(tf)
        if not bybit_tf:
            return []
        data = await self._get(
            "/v5/market/kline",
            params={
                "category": self.category,
                "symbol": symbol,
                "interval": bybit_tf,
                "limit": limit + 1,
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
        for row in raw[-limit:]:
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
        p = self.prices.get(symbol)
        if p is None:
            return None
        # A price that hasn't been refreshed in 10s is NOT a live price: once a
        # coin stops being followed over the websocket its last tick used to
        # stay in this cache forever, and every caller kept reading it as if
        # it were current — producing positions that "hit" a target or a stop
        # within a second of opening, against a price that was hours old.
        # Returning None here makes every caller fall back to a fresh REST
        # price (they all already do).
        if time.time() - self.updated_at.get(symbol, 0) > 10:
            return None
        return p

    async def desired_symbols(self) -> list[str]:
        docs = await db.paper_positions.find({}, {"symbol": 1, "_id": 0}).to_list(1000)
        symbols = {d["symbol"] for d in docs}
        # The independent strategies' open positions need live prices too —
        # otherwise their floating P&L falls back to the entry price (shown
        # as 0.00) for any coin the websocket isn't following.
        for coll in (
            db.s3360_positions,
            db.xrp_acc_positions,
        ):
            rows = await coll.find({"status": "open"}, {"symbol": 1, "_id": 0}).to_list(1000)
            symbols |= {d["symbol"] for d in rows}
        # XRP Accumulation checks its entry every few seconds, so XRPUSDC must
        # be followed live permanently, not only while a position is open.
        if (await get_config()).xrp_acc_enabled:
            symbols.add(XRP_ACC_SYMBOL)
        return sorted(symbols)

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
# the three traditional strategies. By default a
# strategy has no dedicated wallet and draws from/settles to the SHARED main
# paper wallet (get_paper_cash/set_paper_cash above) exactly as before. If the
# user allocates funds to a specific strategy, that strategy gets its own
# isolated cash pool and stops touching the shared one, until deallocated.
# ---------------------------------------------------------------------------
STRATEGY_WALLET_NAMES = ("rsi_reversion",)


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

    # Guard against a stale signal: stop_loss/take_profit are absolute market
    # levels computed from the signal's (older) entry price, not adjusted for
    # the fresh fill_price above. If price already moved past either level by
    # the time we actually get here, the position would be born already
    # "at/beyond target" (or already past its stop) — the very next check
    # would then close it immediately using that stale level as the exit
    # price, which can produce a NEGATIVE real pnl while still being labeled
    # a "win" (target hit) simply because price is on the far side of it.
    # Cleanest fix: skip opening entirely rather than open a trade whose
    # premise the market has already invalidated.
    if signal["side"] == "long":
        if fill_price >= signal["take_profit"] or fill_price <= signal["stop_loss"]:
            return None
    else:
        if fill_price <= signal["take_profit"] or fill_price >= signal["stop_loss"]:
            return None

    risk_usdt = max(1.0, cash * pcfg.risk_per_trade_pct / 100)
    if strategy == "rsi_reversion":
        cfg = await get_config()
        risk_usdt *= await get_regime_size_multiplier(cfg)
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
        atr=float(signal.get("atr", 0.0)),
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
        timeframe=pos.get("timeframe"),
        stop_loss=pos.get("current_stop") or pos.get("stop_loss"),
        take_profit=pos.get("tp2") or pos.get("tp1") or pos.get("take_profit"),
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
        timeframe=pos.get("timeframe"),
        stop_loss=pos.get("current_stop") or pos.get("stop_loss"),
        take_profit=pos.get("tp2") or pos.get("tp1") or pos.get("take_profit"),
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


async def manage_rsi_reversion_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """RSI Reversion: intentionally no partial close, no breakeven, no
    timeout-to-breakeven — the whole premise is to wait for the rebalance
    without being cut early by normal noise. The ONE addition here is a wide
    ATR-based trailing stop, armed once profit clears an estimated round-trip
    fee cost plus a small safety margin — meant purely to catch a genuine
    sudden reversal once the trade has a real edge, not to lock in noise.
    Before this, only the far-away catastrophic stop (3×ATR by default)
    protected a winning trade from giving everything back."""
    if not cfg.trailing_enabled:
        return pos
    entry = float(pos.get("fill_price") or pos.get("entry") or 0)
    atr = float(pos.get("atr") or 0)
    if entry <= 0 or atr <= 0:
        return pos
    long = pos["side"] == "long"
    in_profit = (price > entry) if long else (price < entry)

    if not pos.get("trailing_active"):
        if not in_profit:
            return pos
        # Activate only once profit clears an estimated round-trip fee cost
        # plus a small safety margin — not at the very first cent of profit,
        # which was too easy to trigger on pure market noise right after entry.
        qty = float(pos.get("quantity") or 0)
        notional = entry * qty
        gross_pnl_so_far = (price - entry) * qty if long else (entry - price) * qty
        exit_notional_now = price * qty
        fees_now = (notional + exit_notional_now) * PAPER_FEE_PCT
        margin_now = notional * cfg.rsi_rev_trailing_activation_margin_pct / 100
        if gross_pnl_so_far <= fees_now + margin_now:
            return pos
        await db.paper_positions.update_one(
            {"id": pos["id"]},
            {"$set": {"trailing_active": True, "peak_price": price}},
        )
        return {**pos, "trailing_active": True, "peak_price": price}

    peak = pos.get("peak_price") or price
    new_peak = max(peak, price) if long else min(peak, price)
    if new_peak != peak:
        await db.paper_positions.update_one(
            {"id": pos["id"]}, {"$set": {"peak_price": new_peak}}
        )
        pos = {**pos, "peak_price": new_peak}

    trail_level = (
        new_peak - cfg.rsi_rev_trailing_atr_mult * atr if long
        else new_peak + cfg.rsi_rev_trailing_atr_mult * atr
    )
    hit = price <= trail_level if long else price >= trail_level
    if hit:
        outcome = "win" if ((price - entry) if long else (entry - price)) >= 0 else "loss"
        await close_paper_position(pos, price, outcome)
        return {**pos, "quantity": 0}
    return pos


async def manage_open_position(pos: dict[str, Any], price: float, cfg: Config) -> dict[str, Any]:
    """Run the 3 additive managers + partial close. Returns the possibly-updated
    position dict (with fresh current_stop)."""
    updates: dict[str, Any] = {}
    # RSI Reversion: plain SL/TP, no partial close, no timeout-to-breakeven —
    # only a wide profit-protecting trailing stop (see above).
    if pos.get("strategy") == "rsi_reversion":
        return await manage_rsi_reversion_position(pos, price, cfg)
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

    def extreme_count_in_window(threshold: float, above: bool, window: int = 8) -> int:
        """Count how many of the last `window` candles before the current
        one were beyond `threshold` — NOT requiring an unbroken consecutive
        run. Real RSI rarely holds a threshold with zero noise; a single
        candle dipping back inside for one bar was resetting the whole count
        to 0 under the old strict-consecutive check, even after a genuinely
        sustained extreme move."""
        count = 0
        i = len(rsis) - 2
        checked = 0
        while i >= 0 and rsis[i] is not None and checked < window:
            beyond = (rsis[i] >= threshold) if above else (rsis[i] <= threshold)
            if beyond:
                count += 1
            checked += 1
            i -= 1
        return count

    side: Optional[str] = None
    # Filter 1 (candle-close confirmation) + Filter 3 (min time in extreme
    # zone) are both checked here: the reentry must be on THIS closed candle,
    # preceded by enough candles genuinely beyond the threshold recently.
    if rsis[-1] < ob and extreme_count_in_window(ob, above=True) >= min_extreme:
        side = "short"
    elif rsis[-1] > os_ and extreme_count_in_window(os_, above=False) >= min_extreme:
        side = "long"
    if side is None:
        await log_reject(symbol, tf, STRAT, "nessun rientro da zona estrema")
        return None
    if side == "short" and (await get_paper_config()).trading_mode == "spot":
        await log_reject(symbol, tf, STRAT, "short non eseguibile in modalità spot")
        return None

    # Filter 2: genuine RSI/price divergence, not just a brief dip back inside
    # the bands (reuses the same detector as the other strategies).
    divergence = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if cfg.rsi_divergence_check_enabled and side == "short" and divergence != "bearish":
        await log_reject(symbol, tf, STRAT, "divergenza RSI non coerente")
        return None
    if cfg.rsi_divergence_check_enabled and side == "long" and divergence != "bullish":
        await log_reject(symbol, tf, STRAT, "divergenza RSI non coerente")
        return None

    atr = atr_wilder(highs, lows, closes, cfg.atr_period)
    if not atr or atr <= 0:
        await log_reject(symbol, tf, STRAT, "ATR non calcolabile")
        return None

    entry = closes[-1]
    sma = sum(closes[-cfg.rsi_period:]) / cfg.rsi_period  # proxy for "RSI back to 50"
    catastrophic_buffer = cfg.rsi_rev_catastrophic_atr_mult * atr
    lookback_n = min(cfg.rsi_rev_structural_lookback, len(closes) - 1)

    if side == "short":
        if sma >= entry:
            await log_reject(symbol, tf, STRAT, "nessun margine di rientro (prezzo già sulla media)")
            return None  # price already at/below its average, no reversion left to capture
        take_profit = sma
        # Structural stop: above the recent swing high (the level whose
        # break genuinely invalidates the reversal thesis), with the old
        # ATR buffer only as a minimum safety margin if that level sits
        # implausibly close to entry.
        structural_level = max(highs[-lookback_n:]) * 1.002
        stop_loss = max(structural_level, entry + catastrophic_buffer)
    else:
        if sma <= entry:
            await log_reject(symbol, tf, STRAT, "nessun margine di rientro (prezzo già sulla media)")
            return None
        take_profit = sma
        structural_level = min(lows[-lookback_n:]) * 0.998
        stop_loss = min(structural_level, entry - catastrophic_buffer)

    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    if risk <= 0 or reward <= 0:
        await log_reject(symbol, tf, STRAT, "target/stop non validi")
        return None
    if (reward / risk) < cfg.rsi_rev_min_rr_ratio:
        await log_reject(symbol, tf, STRAT, f"R:R naturale insufficiente (sotto 1:{cfg.rsi_rev_min_rr_ratio:g})")
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
    defaults to RSI Reversion — the only strategy left in this pipeline (Rev
    Pre-FVG, FVG Reversal, "scoring" and "impulse_fvg" were removed)."""
    if cfg.enabled_strategies:
        # The stored list may still name the strategies that were removed
        # (Rev Pre-FVG, FVG Reversal): only RSI Reversion is acted upon.
        return {"rsi_reversion"} & set(cfg.enabled_strategies)
    return {"rsi_reversion"}


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

        # Sort by volume descending and cap — THEN check stability, only on
        # the already-capped shortlist. Checking stability before capping
        # meant a full daily-candle fetch for every symbol that merely
        # passed the basic volume filter (potentially 100+ pairs) instead
        # of just the handful actually being considered — this alone could
        # stall an entire scan cycle for minutes.
        pairs.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
        pairs = pairs[: cfg.max_pairs_per_scan]
        pairs = [p for p in pairs if await is_volume_stable(p, cfg)]

        active = active_strategies(cfg)
        all_tfs = sorted(set(cfg.rsi_reversion_timeframes))
        logger.info("Scanning %d pairs across %s", len(pairs), all_tfs)
        signals_found: list[Signal] = []

        async def process(sym: str) -> None:
            for tf in all_tfs:
                try:
                    if "rsi_reversion" in active and tf in cfg.rsi_reversion_timeframes:
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
            await run_s3360_scan()
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


async def s3360_monitor_loop() -> None:
    """Check 33/60's live-RSI target every 3s using the real-time WS price
    cache — same reasoning as the other monitors."""
    await asyncio.sleep(8)
    while True:
        try:
            await monitor_s3360_positions()
        except Exception as e:  # noqa: BLE001
            logger.exception("33/60 monitor error: %s", e)
        await asyncio.sleep(3)


async def xrp_acc_monitor_loop() -> None:
    """Check XRP Accumulation every 3s using the real-time WS price cache:
    the exit (RSI target) AND the entry (RSI dip). The entry used to run only
    inside the main scan cycle, which takes 3-4 minutes, so a dip under the
    threshold that lasted less than that could be missed. This is the ONLY
    caller of the entry check, on purpose: two callers could both see 'no open
    position' and open two at once."""
    await asyncio.sleep(8)
    while True:
        try:
            await monitor_xrp_acc_positions()
        except Exception as e:  # noqa: BLE001
            logger.exception("XRP Accumulation monitor error: %s", e)
        try:
            await run_xrp_acc_scan()
        except Exception as e:  # noqa: BLE001
            logger.exception("XRP Accumulation entry check error: %s", e)
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
    s3360_monitor_task = asyncio.create_task(s3360_monitor_loop())
    xrp_acc_monitor_task = asyncio.create_task(xrp_acc_monitor_loop())
    ws_task = asyncio.create_task(price_feed.run())
    premature_task = asyncio.create_task(premature_stop_loop())
    entry_timing_task = asyncio.create_task(entry_timing_loop())
    log_prune_task = asyncio.create_task(log_prune_loop())
    yield
    scan_task.cancel()
    monitor_task.cancel()
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


@api.get("/events")
async def get_events(limit: int = 100) -> dict[str, Any]:
    """Unified chronological feed of open/close events for the strategies
    sharing paper_positions/paper_trades (today only RSI Reversion; older
    entries from the removed strategies are still listed) — powers the
    app's single 'Eventi' screen."""
    events: list[dict[str, Any]] = []

    opens = await db.paper_positions.find({}, {"_id": 0}).sort("opened_at", -1).to_list(limit)
    for p in opens:
        events.append({
            "id": f"{p['id']}_open", "type": "open",
            "section": p.get("strategy", "counter_trend"),
            "symbol": p["symbol"], "side": p.get("side"),
            "pnl_usdt": None, "at": p["opened_at"],
        })
    closes = await db.paper_trades.find({}, {"_id": 0}).sort("closed_at", -1).limit(limit).to_list(limit)
    for t in closes:
        events.append({
            "id": f"{t['id']}_close", "type": "close",
            "section": t.get("strategy", "counter_trend"),
            "symbol": t["symbol"], "side": t.get("side"),
            "pnl_usdt": t.get("pnl_usdt"), "at": t["closed_at"],
        })

    events.sort(key=lambda e: e["at"], reverse=True)
    trimmed = events[:limit]
    return {"events": trimmed, "count": len(trimmed)}


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
async def get_candles(symbol: str, timeframe: str = "1h", limit: int = CANDLE_LIMIT) -> dict[str, Any]:
    limit = max(1, min(limit, 1000))  # Bybit's own hard cap per request
    candles = await exchange.get_klines(symbol, timeframe, limit=limit)
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
async def strategy_debug_log(
    limit: int = 1000, minutes: int = 180, symbol: Optional[str] = None
) -> dict[str, Any]:
    """Bottleneck analysis for the 3 traditional strategies: which specific
    check rejects the most candidates, broken down per strategy. Looks at
    the last `minutes` of scans (default 3h) so it reflects current market
    conditions rather than the whole history. Pass `symbol` (e.g. HYPEUSDT)
    to see exactly why one specific pair was rejected across every strategy
    that scanned it, instead of only the aggregate counts."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    query: dict[str, Any] = {"at": {"$gte": since}}
    if symbol:
        query["symbol"] = symbol.upper()
    cursor = db.strategy_debug_log.find(query, {"_id": 0}).sort("at", -1).limit(limit)
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

    result: dict[str, Any] = {
        "window_minutes": minutes,
        "count": len(logs),
        "reason_counts_by_strategy": by_strategy,
        "bottleneck_by_strategy": bottleneck_by_strategy,
    }
    if symbol:
        result["symbol"] = symbol.upper()
        result["entries"] = logs  # raw, timestamped entries for this one pair
    return result


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
    # Rounding-display tolerance: the on-screen balance is rounded to cents,
    # so the real stored value can sit a tiny fraction below it after many
    # small accumulated operations. If the request is within a cent of
    # what's actually there, withdraw exactly what's available instead of
    # rejecting a withdrawal of "the whole displayed balance" — but never
    # adjust upward, so this can't ever push cash negative.
    w_check = await get_strategy_wallet(strategy)
    available = w_check.get("cash", 0.0) if w_check else 0.0
    if amount > available and (amount - available) <= 0.01:
        amount = available
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


@api.post("/strategy/{strategy}/reset")
async def strategy_reset(strategy: str) -> dict[str, Any]:
    """Reset a single strategy's own history: removes its open positions and
    closed trades. If it has an isolated wallet, undoes just the
    accumulated trading P&L from its cash (keeps whatever was allocated
    intact). Does NOT touch the
    other two strategies, and does NOT touch the shared main wallet if this
    strategy is currently on the shared pool (cash there isn't
    attributable to a single strategy)."""
    if strategy not in STRATEGY_WALLET_NAMES:
        raise HTTPException(status_code=400, detail="invalid strategy")
    w = await get_strategy_wallet(strategy)
    if w is not None:
        closed = await db.paper_trades.find({"strategy": strategy}, {"_id": 0}).to_list(10000)
        realized = sum(t.get("pnl_usdt", 0.0) for t in closed)
        open_positions = await db.paper_positions.find({"strategy": strategy}, {"_id": 0}).to_list(1000)
        locked = sum(p["entry"] * p["quantity"] for p in open_positions)
        new_cash = w.get("cash", 0.0) - realized + locked
        await db.strategy_wallets.update_one({"_id": strategy}, {"$set": {"cash": new_cash}})
    await db.paper_positions.delete_many({"strategy": strategy})
    await db.paper_trades.delete_many({"strategy": strategy})
    return {"ok": True, "strategy": strategy}


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
        # The strategies trade USDC pairs, so USDC has to be reported too —
        # summing only USDT showed 0 even with funds sitting in USDC.
        totals = {"USDT": 0.0, "USDC": 0.0}
        for acc in data.get("result", {}).get("list", []):
            for coin in acc.get("coin", []):
                name = coin.get("coin")
                if name in totals:
                    try:
                        totals[name] += float(coin.get("walletBalance") or 0)
                    except (TypeError, ValueError):
                        continue
        return {
            "connected": True,
            "exchange": "bybit",
            "api_key_masked": doc.get("api_key_masked", ""),
            "usdt_balance": round(totals["USDT"], 2),
            "usdc_balance": round(totals["USDC"], 2),
            "connected_at": doc.get("connected_at"),
        }
    except (httpx.HTTPError, InvalidToken) as e:
        return {"connected": False, "exchange": "bybit", "error": str(e)}


@api.post("/exchange/connect")
async def exchange_connect(req: ExchangeConnectRequest) -> dict[str, Any]:
    if not os.environ.get("ENCRYPTION_SECRET", "").strip():
        # Without it the encryption key lives in a file that is wiped on every
        # deploy, so the saved credentials would become unreadable. Refuse
        # instead of saving something that silently disappears.
        raise HTTPException(
            status_code=400,
            detail="Prima imposta la variabile ENCRYPTION_SECRET su Railway: senza, la chiave salvata andrebbe persa a ogni aggiornamento del bot.",
        )
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


# ============================================================================
# SHARED BTC MARKET REGIME — built for the removed Top 10 Long strategy and
# kept on purpose: 33/60 and the position-size trim read it through
# get_regime_size_multiplier(). Do NOT delete the "top10" helpers below.
# ============================================================================


EXCLUDED_STABLE_BASES = ("USDC", "USDT", "BUSD", "DAI", "TUSD", "USDE", "FDUSD", "USDP", "GUSD")  # stablecoin base assets to skip — a stablecoin-vs-stablecoin pair has near-zero volatility and is useless for any of these strategies.


async def is_volume_stable(symbol: str, cfg: Config) -> bool:
    """True if 24h volume has stayed above the configured minimum for each
    of the last 10 days — guards against picking a coin whose current
    volume is just a short-lived spike about to collapse, rather than
    genuine sustained liquidity."""
    try:
        daily = await exchange.get_klines(symbol, "1d")
    except Exception:  # noqa: BLE001
        return True  # fail open on a transient API hiccup — don't block all trading over it
    if len(daily) < 10:
        return False  # too new to have proven itself yet
    recent = daily[-10:]
    return all((c[5] * c[2]) >= cfg.min_24h_volume_usdt for c in recent)


_shared_regime_cache: dict[str, Any] = {"regime": None, "at": 0.0}


async def get_shared_market_regime(cfg: Config) -> str:
    """BTC's regime (bullish/range/bearish) on 1h — shared by RSI Reversion
    and 33/60 so position sizing can be trimmed during an
    uncertain/consolidating phase, not just an outright downtrend. The
    helpers it builds on still carry the "top10" name because they were
    written for the (removed) Top 10 Long strategy — they are kept on purpose.
    Cached for 5 minutes so every position-open across every strategy
    doesn't each trigger a fresh BTC candle fetch."""
    now = time.time()
    if _shared_regime_cache["regime"] is not None and (now - _shared_regime_cache["at"]) < 300:
        return _shared_regime_cache["regime"]
    regime = "range"
    try:
        universe = await get_top10_universe(cfg)
        btc_symbol = next((s for s in universe if s.startswith("BTC")), None)
        if btc_symbol:
            trend_score, _ = await compute_top10_trend_score(btc_symbol, cfg)
            if trend_score >= 65:
                regime = "bullish"
            elif trend_score < 40:
                regime = "bearish"
            else:
                regime = "range"
    except Exception:  # noqa: BLE001
        regime = "range"  # fail safe to cautious sizing rather than crash
    _shared_regime_cache["regime"] = regime
    _shared_regime_cache["at"] = now
    return regime


async def get_regime_size_multiplier(cfg: Config) -> float:
    """1.0 in a clearly bullish regime; reduced (by
    `regime_risk_reduction_pct`) otherwise — trims position size during a
    consolidating or bearish phase instead of sizing every trade the same
    regardless of how uncertain conditions currently are."""
    regime = await get_shared_market_regime(cfg)
    if regime == "bullish":
        return 1.0
    return max(0.0, 1.0 - cfg.regime_risk_reduction_pct / 100)


async def get_top10_universe(cfg: Config) -> list[str]:
    """Top N coins by 24h volume (proxy for market cap — this exchange has
    no market-cap feed), deduplicated by base asset across all supported
    quote currencies so we pick the single most-liquid pair per coin,
    excluding stablecoins."""
    tickers = await exchange.get_tickers()
    vol_map: dict[str, float] = {}
    for t in tickers:
        try:
            vol_map[t["symbol"]] = float(t.get("volValue") or 0)
        except (TypeError, ValueError):
            continue
    symbols = await exchange.get_symbols()
    quotes = {q.strip() for q in (cfg.quote_filter or "").split(",") if q.strip()}
    best_per_base: dict[str, tuple[str, float]] = {}
    for s in symbols:
        if not s.get("enableTrading"):
            continue
        sym = s.get("symbol")
        quote = s.get("quoteCurrency")
        if not sym or not quote:
            continue
        if quotes and quote not in quotes:
            continue
        # Derive the base coin from the symbol string itself (strip the
        # quote suffix) rather than trusting the exchange's baseCurrency
        # field — that field isn't reliably populated for every pair (the
        # same issue that broke the stablecoin exclusion in Scalping).
        if not sym.upper().endswith(quote.upper()):
            continue
        base = sym.upper()[: -len(quote)]
        if not base:
            continue
        if base in EXCLUDED_STABLE_BASES or sym.upper().startswith(tuple(EXCLUDED_STABLE_BASES)):
            continue
        vol = vol_map.get(sym, 0)
        if vol < cfg.min_24h_volume_usdt:
            continue
        if base not in best_per_base or vol > best_per_base[base][1]:
            best_per_base[base] = (sym, vol)
    ranked = sorted(best_per_base.values(), key=lambda x: x[1], reverse=True)
    # Stability check runs only while walking the ranked list, stopping once
    # we have enough — not on every deduped coin up front.
    out: list[str] = []
    for sym, _ in ranked:
        if len(out) >= cfg.top10_universe_size:
            break
        if await is_volume_stable(sym, cfg):
            out.append(sym)
    return out


async def compute_top10_trend_score(symbol: str, cfg: Config) -> tuple[float, dict[str, Any]]:
    """0-100 trend-strength gate on the 4H timeframe: price vs EMA200,
    EMA20/50/200 alignment, HH/HL structure, momentum, volume coherence."""
    candles = await exchange.get_klines(symbol, "4h")
    if len(candles) < cfg.top10_ema_slow:
        return 0.0, {}
    closes = [c[2] for c in candles]
    volumes = [c[5] for c in candles]
    ema_fast = _ema(closes, cfg.top10_ema_fast)
    ema_med = _ema(closes, cfg.top10_ema_medium)
    ema_slow = _ema(closes, cfg.top10_ema_slow)
    price = closes[-1]
    structure = detect_market_structure(candles, window=5, strict=False)
    vol_avg = sum(volumes[-20:]) / min(20, len(volumes))
    vol_now = volumes[-1]

    score = 0.0
    if price > ema_slow[-1]:
        score += 25
    if ema_fast[-1] > ema_med[-1]:
        score += 20
    if ema_med[-1] > ema_slow[-1]:
        score += 20
    if structure == "up":
        score += 20
    elif structure == "range":
        score += 5
    momentum = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) > 6 and closes[-6] else 0.0
    if momentum > 0:
        score += 10
    if vol_now >= vol_avg:
        score += 5
    return score, {"momentum_pct": momentum, "structure": structure}


# ============================================================================
# SIMULATED FILLS (paper) — buy at the real ask, sell at the real bid.
# Used by 33/60 and XRP Accumulation. A paper trade used to fill exactly at the
# last traded price, which is optimistic: a real market order pays the spread
# and, when it is bigger than the best level, walks deeper into the book.
# ============================================================================
BOOK_DEPTH = 10  # order-book levels read per fill (a single request)
_book_checked_at: dict[str, float] = {}


def book_recheck_due(key: str, seconds: float = 10.0) -> bool:
    """True at most once every `seconds` for the same key. The monitors tick
    every 3s and must not hammer the order-book endpoint while a sale is
    waiting for the real bid to clear the margin."""
    now = time.time()
    if now - _book_checked_at.get(key, 0.0) < seconds:
        return False
    _book_checked_at[key] = now
    return True


async def get_order_book(symbol: str, depth: int = BOOK_DEPTH) -> Optional[tuple[list[list[float]], list[list[float]]]]:
    """Top `depth` levels of the Bybit order book as (bids, asks), each a list
    of [price, size] with the best level first. None when it cannot be read or
    looks broken (empty or crossed) — callers then use a safe fallback."""
    try:
        data = await exchange._get(
            "/v5/market/orderbook",
            params={"category": exchange.category, "symbol": symbol, "limit": depth},
        )
        if not data or data.get("retCode") != 0:
            return None
        res = data.get("result") or {}
        bids = [[float(row[0]), float(row[1])] for row in res.get("b", [])]
        asks = [[float(row[0]), float(row[1])] for row in res.get("a", [])]
        if not bids or not asks or bids[0][0] <= 0 or asks[0][0] < bids[0][0]:
            return None
        return bids, asks
    except Exception:  # noqa: BLE001
        return None


def walk_book(
    levels: list[list[float]], *, quote_budget: Optional[float] = None, base_qty: Optional[float] = None
) -> Optional[float]:
    """Average price of a market order that eats `levels` (best first).
    quote_budget: spend this much quote currency (a buy). base_qty: sell this
    much base currency. None when the visible depth is not enough."""
    if quote_budget is not None:
        spent = got = 0.0
        for price, size in levels:
            take = min(quote_budget - spent, price * size)
            got += take / price
            spent += take
            if spent >= quote_budget - 1e-9:
                return spent / got
        return None
    if base_qty:
        remaining, proceeds = base_qty, 0.0
        for price, size in levels:
            take = min(remaining, size)
            proceeds += take * price
            remaining -= take
            if remaining <= 1e-12:
                return proceeds / base_qty
    return None


async def simulate_fill(
    symbol: str, side: str, ref_price: float, cfg: Config,
    *, quote_budget: Optional[float] = None, base_qty: Optional[float] = None,
) -> tuple[float, dict[str, Any]]:
    """Price a market order would really get. side "buy" spends `quote_budget`,
    side "sell" sells `base_qty`. Returns (price, info); info.fill_model is
    "last" (simulation off, no request), "book" (real order book) or
    "fallback" (book unreadable / too thin: last price made worse by
    `sim_fallback_slippage_pct`); info.cost_pct is how much WORSE than the
    last price the fill is (positive = it costs money)."""
    if not cfg.sim_realistic_fills:
        return ref_price, {"fill_model": "last", "cost_pct": 0.0}
    book = await get_order_book(symbol)
    if book:
        bids, asks = book
        if side == "buy":
            avg = walk_book(asks, quote_budget=quote_budget)
        else:
            avg = walk_book(bids, base_qty=base_qty)
        if avg:
            spread_pct = (asks[0][0] / bids[0][0] - 1.0) * 100.0
            cost = (avg / ref_price - 1.0) * 100.0 if side == "buy" else (1.0 - avg / ref_price) * 100.0
            return avg, {"fill_model": "book", "spread_pct": round(spread_pct, 4), "cost_pct": round(cost, 4)}
    haircut = cfg.sim_fallback_slippage_pct / 100.0
    price = ref_price * (1.0 + haircut) if side == "buy" else ref_price * (1.0 - haircut)
    return price, {"fill_model": "fallback", "cost_pct": round(cfg.sim_fallback_slippage_pct, 4)}


# ============================================================================
# Strategy "33/60" — buy when RSI crosses down through 35 (a fresh dip into
# oversold-adjacent territory), exit once RSI recovers up to 60. Backtested
# on ~41 days of BTC 1h data: 14 dips found, 10/14 (71%) reached RSI 60
# within 40 candles, average gain +1.58% (median +1.57%, max +2.51%) on the
# ones that did — none went negative among the ones that reached target.
# The other 29% never got there within the window; those exit on the
# timeout below instead of being held indefinitely. This is a genuinely
# different entry rule from a reversal-candle strategy — here entry fires
# the moment RSI first dips below the threshold, no confirmation candle
# required.
# ============================================================================

S3360_WALLET_ID = "s3360_wallet_singleton"
S3360_FEE_PCT = 0.001


async def get_s3360_wallet() -> dict[str, Any]:
    doc = await db.s3360_wallet.find_one({"_id": S3360_WALLET_ID}, {"_id": 0})
    if not doc:
        doc = {"cash": 0.0, "total_transferred_in": 0.0, "reset_seq": 0}
        await db.s3360_wallet.update_one(
            {"_id": S3360_WALLET_ID}, {"$set": doc}, upsert=True
        )
    doc.setdefault("reset_seq", 0)
    return doc


_s3360_last_candle: dict[tuple[str, str], float] = {}  # per (symbol, timeframe): timestamp of the CURRENT still-forming candle a live-touch attempt was already made on — see run_s3360_scan


_daily_ref_cache: dict[str, tuple[float, float]] = {}  # symbol -> (UTC day start, price the day opened at)


async def get_daily_open_price(symbol: str) -> Optional[float]:
    """Price the CURRENT UTC day opened at, taken as yesterday's close (this
    market never closes, so the two are practically the same). get_klines drops
    the still-forming candle, so the last daily candle it returns should be
    yesterday's; if it is anything else (exchange lag around midnight, a gap)
    return None instead of a wrong number — callers then let the trade through,
    exactly as before the filter existed."""
    day_start = (time.time() // 86400) * 86400
    cached = _daily_ref_cache.get(symbol)
    if cached and cached[0] == day_start:
        return cached[1]
    try:
        daily = await exchange.get_klines(symbol, "1d")
    except Exception:  # noqa: BLE001
        return None
    if not daily or abs(daily[-1][0] - (day_start - 86400)) > 1:
        return None
    ref = daily[-1][2]
    if not ref or ref <= 0:
        return None
    _daily_ref_cache[symbol] = (day_start, ref)
    return ref


async def run_s3360_scan() -> None:
    cfg = await get_config()
    if not cfg.s3360_enabled:
        return

    open_count = await db.s3360_positions.count_documents({"status": "open"})
    if open_count >= cfg.s3360_max_open_positions:
        return

    wallet = await get_s3360_wallet()
    cash = wallet.get("cash", 0.0)
    if cash <= 1.0:
        return

    tickers = await exchange.get_tickers()
    vol_map: dict[str, float] = {}
    for t in tickers:
        try:
            vol_map[t["symbol"]] = float(t.get("volValue") or 0)
        except (TypeError, ValueError):
            continue
    symbols = await exchange.get_symbols()
    quotes = {q.strip() for q in (cfg.quote_filter or "").split(",") if q.strip()}
    candidates: list[str] = []
    for s in symbols:
        if not s.get("enableTrading"):
            continue
        sym = s.get("symbol")
        if not sym:
            continue
        if quotes and s.get("quoteCurrency") not in quotes:
            continue
        if any(sym.upper().startswith(base) for base in EXCLUDED_STABLE_BASES):
            continue
        if cfg.excluded_pairs and sym in cfg.excluded_pairs:
            continue
        if cfg.enabled_pairs and sym not in cfg.enabled_pairs:
            continue
        if vol_map.get(sym, 0) < cfg.min_24h_volume_usdt:
            continue
        candidates.append(sym)
    candidates.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
    candidates = candidates[:30]
    candidates = [c for c in candidates if await is_volume_stable(c, cfg)]

    for symbol in candidates:
        if open_count >= cfg.s3360_max_open_positions:
            break
        if await db.s3360_positions.find_one({"symbol": symbol, "status": "open"}):
            continue
        for tf in cfg.s3360_timeframes:
            candles = await exchange.get_klines(symbol, tf)
            if len(candles) < cfg.s3360_rsi_period + 3:
                await log_reject(symbol, tf, "s3360", "dati insufficienti")
                continue
            # Live entry: react to the RSI value AS IT IS RIGHT NOW, using the
            # real-time price as a stand-in for the still-forming candle's
            # close — same number the exchange's own live chart shows you
            # ticking in real time. Waiting for that candle to actually
            # close (the old behaviour) missed fast touches that recovered
            # before the candle finished.
            live_price = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
            if not live_price:
                await log_reject(symbol, tf, "s3360", "prezzo live non disponibile")
                continue
            closes = [c[2] for c in candles]
            live_rsis = rsi_wilder(closes + [live_price], cfg.s3360_rsi_period)
            live_rsi = live_rsis[-1]
            if live_rsi > cfg.s3360_low_threshold:
                await log_reject(symbol, tf, "s3360", "nessun nuovo ingresso sotto soglia bassa")
                continue
            # Dedup against the CURRENT still-forming candle's start time —
            # not the last closed one — so a live touch only attempts once
            # per forming candle, no matter how many scans happen while
            # price stays under the threshold within it.
            tf_sec = TF_SECONDS.get(tf, 3600)
            forming_candle_t = candles[-1][0] + tf_sec
            if _s3360_last_candle.get((symbol, tf)) == forming_candle_t:
                await log_reject(symbol, tf, "s3360", "stessa candela già tentata")
                continue
            if cfg.s3360_min_atr_pct > 0:
                atr_now = atr_wilder(
                    [c[3] for c in candles], [c[4] for c in candles], closes, cfg.s3360_rsi_period
                )
                # If the ATR cannot be computed, let the trade through (as before the filter).
                if atr_now is not None and atr_now / live_price * 100.0 < cfg.s3360_min_atr_pct:
                    # Not marked as "attempted": volatility may pick up within this candle.
                    await log_reject(symbol, tf, "s3360", "mercato troppo piatto (filtro volatilità)")
                    continue
            rise_pct: Optional[float] = None
            day_open = await get_daily_open_price(symbol)
            if day_open:
                rise_pct = round((live_price / day_open - 1.0) * 100.0, 4)
            if (
                cfg.s3360_max_daily_rise_pct > 0
                and rise_pct is not None
                and rise_pct > cfg.s3360_max_daily_rise_pct
            ):
                # Not marked as "attempted": the coin may cool off within this
                # same candle and qualify on a later scan.
                await log_reject(symbol, tf, "s3360", "già salita troppo oggi (filtro crescita giornaliera)")
                continue
            _s3360_last_candle[(symbol, tf)] = forming_candle_t
            signal = {
                "entry": live_price,
                "rsi": live_rsi,
                "candle_t": forming_candle_t,
                "daily_rise_pct": rise_pct,
            }
            await open_s3360_position(symbol, tf, signal, cfg)
            open_count += 1
            break


async def open_s3360_position(symbol: str, tf: str, signal: dict[str, Any], cfg: Config) -> None:
    wallet = await get_s3360_wallet()
    cash = wallet.get("cash", 0.0)
    if cash <= 1.0:
        return
    entry = signal["entry"]
    live_price = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
    ref_price = live_price or entry
    # Size each trade as a fixed share of TOTAL equity (cash + value of
    # currently open positions), not just the free cash — so "N slots"
    # always means "1/N of total capital per trade" regardless of how many
    # positions happen to be open right now. E.g. max_open_positions=2 means
    # each trade gets 50% of equity, whether it's the 1st or 2nd slot filled.
    open_positions = await db.s3360_positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    open_value = sum(
        (price_feed.get(p["symbol"]) or p["entry"]) * p["quantity"] for p in open_positions
    )
    equity = cash + open_value
    slots = max(1, cfg.s3360_max_open_positions)
    notional = min((equity / slots) * await get_regime_size_multiplier(cfg), cash)
    if notional < 1.0:
        return
    # Paper fill at the real ask (walking the book if the order is bigger than
    # the first level) instead of the last traded price.
    fill_price, fill_info = await simulate_fill(symbol, "buy", ref_price, cfg, quote_budget=notional)
    quantity = notional / fill_price

    candles = await exchange.get_klines(symbol, tf)
    highs = [c[3] for c in candles]
    lows = [c[4] for c in candles]
    closes = [c[2] for c in candles]
    atr = atr_wilder(highs, lows, closes, cfg.s3360_rsi_period) or 0.0

    doc = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "timeframe": tf,
        "side": "long",
        "entry": entry,
        "fill_price": fill_price,
        "fill_model": fill_info["fill_model"],
        "entry_cost_pct": fill_info["cost_pct"],
        "spread_pct": fill_info.get("spread_pct"),
        "rsi_at_entry": signal.get("rsi"),
        "daily_rise_pct": (
            None if signal.get("daily_rise_pct") is None else round(signal["daily_rise_pct"], 2)
        ),
        "atr": atr,
        "quantity": quantity,
        "notional": notional,
        "status": "open",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.s3360_positions.insert_one(dict(doc))
    await db.s3360_wallet.update_one(
        {"_id": S3360_WALLET_ID}, {"$inc": {"cash": -notional}}, upsert=True
    )


_s3360_hold_log_at: dict[str, float] = {}


async def _s3360_hold_log(p: dict[str, Any]) -> None:
    """The monitor runs every 3 seconds; write the 'waiting' note at most once
    every 5 minutes per position so it stays visible without flooding the log."""
    now = time.time()
    if now - _s3360_hold_log_at.get(p["id"], 0.0) < 300:
        return
    _s3360_hold_log_at[p["id"]] = now
    await log_reject(
        p["symbol"], p.get("timeframe", "?"), "s3360",
        "RSI al target ma prezzo sotto l'entrata: attendo il recupero",
    )


async def monitor_s3360_positions() -> None:
    cfg = await get_config()
    open_positions = await db.s3360_positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    for p in open_positions:
        cur = price_feed.get(p["symbol"])
        if not cur:
            cur = await price_feed.price_or_rest(p["symbol"])
        if not cur or cur <= 0:
            continue

        hit = None
        tf = p.get("timeframe", "1h")
        candles = await exchange.get_klines(p["symbol"], tf)
        # Live exit: same principle as entry — use the real-time price as
        # the still-forming candle's close, so a touch of the target RSI
        # closes the trade immediately instead of waiting for the candle
        # to finish (which could let the touch reverse away unrealized).
        closes = [c[2] for c in candles] + [cur]
        rsis = rsi_wilder(closes, cfg.s3360_rsi_period) if len(closes) > cfg.s3360_rsi_period else []
        if rsis and rsis[-1] >= cfg.s3360_high_threshold:
            hit = "target_rsi_raggiunto"
        if not hit:
            continue

        fill = p.get("fill_price", p["entry"])
        margin_price = fill * (1 + cfg.s3360_min_exit_gain_pct / 100.0)
        if cfg.s3360_hold_below_entry and cur < margin_price:
            # RSI reached the target but closing now would lock in a loss (or
            # less than the fees): keep waiting. Both conditions must be true
            # together — RSI at the target AND the price above the entry.
            await _s3360_hold_log(p)
            continue

        # The last price clears the margin; now ask what a market sell would
        # REALLY get (best bid / depth). With the simulation off this is `cur`
        # and costs no request. The book is read at most every 10s per position.
        if cfg.sim_realistic_fills and not book_recheck_due(p["id"]):
            continue
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        if cfg.s3360_hold_below_entry and sell_price < margin_price:
            await _s3360_hold_log(p)  # the price we would really get is still too low
            continue

        gross_pnl = (sell_price - fill) * p["quantity"]
        exit_notional = sell_price * p["quantity"]
        fees = (p["notional"] + exit_notional) * S3360_FEE_PCT
        pnl = gross_pnl - fees
        await db.s3360_wallet.update_one(
            {"_id": S3360_WALLET_ID},
            {"$inc": {"cash": p["notional"] + pnl}},
            upsert=True,
        )
        await db.s3360_positions.update_one(
            {"id": p["id"]},
            {"$set": {
                "status": "closed", "close_price": sell_price, "close_reason": hit,
                "close_last_price": cur,
                "exit_fill_model": sell_info["fill_model"],
                "exit_cost_pct": sell_info["cost_pct"],
                "exit_spread_pct": sell_info.get("spread_pct"),
                "pnl_usdt": round(pnl, 4),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )


class S3360TransferRequest(BaseModel):
    amount: float


@api.post("/s3360/deposit")
async def s3360_deposit(req: S3360TransferRequest) -> dict[str, Any]:
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
    updated = await db.s3360_wallet.find_one_and_update(
        {"_id": S3360_WALLET_ID},
        {"$inc": {"cash": amount, "total_transferred_in": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return {"ok": True, "s3360_cash": updated.get("cash", amount), "main_cash": main_cash - amount}


@api.post("/s3360/withdraw")
async def s3360_withdraw(req: S3360TransferRequest) -> dict[str, Any]:
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")
    w = await get_s3360_wallet()
    available = w.get("cash", 0.0)
    if amount > available and (amount - available) <= 0.01:
        amount = available
    updated = await db.s3360_wallet.find_one_and_update(
        {"_id": S3360_WALLET_ID, "cash": {"$gte": amount}},
        {"$inc": {"cash": -amount}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=400, detail="Fondi insufficienti nel portafoglio 33/60")
    main_cash = await get_paper_cash()
    await set_paper_cash(main_cash + amount)
    return {"ok": True, "s3360_cash": updated.get("cash", 0.0), "main_cash": main_cash + amount}


@api.get("/s3360/portfolio")
async def s3360_portfolio() -> dict[str, Any]:
    cfg = await get_config()
    wallet = await get_s3360_wallet()
    open_docs = await db.s3360_positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    closed_docs = await db.s3360_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(500)

    unrealized = 0.0
    open_out = []
    open_value = 0.0
    for p in open_docs:
        cur = price_feed.get(p["symbol"]) or await price_feed.price_or_rest(p["symbol"]) or p["entry"]
        # What the bot REALLY paid (the real ask) — the same basis its exit rule
        # uses. `entry` is only the last price seen when the signal fired.
        basis = p.get("fill_price", p["entry"])
        upnl = (cur - basis) * p["quantity"]
        unrealized += upnl
        open_value += cur * p["quantity"]
        item = {**p, "current_price": cur, "unrealized_pnl": round(upnl, 4)}
        if cfg.s3360_hold_below_entry:
            level = basis * (1 + cfg.s3360_min_exit_gain_pct / 100.0)
            item["exit_level"] = level  # the real SELL price needed to close (with RSI at the target)
            item["pct_to_exit_level"] = round((level / cur - 1.0) * 100.0, 2) if cur else None
        open_out.append(item)

    realized = sum(c.get("pnl_usdt", 0.0) for c in closed_docs)
    equity = wallet.get("cash", 0.0) + open_value
    wins = sum(1 for c in closed_docs if c.get("pnl_usdt", 0.0) > 0)
    losses = sum(1 for c in closed_docs if c.get("pnl_usdt", 0.0) <= 0)
    return {
        "cash": round(wallet.get("cash", 0.0), 4),
        "equity": round(equity, 4),
        "total_transferred_in": wallet.get("total_transferred_in", 0.0),
        "unrealized_pnl": round(unrealized, 4),
        "realized_pnl": round(realized, 4),
        "open_positions": open_out,
        "closed_positions": closed_docs[:100],
        "open_count": len(open_docs),
        "closed_count": len(closed_docs),
        "win_rate": round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0,
        # The app shows these instead of numbers written by hand in its text.
        "rsi_low_threshold": cfg.s3360_low_threshold,
        "rsi_target": cfg.s3360_high_threshold,
        "hold_below_entry": cfg.s3360_hold_below_entry,
        "min_exit_gain_pct": cfg.s3360_min_exit_gain_pct,
    }


_RISE_BUCKETS = ["sotto 0%", "0-1%", "1-2%", "2-3%", "3-6%", "oltre 6%"]


def _rise_bucket(rise: float) -> str:
    if rise < 0:
        return "sotto 0%"
    if rise < 1:
        return "0-1%"
    if rise < 2:
        return "1-2%"
    if rise < 3:
        return "2-3%"
    if rise < 6:
        return "3-6%"
    return "oltre 6%"


def summarize_by_daily_rise(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Groups closed trades by how much the coin had already risen that UTC day
    when the trade was opened. Trades opened before this data was recorded are
    counted separately instead of being guessed."""
    buckets: dict[str, dict[str, float]] = {}
    without = 0
    for p in closed:
        rise = p.get("daily_rise_pct")
        if rise is None:
            without += 1
            continue
        b = buckets.setdefault(_rise_bucket(rise), {"n": 0, "wins": 0, "pnl": 0.0, "pct": 0.0})
        pnl = p.get("pnl_usdt") or 0.0
        notional = p.get("notional") or 0.0
        b["n"] += 1
        b["wins"] += 1 if pnl > 0 else 0
        b["pnl"] += pnl
        b["pct"] += (pnl / notional * 100.0) if notional else 0.0
    rows = []
    for name in _RISE_BUCKETS:
        b = buckets.get(name)
        if not b:
            continue
        rows.append({
            "fascia": name,
            "operazioni": int(b["n"]),
            "vinte_pct": round(100.0 * b["wins"] / b["n"], 1),
            "guadagno_medio_pct": round(b["pct"] / b["n"], 2),
            "guadagno_totale_usd": round(b["pnl"], 2),
        })
    return {"con_dato": sum(r["operazioni"] for r in rows), "senza_dato": without, "fasce": rows}


@api.get("/s3360/analysis")
async def s3360_analysis() -> dict[str, Any]:
    closed = await db.s3360_positions.find({"status": "closed"}, {"_id": 0}).to_list(5000)
    return summarize_by_daily_rise(closed)


@api.post("/s3360/reset")
async def s3360_reset() -> dict[str, Any]:
    await db.s3360_positions.delete_many({})
    await db.s3360_wallet.update_one(
        {"_id": S3360_WALLET_ID},
        {"$set": {"cash": 0.0, "total_transferred_in": 0.0}, "$inc": {"reset_seq": 1}},
        upsert=True,
    )
    return {"ok": True}


# ============================================================================
# XRP Accumulation — the goal here is NOT dollar profit, it's owning more XRP
# over time. All trading capital buys XRPUSDC the instant RSI touches <=25,
# sells everything the instant RSI touches >=60 (same live-touch mechanism
# as 33/60 — no waiting for a candle to close). On every sale, the ORIGINAL
# trading capital goes back to cash for the next cycle, and ONLY the profit
# gets converted to XRP immediately and held forever — it never re-enters
# the trading capital, so the tradeable USDC balance never inflates and the
# permanent XRP stash only ever grows. No stop-loss, no timeout: this is a
# pure accumulation philosophy — if RSI never comes back up, the position
# just sits in XRP and waits, exactly as intended for something you'd want
# to hold anyway.
# ============================================================================

XRP_ACC_WALLET_ID = "xrp_acc_wallet_singleton"
XRP_ACC_FEE_PCT = 0.001
XRP_ACC_SYMBOL = "XRPUSDC"


async def get_xrp_acc_wallet() -> dict[str, Any]:
    doc = await db.xrp_acc_wallet.find_one({"_id": XRP_ACC_WALLET_ID}, {"_id": 0})
    if not doc:
        doc = {"cash": 0.0, "permanent_xrp": 0.0, "total_transferred_in": 0.0, "reset_seq": 0}
        await db.xrp_acc_wallet.update_one(
            {"_id": XRP_ACC_WALLET_ID}, {"$set": doc}, upsert=True
        )
    doc.setdefault("permanent_xrp", 0.0)
    doc.setdefault("reset_seq", 0)
    return doc


_xrp_acc_last_candle: dict[str, float] = {}  # per timeframe: timestamp of the CURRENT still-forming candle a live-touch attempt was already made on


_xrp_acc_log_at: dict[str, float] = {}
_xrp_acc_candle_cache: dict[str, tuple[float, list[list[float]]]] = {}


async def _xrp_acc_log(tf: str, reason: str) -> None:
    """The entry check now runs every few seconds; a diagnostic row each time
    would flood the log (and the database), so each (timeframe, reason) is
    written at most once a minute — enough to show the strategy is alive."""
    key = f"{tf}|{reason}"
    now = time.time()
    if now - _xrp_acc_log_at.get(key, 0.0) < 60:
        return
    _xrp_acc_log_at[key] = now
    await log_reject(XRP_ACC_SYMBOL, tf, "xrp_acc", reason)


async def _xrp_acc_candles(tf: str) -> list[list[float]]:
    """Closed candles only change when a candle closes, so re-downloading
    ~200 of them every 3 seconds would be wasted traffic. Reuse them for 30s,
    and fall back to the last good copy (up to 5 minutes) if a download fails."""
    now = time.time()
    cached = _xrp_acc_candle_cache.get(tf)
    if cached and cached[1] and now - cached[0] < 30:
        return cached[1]
    candles = await exchange.get_klines(XRP_ACC_SYMBOL, tf)
    if candles:
        _xrp_acc_candle_cache[tf] = (now, candles)
        return candles
    if cached and cached[1] and now - cached[0] < 300:
        return cached[1]
    return []


async def run_xrp_acc_scan() -> None:
    cfg = await get_config()
    if not cfg.xrp_acc_enabled:
        return
    if await db.xrp_acc_positions.find_one({"status": "open"}):
        return
    wallet = await get_xrp_acc_wallet()
    cash = wallet.get("cash", 0.0)
    if cash <= 1.0:
        return

    for tf in cfg.xrp_acc_timeframes:
        candles = await _xrp_acc_candles(tf)
        if len(candles) < cfg.xrp_acc_rsi_period + 3:
            await _xrp_acc_log(tf, "dati insufficienti")
            continue
        live_price = price_feed.get(XRP_ACC_SYMBOL) or await price_feed.price_or_rest(XRP_ACC_SYMBOL)
        if not live_price:
            await _xrp_acc_log(tf, "prezzo live non disponibile")
            continue
        closes = [c[2] for c in candles]
        live_rsis = rsi_wilder(closes + [live_price], cfg.xrp_acc_rsi_period)
        live_rsi = live_rsis[-1]
        if live_rsi > cfg.xrp_acc_low_threshold:
            await _xrp_acc_log(tf, "nessun nuovo ingresso sotto soglia bassa")
            continue
        tf_sec = TF_SECONDS.get(tf, 3600)
        forming_candle_t = candles[-1][0] + tf_sec
        if _xrp_acc_last_candle.get(tf) == forming_candle_t:
            await _xrp_acc_log(tf, "stessa candela già tentata")
            continue
        _xrp_acc_last_candle[tf] = forming_candle_t

        # Paper fill at the real ask (walking the book if needed) instead of
        # the last traded price.
        fill_price, fill_info = await simulate_fill(XRP_ACC_SYMBOL, "buy", live_price, cfg, quote_budget=cash)
        quantity = cash / fill_price
        doc = {
            "id": str(uuid.uuid4()),
            "symbol": XRP_ACC_SYMBOL,
            "timeframe": tf,
            "side": "long",
            "entry": live_price,
            "fill_price": fill_price,
            "fill_model": fill_info["fill_model"],
            "entry_cost_pct": fill_info["cost_pct"],
            "spread_pct": fill_info.get("spread_pct"),
            "rsi_at_entry": live_rsi,
            "quantity": quantity,
            "notional": cash,
            "status": "open",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.xrp_acc_positions.insert_one(dict(doc))
        await db.xrp_acc_wallet.update_one(
            {"_id": XRP_ACC_WALLET_ID}, {"$inc": {"cash": -cash}}, upsert=True
        )
        break


_xrp_acc_hold_log_at: dict[str, float] = {}


async def _xrp_acc_hold_log(p: dict[str, Any]) -> None:
    """The monitor runs every 3 seconds; write the 'waiting' note at most once
    every 5 minutes per position so it stays visible without flooding the log."""
    now = time.time()
    if now - _xrp_acc_hold_log_at.get(p["id"], 0.0) < 300:
        return
    _xrp_acc_hold_log_at[p["id"]] = now
    await log_reject(
        p["symbol"], p.get("timeframe", "?"), "xrp_acc",
        "RSI al target ma prezzo sotto l'entrata: attendo il recupero",
    )


async def monitor_xrp_acc_positions() -> None:
    cfg = await get_config()
    open_positions = await db.xrp_acc_positions.find({"status": "open"}, {"_id": 0}).to_list(5)
    for p in open_positions:
        cur = price_feed.get(p["symbol"])
        if not cur:
            cur = await price_feed.price_or_rest(p["symbol"])
        if not cur or cur <= 0:
            continue
        tf = p.get("timeframe", "1h")
        candles = await exchange.get_klines(p["symbol"], tf)
        closes = [c[2] for c in candles] + [cur]
        rsis = rsi_wilder(closes, cfg.xrp_acc_rsi_period) if len(closes) > cfg.xrp_acc_rsi_period else []
        if not (rsis and rsis[-1] >= cfg.xrp_acc_high_threshold):
            continue

        fill = p.get("fill_price", p["entry"])
        margin_price = fill * (1 + cfg.xrp_acc_min_exit_gain_pct / 100.0)
        if cfg.xrp_acc_hold_below_entry and cur < margin_price:
            # RSI reached the target but selling now would lock in a loss (or
            # less than the fees): keep waiting. Both conditions must be true
            # together — RSI at the target AND the price above the entry. All
            # the trading capital sits in this one position, so while it waits
            # no new entry can open.
            await _xrp_acc_hold_log(p)
            continue

        # The last price clears the margin; now ask what a market sell would
        # REALLY get (best bid / depth). With the simulation off this is `cur`
        # and costs no request. The book is read at most every 10s.
        if cfg.sim_realistic_fills and not book_recheck_due(p["id"]):
            continue
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        if cfg.xrp_acc_hold_below_entry and sell_price < margin_price:
            await _xrp_acc_hold_log(p)  # the price we would really get is still too low
            continue

        proceeds = sell_price * p["quantity"]
        fees = (p["notional"] + proceeds) * XRP_ACC_FEE_PCT
        net_proceeds = proceeds - fees
        profit_usdt = net_proceeds - p["notional"]
        # Original trading capital goes back to cash for the next cycle;
        # only the profit (if any) converts to XRP and joins the permanent
        # stash. With the hold-below-entry rule on, a sale is never a net
        # loss; with it off, cash gets back less than it started with — the
        # permanent stash never goes backwards, but the trading capital can
        # shrink on a losing round (there is no stop).
        cash_back = min(net_proceeds, p["notional"])
        profit_xrp = max(0.0, profit_usdt) / cur
        await db.xrp_acc_wallet.update_one(
            {"_id": XRP_ACC_WALLET_ID},
            {"$inc": {"cash": cash_back, "permanent_xrp": profit_xrp}},
            upsert=True,
        )
        await db.xrp_acc_positions.update_one(
            {"id": p["id"]},
            {"$set": {
                "status": "closed", "close_price": sell_price, "close_reason": "target_rsi_raggiunto",
                "close_last_price": cur,
                "exit_fill_model": sell_info["fill_model"],
                "exit_cost_pct": sell_info["cost_pct"],
                "exit_spread_pct": sell_info.get("spread_pct"),
                "profit_usdt": round(profit_usdt, 4),
                "profit_xrp": round(profit_xrp, 6),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )


class XrpAccTransferRequest(BaseModel):
    amount: float


@api.post("/xrp-accumulation/deposit")
async def xrp_acc_deposit(req: XrpAccTransferRequest) -> dict[str, Any]:
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
    updated = await db.xrp_acc_wallet.find_one_and_update(
        {"_id": XRP_ACC_WALLET_ID},
        {"$inc": {"cash": amount, "total_transferred_in": amount}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return {"ok": True, "xrp_acc_cash": updated.get("cash", amount), "main_cash": main_cash - amount}


@api.post("/xrp-accumulation/withdraw")
async def xrp_acc_withdraw(req: XrpAccTransferRequest) -> dict[str, Any]:
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="L'importo deve essere positivo")
    w = await get_xrp_acc_wallet()
    available = w.get("cash", 0.0)
    if amount > available and (amount - available) <= 0.01:
        amount = available
    updated = await db.xrp_acc_wallet.find_one_and_update(
        {"_id": XRP_ACC_WALLET_ID, "cash": {"$gte": amount}},
        {"$inc": {"cash": -amount}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(
            status_code=400,
            detail="Fondi insufficienti nel capitale di trading — la riserva permanente di XRP non può essere prelevata da qui",
        )
    main_cash = await get_paper_cash()
    await set_paper_cash(main_cash + amount)
    return {"ok": True, "xrp_acc_cash": updated.get("cash", 0.0), "main_cash": main_cash + amount}


@api.get("/xrp-accumulation/portfolio")
async def xrp_acc_portfolio() -> dict[str, Any]:
    cfg = await get_config()
    wallet = await get_xrp_acc_wallet()
    open_docs = await db.xrp_acc_positions.find({"status": "open"}, {"_id": 0}).to_list(5)
    closed_docs = await db.xrp_acc_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(500)

    live_price = price_feed.get(XRP_ACC_SYMBOL) or await price_feed.price_or_rest(XRP_ACC_SYMBOL) or 0.0
    open_out = []
    open_quantity = 0.0
    for p in open_docs:
        cur = price_feed.get(p["symbol"]) or p["entry"]
        open_quantity += p["quantity"]
        basis = p.get("fill_price", p["entry"])  # what the bot REALLY paid (real ask)
        item = {**p, "current_price": cur, "unrealized_pnl": round((cur - basis) * p["quantity"], 4)}
        if cfg.xrp_acc_hold_below_entry:
            level = basis * (1 + cfg.xrp_acc_min_exit_gain_pct / 100.0)
            item["exit_level"] = level  # the real SELL price needed to close (with RSI at the target)
            item["pct_to_exit_level"] = round((level / cur - 1.0) * 100.0, 2) if cur else None
        open_out.append(item)

    permanent_xrp = wallet.get("permanent_xrp", 0.0)
    total_xrp = permanent_xrp + open_quantity
    cash = wallet.get("cash", 0.0)
    equity_usdt = cash + open_quantity * live_price + permanent_xrp * live_price
    total_profit_xrp = sum(c.get("profit_xrp", 0.0) for c in closed_docs)
    return {
        "cash": round(cash, 4),
        "permanent_xrp": round(permanent_xrp, 6),
        "total_xrp": round(total_xrp, 6),
        "xrp_price": live_price,
        "equity_usdt": round(equity_usdt, 4),
        "total_transferred_in": wallet.get("total_transferred_in", 0.0),
        "total_profit_xrp": round(total_profit_xrp, 6),
        "open_positions": open_out,
        "closed_positions": closed_docs[:100],
        "open_count": len(open_docs),
        "closed_count": len(closed_docs),
        "rsi_low_threshold": cfg.xrp_acc_low_threshold,
        "rsi_target": cfg.xrp_acc_high_threshold,
        "hold_below_entry": cfg.xrp_acc_hold_below_entry,
        "min_exit_gain_pct": cfg.xrp_acc_min_exit_gain_pct,
    }


@api.post("/xrp-accumulation/reset")
async def xrp_acc_reset() -> dict[str, Any]:
    await db.xrp_acc_positions.delete_many({})
    await db.xrp_acc_wallet.update_one(
        {"_id": XRP_ACC_WALLET_ID},
        {"$set": {"cash": 0.0, "permanent_xrp": 0.0, "total_transferred_in": 0.0}, "$inc": {"reset_seq": 1}},
        upsert=True,
    )
    return {"ok": True}


app.include_router(api)
