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
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
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
    default_fee_rate: float = 0.001  # LEGACY: no longer read, replaced by sim_fee_pct (kept so stored configs still load)
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
    real_trading_enabled: bool = False  # master switch for REAL orders (off by default)
    real_max_order_usdc: float = 30.0
    real_max_total_usdc: float = 30.0
    real_daily_loss_usdc: float = 3.0
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
    trend_filter_timeframe: str = "1h"  # trend filter: timeframe on which the coin's own trend is read (EMA fast above EMA slow = trend is long)
    trend_filter_ema_fast: int = 20  # trend filter: fast EMA period
    trend_filter_ema_slow: int = 50  # trend filter: slow EMA period
    s3360_require_coin_trend: bool = False  # entry filter: open only if the coin's own trend is long (EMA fast > EMA slow on trend_filter_timeframe)
    trend_filter_long_timeframe: str = "1d"  # trend filter: timeframe on which the long EMA is read (daily, to judge the big trend)
    trend_filter_ema_long: int = 200  # trend filter: long EMA period; with the 'above long EMA' switches on, the price must be above it
    s3360_require_above_long_ema: bool = False  # entry filter: live price must be above the long EMA (default 200) on trend_filter_timeframe
    xrp_acc_require_above_long_ema: bool = False  # same, for XRP Accumulation
    s3360_require_btc_trend: bool = False  # entry filter: open only if BTC's regime is bullish (same regime used for position sizing)
    xrp_acc_require_coin_trend: bool = False  # same as s3360_require_coin_trend, for XRP Accumulation
    xrp_acc_require_btc_trend: bool = False  # same as s3360_require_btc_trend, for XRP Accumulation
    s3360_min_atr_pct: float = 0.0  # entry filter: skip when the timeframe's ATR (as % of price) is below this — in a dead-flat market the rebounds are tiny (0 = filter off)
    s3360_hold_below_entry: bool = True  # exit rule: when RSI reaches the target but the price is still below the entry (plus the margin below), keep the position open instead of closing at a loss
    s3360_min_net_profit_pct: float = 0.3  # exit rule: the sale must leave at least this NET profit (%) after the fees on both legs; the bot works out the price needed (only used with hold_below_entry)
    s3360_trailing_enabled: bool = True  # once the minimum net profit is reached, follow the highest price and sell if it falls back by s3360_trailing_pct — never below break-even (fees covered); only used with hold_below_entry
    s3360_trailing_pct: float = 0.3  # how far (%) below the highest price the trailing sells
    s3360_rsi_giveback_enabled: bool = False  # extra exit: once the RSI has been above s3360_rsi_arm, selling as soon as it falls back below s3360_rsi_giveback — at ANY price, ignoring the minimum net profit / break-even rules
    s3360_rsi_arm: float = 52.0
    s3360_rsi_giveback: float = 50.0
    s3360_rsi_arm_closed_only: bool = True  # l'uscita 'RSI di ritorno' si arma solo con l'RSI di una candela CHIUSA (non con un picco di pochi secondi)
    s3360_entry_mode: str = "tocco"  # tocco | risale | incrocio | entrambi (ingresso a conferma)
    s3360_confirm_level: float = 30.0  # livello che l'RSI deve riconquistare (modalita risale / entrambi)
    s3360_confirm_lookback: int = 5  # l'RSI deve essere stato sotto la soglia bassa in queste ultime candele
    s3360_confirm_ma_period: int = 14  # periodo della media dell'RSI (modalita incrocio / entrambi)
    s3360_confirm_closed_only: bool = True  # True: decide solo a candela chiusa (il segnale non sparisce)
    s3360_post_target_trailing_enabled: bool = False  # a RSI target non vende subito: segue il massimo
    s3360_post_target_trailing_pct: float = 1.0  # vende se il prezzo scende di questa % dal massimo
    s3360_post_target_trailing_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    s3360_rsi_giveback_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])  # timeframe su cui vale l'uscita RSI di ritorno
    xrp_acc_entry_mode: str = "tocco"  # tocco | risale | incrocio | entrambi (ingresso a conferma)
    xrp_acc_confirm_level: float = 30.0  # livello che l'RSI deve riconquistare (modalita risale / entrambi)
    xrp_acc_confirm_lookback: int = 5  # l'RSI deve essere stato sotto la soglia bassa in queste ultime candele
    xrp_acc_confirm_ma_period: int = 14  # periodo della media dell'RSI (modalita incrocio / entrambi)
    xrp_acc_confirm_closed_only: bool = True  # True: decide solo a candela chiusa (il segnale non sparisce)
    xrp_acc_enabled: bool = True
    xrp_acc_timeframes: list[str] = Field(default_factory=lambda: ["1h"])
    xrp_acc_rsi_period: int = 14
    xrp_acc_low_threshold: float = 25.0  # entry: buy ALL trading capital when RSI drops to/below this
    xrp_acc_high_threshold: float = 60.0  # exit: sell everything when RSI rises to/above this
    xrp_acc_hold_below_entry: bool = True  # exit rule: when RSI reaches the target but the price is still below the entry (plus the margin below), keep the position open instead of selling at a loss
    xrp_acc_min_net_profit_pct: float = 0.3  # exit rule: the sale must leave at least this NET profit (%) after the fees on both legs; the bot works out the price needed (only used with hold_below_entry)
    xrp_acc_trailing_enabled: bool = True  # once the minimum net profit is reached, follow the highest price and sell if it falls back by xrp_acc_trailing_pct — never below break-even (fees covered); only used with hold_below_entry
    xrp_acc_trailing_pct: float = 0.3  # how far (%) below the highest price the trailing sells
    # --- ICT (meccanico, solo long): sweep -> MSS -> ritorno nel gap. Paper di default.
    ict_enabled: bool = True
    ict_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC"])
    ict_timeframe: str = "15m"
    ict_rr: float = 2.0
    ict_kill_zones: bool = True
    ict_alloc_pct: float = 25.0
    ict_max_open: int = 2
    ict_max_hold_h: float = 48.0
    ict_capital: float = 200.0
    sim_realistic_fills: bool = True  # paper fills of 33/60 and XRP Accumulation use the REAL order book (buy at the ask side, sell at the bid side, walking the depth) instead of the last traded price
    sim_fallback_slippage_pct: float = 0.05  # when the order book cannot be read (or is too thin for the order), the fill is the last price made worse by this % per side
    sim_fee_pct: float = 0.25  # exchange fee per side (%), charged on both legs of every paper trade — the Bybit EU rate the account actually pays (taker)

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
            db.ict_positions,
            db.manual_setups,
            db.ma_positions,
        ):
            rows = await coll.find({"status": "open"}, {"symbol": 1, "_id": 0}).to_list(1000)
            symbols |= {d["symbol"] for d in rows}
        # XRP Accumulation checks its entry every few seconds, so XRPUSDC must
        # be followed live permanently, not only while a position is open.
        if (await get_config()).xrp_acc_enabled:
            symbols.add(XRP_ACC_SYMBOL)
        _c = await get_config()
        if _c.ict_enabled:
            symbols |= set(_c.ict_symbols)
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
    # Fees on both legs, like the other strategies: this pipeline used to book
    # the profit GROSS of fees.
    fee = (entry * qty + exit_price * qty) * ((await get_config()).sim_fee_pct / 100.0)
    if pos["side"] == "long":
        pnl = (exit_price - entry) * qty - fee
    else:
        pnl = (entry - exit_price) * qty - fee
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
        # Unlock notional and add PnL: cash += exit * qty - fees
        await adjust_effective_cash(strategy, exit_price * qty - fee)
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
_funding_cache: dict[str, float] = {}


async def get_trade_fees(symbol: str, cfg: Config) -> tuple[float, float]:
    """Maker/taker fee rates (as fractions). Paper trading charges the single
    configured rate `sim_fee_pct` on every strategy; reading the real
    per-symbol rates from the signed Bybit API is part of the execution phase."""
    rate = cfg.sim_fee_pct / 100.0
    return rate, rate


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
    fee = (entry * close_qty + price * close_qty) * ((await get_config()).sim_fee_pct / 100.0)
    pnl = ((price - entry) * close_qty if pos["side"] == "long" else (entry - price) * close_qty) - fee
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
        await adjust_effective_cash(strategy, price * close_qty - fee)
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
        fees_now = (notional + exit_notional_now) * (cfg.sim_fee_pct / 100.0)
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
    s3360_entry_task = asyncio.create_task(s3360_entry_loop())
    ict_monitor_task = asyncio.create_task(ict_monitor_loop())
    desk_task = asyncio.create_task(desk_loop())
    crus_task = asyncio.create_task(crus_loop())
    mng_task = asyncio.create_task(mng_loop())
    term_task = asyncio.create_task(term_loop())
    ma_task = asyncio.create_task(ma_loop())
    manual_task = asyncio.create_task(manual_loop())
    ws_task = asyncio.create_task(price_feed.run())
    premature_task = asyncio.create_task(premature_stop_loop())
    entry_timing_task = asyncio.create_task(entry_timing_loop())
    log_prune_task = asyncio.create_task(log_prune_loop())
    yield
    scan_task.cancel()
    monitor_task.cancel()
    ict_monitor_task.cancel()
    desk_task.cancel()
    crus_task.cancel()
    mng_task.cancel()
    term_task.cancel()
    ma_task.cancel()
    manual_task.cancel()
    s3360_entry_task.cancel()
    ws_task.cancel()
    premature_task.cancel()
    entry_timing_task.cancel()
    log_prune_task.cancel()
    await exchange.close()
    client.close()


# Optional access code for the whole API. Active ONLY when the BOT_ACCESS_KEY
# variable is set on Railway, so deploying this code changes nothing until a
# code is chosen. With a code set, the auto-generated API pages (/docs, /redoc,
# /openapi.json) are hidden too: they would list every endpoint to a stranger.
BOT_ACCESS_KEY = os.environ.get("BOT_ACCESS_KEY", "").strip()
_HIDE_DOCS = {"docs_url": None, "redoc_url": None, "openapi_url": None} if BOT_ACCESS_KEY else {}
app = FastAPI(lifespan=lifespan, **_HIDE_DOCS)
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


def _downsample_track(samples: list[list[float]], n: int) -> list[list[float]]:
    """At most `n` points, always keeping the lowest and highest ones so the
    chart never hides how deep or how high the price went."""
    if len(samples) <= n:
        return samples
    step = len(samples) / n
    picked = {int(i * step) for i in range(n)} | {len(samples) - 1}
    picked.add(min(range(len(samples)), key=lambda i: samples[i][1]))
    picked.add(max(range(len(samples)), key=lambda i: samples[i][1]))
    return [samples[i] for i in sorted(picked)]


@api.get("/positions/{kind}/{position_id}/track")
async def position_track(kind: str, position_id: str, max_points: int = 400) -> dict[str, Any]:
    """Everything the position-detail screen needs: the operation, the price
    path recorded while it was open, the lowest/highest points, how long it
    spent under its entry price, and the levels the bot is waiting for."""
    cfg = await get_config()
    if kind == "s3360":
        coll, pre = db.s3360_positions, "s3360"
    elif kind == "xrp_acc":
        coll, pre = db.xrp_acc_positions, "xrp_acc"
    else:
        raise HTTPException(status_code=404, detail="Tipo di operazione sconosciuto")
    pos = await coll.find_one({"id": position_id}, {"_id": 0})
    if not pos:
        raise HTTPException(status_code=404, detail="Operazione non trovata")
    track = await db.position_tracks.find_one({"_id": position_id}) or {}
    samples = sorted(([float(a), float(b)] for a, b in track.get("samples", [])), key=lambda x: x[0])
    fill = pos.get("fill_price", pos["entry"])
    is_open = pos.get("status") == "open"
    now_ts = time.time()
    cur = None
    if is_open:
        cur = price_feed.get(pos["symbol"]) or await price_feed.price_or_rest(pos["symbol"]) or None
    else:
        cur = pos.get("close_price")

    prices = [x[1] for x in samples] + ([cur] if cur else [])
    lo_candidates = [v for v in (pos.get("min_price"), *prices) if v]
    hi_candidates = [v for v in (pos.get("max_price"), *prices) if v]
    lo = min(lo_candidates) if lo_candidates else None
    hi = max(hi_candidates) if hi_candidates else None
    below = 0.0
    for (t0, p0), (t1, _p1) in zip(samples, samples[1:]):
        if p0 < fill:
            below += t1 - t0
    if is_open and samples and samples[-1][1] < fill:
        below += max(0.0, now_ts - samples[-1][0])
    try:
        opened_ts = datetime.fromisoformat(pos["opened_at"]).timestamp()
    except Exception:  # noqa: BLE001
        opened_ts = samples[0][0] if samples else now_ts
    try:
        end_ts = now_ts if is_open else datetime.fromisoformat(pos["closed_at"]).timestamp()
    except Exception:  # noqa: BLE001
        end_ts = now_ts

    min_net = getattr(cfg, f"{pre}_min_net_profit_pct")
    levels: dict[str, Any] = {
        "entry": fill,
        "break_even": required_exit_price(fill, 0.0, cfg.sim_fee_pct),
        "exit_level": required_exit_price(fill, min_net, cfg.sim_fee_pct),
        "trailing_stop": None,
        "rsi_target": getattr(cfg, f"{pre}_high_threshold"),
    }
    if pos.get("trailing_active") and getattr(cfg, f"{pre}_trailing_enabled"):
        peak = float(pos.get("peak_price") or cur or fill)
        levels["trailing_stop"] = trailing_stop_price(fill, peak, getattr(cfg, f"{pre}_trailing_pct"), cfg.sim_fee_pct)
        levels["peak_price"] = peak
    return {
        "kind": kind,
        "id": position_id,
        "symbol": pos["symbol"],
        "timeframe": pos.get("timeframe"),
        "status": pos.get("status"),
        "opened_at": pos.get("opened_at"),
        "closed_at": pos.get("closed_at"),
        "entry": pos["entry"],
        "fill_price": fill,
        "quantity": pos.get("quantity"),
        "notional": pos.get("notional"),
        "rsi_at_entry": pos.get("rsi_at_entry"),
        "current_price": cur,
        "pct_vs_entry": round((cur / fill - 1.0) * 100.0, 3) if cur and fill else None,
        "close_price": pos.get("close_price"),
        "close_reason": pos.get("close_reason"),
        "pnl_usdt": pos.get("pnl_usdt", pos.get("profit_usdt")),
        "profit_xrp": pos.get("profit_xrp"),
        "fee_pct": pos.get("fee_pct", cfg.sim_fee_pct),
        "levels": levels,
        "stats": {
            "min_price": lo,
            "min_at": pos.get("min_at"),
            "max_price": hi,
            "max_at": pos.get("max_at"),
            "max_drawdown_pct": round((lo / fill - 1.0) * 100.0, 3) if lo and fill else None,
            "max_gain_pct": round((hi / fill - 1.0) * 100.0, 3) if hi and fill else None,
            "time_below_entry_s": round(below),
            "age_s": round(max(0.0, end_ts - opened_ts)),
            "samples": len(samples),
        },
        "samples": _downsample_track(samples, max(50, min(max_points, 1000))),
    }


# --- ACCESS GUARD START ---
# The app sends the code in the X-Access-Key header; for opening links in a
# browser it can also be passed as ?key=CODE. 10 wrong codes from one address
# in 15 minutes lock that address out for the rest of the window.
_AUTH_WINDOW_SEC = 15 * 60
_AUTH_MAX_FAILS = 10
_auth_fails: dict[str, list[float]] = {}
_AUTH_OPEN_PATHS = ("/api", "/api/")  # bare health answer ({"status": "ok"}): left open for Railway's checks


def _client_ip(request: Request) -> str:
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # The LAST entry is the one added by the nearest proxy; the first ones
        # are written by the client and can be faked.
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def install_access_guard(application: FastAPI, access_key: str) -> None:
    @application.middleware("http")
    async def access_guard(request: Request, call_next):
        if (
            not access_key
            or request.method == "OPTIONS"
            or not request.url.path.startswith("/api")
            or request.url.path in _AUTH_OPEN_PATHS
        ):
            return await call_next(request)
        ip = _client_ip(request)
        now = time.time()
        recent = [t for t in _auth_fails.get(ip, []) if now - t < _AUTH_WINDOW_SEC]
        if recent:
            _auth_fails[ip] = recent
        else:
            _auth_fails.pop(ip, None)
        if len(recent) >= _AUTH_MAX_FAILS:
            return JSONResponse(
                {"detail": "Troppi tentativi sbagliati. Riprova tra qualche minuto."},
                status_code=429,
            )
        provided = request.headers.get("x-access-key") or request.query_params.get("key") or ""
        if not provided:
            # A missing code is not a guess, so it does not count as a failure.
            return JSONResponse({"detail": "Codice di accesso richiesto"}, status_code=401)
        if not hmac.compare_digest(provided.encode(), access_key.encode()):
            if len(_auth_fails) > 5000:
                _auth_fails.clear()
            _auth_fails.setdefault(ip, []).append(now)
            logger.warning("Access refused: wrong code from %s (%d in the last 15 min)", ip, len(recent) + 1)
            return JSONResponse({"detail": "Codice di accesso errato"}, status_code=401)
        return await call_next(request)
# --- ACCESS GUARD END ---


# Registered BEFORE the CORS middleware on purpose: the last one added is the
# outermost, so CORS wraps this guard and its 401/429 answers still carry the
# CORS headers a browser needs.
install_access_guard(app, BOT_ACCESS_KEY)


@api.get("/auth/check")
async def auth_check() -> dict[str, Any]:
    """Reaching this point means the access code (if one is required) was
    accepted — the login screen uses it to validate what the user typed."""
    return {"ok": True, "protected": bool(BOT_ACCESS_KEY)}


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


_coin_trend_cache: dict[tuple[str, str, int, int], tuple[bool, float]] = {}


async def coin_trend_is_long(symbol: str, cfg: Config) -> Optional[bool]:
    """True if the coin's own trend is long: fast EMA above slow EMA on the
    trend-filter timeframe. None when it cannot be worked out (not enough
    candles / error) — callers then block the entry, because the filter is
    meant to be strict. Cached 2 minutes per coin."""
    tf = cfg.trend_filter_timeframe
    fast, slow = cfg.trend_filter_ema_fast, cfg.trend_filter_ema_slow
    key = (symbol, tf, fast, slow)
    now = time.time()
    hit = _coin_trend_cache.get(key)
    if hit and now - hit[1] < 120:
        return hit[0]
    try:
        candles = await exchange.get_klines(symbol, tf)
    except Exception:  # noqa: BLE001
        return None
    if len(candles) < slow + 5 or fast >= slow:
        return None
    closes = [c[2] for c in candles]
    ok = _ema(closes, fast)[-1] > _ema(closes, slow)[-1]
    _coin_trend_cache[key] = (ok, now)
    return ok


_long_ema_cache: dict[tuple[str, str, int], tuple[Optional[float], float]] = {}


async def long_ema_value(symbol: str, cfg: Config) -> Optional[float]:
    """Current value of the long EMA (default 200 periods) on its own
    timeframe (default daily). Fetches twice the period in candles so the EMA is properly
    warmed up. None if there is not enough history. Cached 2 minutes."""
    tf, period = cfg.trend_filter_long_timeframe, cfg.trend_filter_ema_long
    key = (symbol, tf, period)
    now = time.time()
    hit = _long_ema_cache.get(key)
    if hit and now - hit[1] < 120:
        return hit[0]
    try:
        candles = await exchange.get_klines(symbol, tf, limit=max(CANDLE_LIMIT, period * 2))
    except Exception:  # noqa: BLE001
        return None
    if period < 2 or len(candles) < period + 5:
        return None
    val = _ema([c[2] for c in candles], period)[-1]
    _long_ema_cache[key] = (val, now)
    return val


async def trend_filter_block_reason(symbol: str, cfg: Config, need_coin: bool, need_btc: bool, need_above_long: bool = False, price: Optional[float] = None) -> Optional[str]:
    """None = entry allowed; otherwise the reason (Italian, for the log)."""
    if need_btc and await get_shared_market_regime(cfg) != "bullish":
        return "trend BTC non rialzista (filtro trend)"
    if need_above_long:
        ema_l = await long_ema_value(symbol, cfg)
        if ema_l is None or not price:
            return f"media {cfg.trend_filter_ema_long} non calcolabile (filtro trend)"
        if price <= ema_l:
            return f"prezzo sotto la media {cfg.trend_filter_ema_long} su {cfg.trend_filter_long_timeframe} (filtro trend)"
    if need_coin:
        up = await coin_trend_is_long(symbol, cfg)
        if up is None:
            return "trend della moneta non calcolabile (filtro trend)"
        if not up:
            return f"trend della moneta non long su {cfg.trend_filter_timeframe} (filtro trend)"
    return None


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


def required_exit_price(fill: float, min_net_pct: float, fee_pct: float) -> float:
    """Lowest sell price that still leaves `min_net_pct` NET profit after the
    fee on BOTH legs (fee charged on the entry value and on the exit value, as
    the closing code does). With N the entry value, q the quantity and f the fee:
        sell*q - (N + sell*q)*f >= N*(1+m)   =>   sell >= fill*(1+m+f)/(1-f)
    Exact for any fee level; the spread is not in here because the callers
    compare it with the REAL bid, which already includes it."""
    m = min_net_pct / 100.0
    f = fee_pct / 100.0
    return fill * (1.0 + m + f) / (1.0 - f)


def trailing_stop_price(fill: float, peak: float, trail_pct: float, fee_pct: float) -> float:
    """Price at which the trailing sells: `trail_pct` below the highest price
    seen, but NEVER below break-even — the price where the fees on both legs are
    exactly covered — so a trailing exit can never lock in a net loss."""
    return max(peak * (1.0 - trail_pct / 100.0), required_exit_price(fill, 0.0, fee_pct))


async def update_trailing_state(coll: Any, p: dict[str, Any], cur: float, activation_price: float) -> tuple[bool, float]:
    """Arms the trailing the first time the last price clears `activation_price`
    (the minimum-net-profit level) and from then on keeps the highest price
    seen. Saved on the position, so it survives restarts. Returns (active, peak)."""
    active = bool(p.get("trailing_active"))
    peak = float(p.get("peak_price") or 0.0)
    if not active:
        if cur >= activation_price:
            active, peak = True, max(peak, cur)
            await coll.update_one({"id": p["id"]}, {"$set": {"trailing_active": True, "peak_price": peak}})
    elif cur > peak * 1.0001:  # a new high; ignore sub-0.01% noise to keep database writes low
        peak = cur
        await coll.update_one({"id": p["id"]}, {"$set": {"peak_price": peak}})
    return active, peak


# ---- POSITION TRACKING: a "black box" for every open 33/60 and XRP operation ----
TRACK_MAX_SAMPLES = 6000          # hard cap per operation (a few days at one sample a minute)
TRACK_KEEP_DAYS = 60              # closed operations keep their path this long
_track_last_at: dict[str, float] = {}
_track_ext_at: dict[str, float] = {}
_track_cleanup_at: float = 0.0


def _track_interval(age_s: float) -> float:
    """Seconds between price samples: one a minute on the first day, then
    sparser as the operation ages, so a long one stays small."""
    if age_s <= 86400:
        return 60.0
    if age_s <= 7 * 86400:
        return 300.0
    return 900.0


async def record_track(kind: str, coll: Any, p: dict[str, Any], cur: float) -> None:
    """Called by the monitors every 3s for each open operation. Writes at most
    one price sample per interval into `position_tracks`, and keeps the lowest
    and highest price seen on the operation itself. Never raises: a tracking
    problem must not get in the way of a trade."""
    global _track_cleanup_at
    try:
        now = time.time()
        pid = p["id"]
        iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
        lo = float(p.get("min_price") or 0.0)
        hi = float(p.get("max_price") or 0.0)
        upd: dict[str, Any] = {}
        if lo == 0.0 or cur < lo * 0.9999:
            upd["min_price"], upd["min_at"] = cur, iso
        if hi == 0.0 or cur > hi * 1.0001:
            upd["max_price"], upd["max_at"] = cur, iso
        if upd and now - _track_ext_at.get(pid, 0.0) >= 10.0:
            _track_ext_at[pid] = now
            await coll.update_one({"id": pid}, {"$set": upd})
        try:
            age = now - datetime.fromisoformat(p["opened_at"]).timestamp()
        except Exception:  # noqa: BLE001
            age = 0.0
        if now - _track_last_at.get(pid, 0.0) >= _track_interval(age):
            _track_last_at[pid] = now
            await db.position_tracks.update_one(
                {"_id": pid},
                {
                    "$setOnInsert": {"kind": kind, "symbol": p.get("symbol"), "opened_at": p.get("opened_at")},
                    "$push": {"samples": {"$each": [[round(now, 1), cur]], "$slice": -TRACK_MAX_SAMPLES}},
                },
                upsert=True,
            )
        if now - _track_cleanup_at > 3600:
            _track_cleanup_at = now
            cutoff = datetime.fromtimestamp(now - TRACK_KEEP_DAYS * 86400, timezone.utc).isoformat()
            await db.position_tracks.delete_many({"closed_at": {"$lt": cutoff}})
    except Exception as e:  # noqa: BLE001
        logger.warning("Position tracking skipped: %s", e)


async def close_track(pid: str, last_price: float) -> None:
    """Final price point and closing time: the path stays available for review."""
    try:
        now = time.time()
        await db.position_tracks.update_one(
            {"_id": pid},
            {
                "$set": {"closed_at": datetime.fromtimestamp(now, timezone.utc).isoformat()},
                "$push": {"samples": {"$each": [[round(now, 1), last_price]], "$slice": -TRACK_MAX_SAMPLES}},
            },
        )
        _track_last_at.pop(pid, None)
        _track_ext_at.pop(pid, None)
    except Exception as e:  # noqa: BLE001
        logger.warning("Closing the position track failed: %s", e)


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


try:
    import entry_confirm as EC
except ImportError:  # pragma: no cover
    from backend import entry_confirm as EC


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



# ---------------------------------------------------------------------------
# 33/60: controllo d'ingresso frequente (ogni ~20 s) con cache, e registro dei segnali.
# ---------------------------------------------------------------------------
_s3360_px_cache: dict[str, Any] = {"at": 0.0, "map": {}, "vol": {}}
_s3360_cand_cache: dict[str, Any] = {"at": 0.0, "list": []}
_s3360_candle_cache: dict[tuple[str, str], tuple[float, list[list[float]]]] = {}
_s3360_log_at: dict[str, float] = {}
_s3360_event_seen: set = set()
_s3360_event_prune_at = 0.0


async def _s3360_candidates(cfg: Config) -> tuple[list[str], dict[str, float]]:
    """Elenco monete da controllare (ricalcolato ogni 5 minuti) e prezzi di tutte le monete (una sola richiesta ogni 10 s)."""
    now = time.time()
    px = _s3360_px_cache
    if now - px["at"] > 10 or not px["map"]:
        tickers = await exchange.get_tickers()
        pm: dict[str, float] = {}
        vm: dict[str, float] = {}
        for t in tickers:
            try:
                vm[t["symbol"]] = float(t.get("volValue") or 0)
            except (TypeError, ValueError, KeyError):
                pass
            try:
                pm[t["symbol"]] = float(t.get("last") or 0)
            except (TypeError, ValueError, KeyError):
                pass
        if pm:
            px["at"], px["map"], px["vol"] = now, pm, vm
    cc = _s3360_cand_cache
    if now - cc["at"] > 300 or not cc["list"]:
        vol_map = px.get("vol", {})
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
        if candidates:
            cc["at"], cc["list"] = now, candidates
    return cc["list"], px["map"]


async def _s3360_candles(symbol: str, tf: str) -> list[list[float]]:
    """Candele chiuse: si riscaricano solo quando ne e' chiusa una nuova (non ad ogni controllo)."""
    now = time.time()
    tf_sec = TF_SECONDS.get(tf, 3600)
    hit = _s3360_candle_cache.get((symbol, tf))
    if hit and hit[1] and now < hit[1][-1][0] + 2 * tf_sec + 3 and now - hit[0] < tf_sec * 3:
        return hit[1]
    candles = await exchange.get_klines(symbol, tf)
    if candles:
        _s3360_candle_cache[(symbol, tf)] = (now, candles)
        return candles
    if hit and hit[1] and now - hit[0] < 600:
        return hit[1]
    return []


async def _s3360_log(symbol: str, tf: str, reason: str) -> None:
    """Come log_reject, ma al massimo una volta ogni 5 minuti (15 per il motivo piu' comune) per moneta/timeframe/motivo."""
    key = f"{symbol}|{tf}|{reason}"
    ttl = 900.0 if reason.startswith("nessun nuovo ingresso") else 300.0
    now = time.time()
    if now - _s3360_log_at.get(key, 0.0) < ttl:
        return
    _s3360_log_at[key] = now
    await log_reject(symbol, tf, "s3360", reason)


async def _s3360_event(symbol: str, tf: str, rsi: float, price: float, candle_t: float, outcome: str) -> None:
    """Registro dei segnali: una riga ogni volta che una moneta tocca la soglia, con l'esito. Tenuto 14 giorni."""
    global _s3360_event_prune_at
    key = (symbol, tf, candle_t, outcome)
    if key in _s3360_event_seen:
        return
    if len(_s3360_event_seen) > 5000:
        _s3360_event_seen.clear()
    _s3360_event_seen.add(key)
    try:
        now = datetime.now(timezone.utc)
        await db.signal_events.insert_one({
            "strategy": "s3360", "symbol": symbol, "tf": tf, "rsi": round(float(rsi), 2),
            "price": price, "outcome": outcome, "at": now.isoformat(),
        })
        if time.time() - _s3360_event_prune_at > 3600:
            _s3360_event_prune_at = time.time()
            await db.signal_events.delete_many({"at": {"$lt": (now - timedelta(days=14)).isoformat()}})
    except Exception:  # noqa: BLE001
        pass


async def s3360_entry_loop() -> None:
    """Unico chiamante del controllo d'ingresso 33/60 (due chiamanti potrebbero aprire due posizioni insieme)."""
    await asyncio.sleep(15)
    while True:
        try:
            await run_s3360_scan()
        except Exception as e:  # noqa: BLE001
            logger.exception("33/60 entry loop error: %s", e)
        await asyncio.sleep(20)


@api.get("/s3360/segnali")
async def s3360_signals() -> dict[str, Any]:
    rows = await db.signal_events.find({"strategy": "s3360"}, {"_id": 0}).sort("at", -1).to_list(150)
    return {"rows": rows}


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

    candidates, last_prices = await _s3360_candidates(cfg)

    for symbol in candidates:
        if open_count >= cfg.s3360_max_open_positions:
            break
        if await db.s3360_positions.find_one({"symbol": symbol, "status": "open"}):
            continue
        for tf in cfg.s3360_timeframes:
            candles = await _s3360_candles(symbol, tf)
            if len(candles) < cfg.s3360_rsi_period + 3:
                await _s3360_log(symbol, tf, "dati insufficienti")
                continue
            # Live entry: react to the RSI value AS IT IS RIGHT NOW, using the
            # real-time price as a stand-in for the still-forming candle's
            # close — same number the exchange's own live chart shows you
            # ticking in real time. Waiting for that candle to actually
            # close (the old behaviour) missed fast touches that recovered
            # before the candle finished.
            live_price = price_feed.get(symbol) or last_prices.get(symbol) or await price_feed.price_or_rest(symbol)
            if not live_price:
                await _s3360_log(symbol, tf, "prezzo live non disponibile")
                continue
            closes = [c[2] for c in candles]
            live_rsis = rsi_wilder(closes + [live_price], cfg.s3360_rsi_period)
            live_rsi = live_rsis[-1]
            if cfg.s3360_entry_mode in EC.CONFIRM_MODES:
                _ev = EC.evaluate(closes, live_price, rsi_wilder, mode=cfg.s3360_entry_mode, period=cfg.s3360_rsi_period,
                                  low=cfg.s3360_low_threshold, level=cfg.s3360_confirm_level, lookback=cfg.s3360_confirm_lookback,
                                  ma_period=cfg.s3360_confirm_ma_period, closed_only=cfg.s3360_confirm_closed_only)
                if not _ev["ok"]:
                    await _s3360_log(symbol, tf, "nessun nuovo ingresso (" + _ev["reason"] + ")")
                    continue
                if _ev["rsi"] is not None:
                    live_rsi = _ev["rsi"]
            elif live_rsi > cfg.s3360_low_threshold:
                await _s3360_log(symbol, tf, "nessun nuovo ingresso sotto soglia bassa")
                continue
            # Dedup against the CURRENT still-forming candle's start time —
            # not the last closed one — so a live touch only attempts once
            # per forming candle, no matter how many scans happen while
            # price stays under the threshold within it.
            tf_sec = TF_SECONDS.get(tf, 3600)
            forming_candle_t = candles[-1][0] + tf_sec
            if _s3360_last_candle.get((symbol, tf)) == forming_candle_t:
                await _s3360_log(symbol, tf, "stessa candela già tentata")
                continue
            if cfg.s3360_min_atr_pct > 0:
                atr_now = atr_wilder(
                    [c[3] for c in candles], [c[4] for c in candles], closes, cfg.s3360_rsi_period
                )
                # If the ATR cannot be computed, let the trade through (as before the filter).
                if atr_now is not None and atr_now / live_price * 100.0 < cfg.s3360_min_atr_pct:
                    # Not marked as "attempted": volatility may pick up within this candle.
                    await _s3360_log(symbol, tf, "mercato troppo piatto (filtro volatilità)")
                    await _s3360_event(symbol, tf, live_rsi, live_price, forming_candle_t, "scartato: mercato troppo piatto")
                    continue
            if cfg.s3360_require_coin_trend or cfg.s3360_require_btc_trend or cfg.s3360_require_above_long_ema:
                why = await trend_filter_block_reason(
                    symbol, cfg, cfg.s3360_require_coin_trend, cfg.s3360_require_btc_trend,
                    cfg.s3360_require_above_long_ema, live_price,
                )
                if why:
                    # Not marked as "attempted": the trend may turn within this candle.
                    await _s3360_log(symbol, tf, why)
                    await _s3360_event(symbol, tf, live_rsi, live_price, forming_candle_t, "scartato: " + str(why))
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
                await _s3360_log(symbol, tf, "già salita troppo oggi (filtro crescita giornaliera)")
                await _s3360_event(symbol, tf, live_rsi, live_price, forming_candle_t, "scartato: già salita troppo oggi")
                continue
            _s3360_last_candle[(symbol, tf)] = forming_candle_t
            signal = {
                "entry": live_price,
                "rsi": live_rsi,
                "candle_t": forming_candle_t,
                "daily_rise_pct": rise_pct,
            }
            await open_s3360_position(symbol, tf, signal, cfg)
            _op = await db.s3360_positions.find_one({"symbol": symbol, "status": "open"}, {"_id": 0, "opened_at": 1})
            _fresh = bool(_op) and (datetime.now(timezone.utc) - datetime.fromisoformat(_op["opened_at"])).total_seconds() < 30
            await _s3360_event(symbol, tf, live_rsi, live_price, forming_candle_t,
                               "aperta" if _fresh else "non aperta (capitale, limiti o reale bloccato: vedi registro)")
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
    _pc_mode = "real" if (await real_guard(cfg, "s3360"))[0] == "real" else "paper"
    _pc_ok, _pc_why, notional = await precheck_entry(symbol, tf, notional, cfg, _pc_mode, "s3360")
    if not _pc_ok or notional < 1.0:
        return
    # Paper fill at the real ask (walking the book if the order is bigger than
    # the first level) instead of the last traded price.
    mode, why = await real_guard(cfg, "s3360")
    if mode == "blocked":
        await log_reject(symbol, tf, "s3360", f"reale bloccato: {why}")
        return
    real_info = None
    if mode == "real":
        rb = await real_open_buy(symbol, notional, cfg, "s3360", cash_cap=cash)
        if not rb:
            await log_reject(symbol, tf, "s3360", "ordine reale non eseguito (vedi registro reale)")
            return
        real_info = rb
        notional = rb["value"]
        fill_price = rb["avg_price"]
        fill_info = {"fill_model": "reale", "cost_pct": 0.0, "spread_pct": None}
        quantity = rb["net_qty"]
    else:
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
    if real_info:
        doc["real"] = {k: real_info[k] for k in ("order_id", "link_id", "net_qty", "fee_base", "fee_quote", "qty")}
    await db.s3360_positions.insert_one(dict(doc))
    await db.s3360_wallet.update_one(
        {"_id": S3360_WALLET_ID}, {"$inc": {"cash": -notional}}, upsert=True
    )


async def _s3360_exit_diag(p: dict[str, Any], tf: str, hit: str, cur: float, diag: dict[str, Any]) -> None:
    """Dopo la chiusura: salva l'RSI al momento dell'uscita e, per l'uscita 'RSI di ritorno', scrive nel Registro segnali
    quando e con quale RSI si era armata. Non deve mai disturbare la chiusura."""
    try:
        await db.s3360_positions.update_one({"id": p["id"]}, {"$set": diag})
        if hit == "rsi_ritorno":
            how = p.get("rsi_armed_how") or "?"
            v = p.get("rsi_armed_value")
            await _s3360_event(p["symbol"], tf, diag.get("exit_rsi_live") or 0.0, cur, time.time(),
                               "USCITA RSI di ritorno: RSI ora %s, armata %s con RSI %s" % (diag.get("exit_rsi_live"), how, v))
    except Exception:  # noqa: BLE001
        pass


_s3360_hold_log_at: dict[str, float] = {}


async def _s3360_hold_log(p: dict[str, Any], reason: str = "RSI al target ma prezzo sotto l'entrata: attendo il recupero") -> None:
    """The monitor runs every 3 seconds; write the 'waiting' note at most once
    every 5 minutes per position so it stays visible without flooding the log."""
    now = time.time()
    if now - _s3360_hold_log_at.get(p["id"], 0.0) < 300:
        return
    _s3360_hold_log_at[p["id"]] = now
    await log_reject(
        p["symbol"], p.get("timeframe", "?"), "s3360",
        reason,
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
        await record_track("s3360", db.s3360_positions, p, cur)

        hit = None
        tf = p.get("timeframe", "1h")
        candles = await exchange.get_klines(p["symbol"], tf)
        # Live exit: same principle as entry — use the real-time price as
        # the still-forming candle's close, so a touch of the target RSI
        # closes the trade immediately instead of waiting for the candle
        # to finish (which could let the touch reverse away unrealized).
        closes = [c[2] for c in candles] + [cur]
        rsis = rsi_wilder(closes, cfg.s3360_rsi_period) if len(closes) > cfg.s3360_rsi_period else []
        rsi_hit = bool(rsis and rsis[-1] >= cfg.s3360_high_threshold)
        # Uscita extra "RSI di ritorno": dopo che l'RSI ha superato s3360_rsi_arm la posizione e' 'armata';
        # se poi l'RSI scende sotto s3360_rsi_giveback si esce (stesso pavimento delle altre uscite).
        giveback_hit = False
        _gb_diag: dict[str, Any] = {"exit_rsi_live": round(float(rsis[-1]), 2) if rsis and rsis[-1] is not None else None}
        if cfg.s3360_rsi_giveback_enabled and rsis and tf in cfg.s3360_rsi_giveback_timeframes:
            if not p.get("rsi_armed"):
                _arm_rsi, _arm_how = None, ""
                if cfg.s3360_rsi_arm_closed_only:
                    # Si arma solo con l'RSI di una candela CHIUSA dopo l'apertura: un picco di pochi secondi dentro la candela non basta.
                    try:
                        _opened_ts = datetime.fromisoformat(p["opened_at"]).timestamp()
                    except Exception:  # noqa: BLE001
                        _opened_ts = 0.0
                    if (len(rsis) >= 2 and rsis[-2] is not None and candles
                            and candles[-1][0] + TF_SECONDS.get(tf, 3600) > _opened_ts and rsis[-2] > cfg.s3360_rsi_arm):
                        _arm_rsi, _arm_how = float(rsis[-2]), "a candela chiusa"
                elif rsis[-1] is not None and rsis[-1] > cfg.s3360_rsi_arm:
                    _arm_rsi, _arm_how = float(rsis[-1]), "live"
                if _arm_rsi is not None:
                    _arm_doc = {"rsi_armed": True, "rsi_armed_at": datetime.now(timezone.utc).isoformat(),
                                "rsi_armed_value": round(_arm_rsi, 2), "rsi_armed_how": _arm_how, "rsi_armed_price": cur}
                    p.update(_arm_doc)
                    await db.s3360_positions.update_one({"id": p["id"]}, {"$set": _arm_doc})
                    await _s3360_event(p["symbol"], tf, _arm_rsi, cur, candles[-1][0] if candles else 0.0,
                                       "RSI di ritorno ARMATO (%s, soglia %g)" % (_arm_how, cfg.s3360_rsi_arm))
            giveback_hit = bool(p.get("rsi_armed")) and rsis[-1] is not None and rsis[-1] < cfg.s3360_rsi_giveback

        fill = p.get("fill_price", p["entry"])
        margin_price = required_exit_price(fill, cfg.s3360_min_net_profit_pct, cfg.sim_fee_pct)
        break_even = required_exit_price(fill, 0.0, cfg.sim_fee_pct)
        # Trailing: armed when the last price first clears the minimum-net level,
        # then it follows the highest price. It sells if the price falls back by
        # s3360_trailing_pct, but never below break-even.
        trail_hit = False
        if cfg.s3360_hold_below_entry and cfg.s3360_trailing_enabled:
            active, peak = await update_trailing_state(db.s3360_positions, p, cur, margin_price)
            trail_hit = active and cur <= trailing_stop_price(fill, peak, cfg.s3360_trailing_pct, cfg.sim_fee_pct)

        # Trailing DOPO il target: a RSI target (con prezzo che copre il guadagno minimo) non si vende subito;
        # si segue il massimo e si vende solo se il prezzo ricade di s3360_post_target_trailing_pct dal massimo.
        pt_on = bool(cfg.s3360_post_target_trailing_enabled and tf in cfg.s3360_post_target_trailing_timeframes)
        pt_armed = False
        pt_hit = False
        if pt_on:
            if not p.get("pt_armed") and rsi_hit and (not cfg.s3360_hold_below_entry or cur >= margin_price):
                p["pt_armed"] = True
                p["pt_peak"] = cur
                await db.s3360_positions.update_one({"id": p["id"]}, {"$set": {
                    "pt_armed": True, "pt_peak": cur, "pt_armed_at": datetime.now(timezone.utc).isoformat()}})
            if p.get("pt_armed"):
                pt_armed = True
                old_peak = float(p.get("pt_peak") or 0.0)
                peak_now = max(old_peak, cur)
                if peak_now > old_peak:
                    p["pt_peak"] = peak_now
                    await db.s3360_positions.update_one({"id": p["id"]}, {"$set": {"pt_peak": peak_now}})
                pt_hit = cur <= peak_now * (1.0 - cfg.s3360_post_target_trailing_pct / 100.0)

        # The RSI target stays the main exit; the trailing protects the profit
        # when the price turns back before the target is reached.
        if pt_armed and not (pt_hit or trail_hit or giveback_hit):
            continue  # target raggiunto: si lascia correre, il trailing dopo il target protegge
        if pt_hit:
            hit, floor_price = "trailing_dopo_target", break_even
        elif rsi_hit and not pt_on and (not cfg.s3360_hold_below_entry or cur >= margin_price):
            hit, floor_price = "target_rsi_raggiunto", margin_price
        elif trail_hit:
            hit, floor_price = "trailing_stop", break_even
        elif giveback_hit:
            # Voluto dall'utente: questa uscita chiude SEMPRE, a qualunque prezzo (anche in perdita),
            # senza guardare il guadagno minimo ne' il pareggio.
            hit, floor_price = "rsi_ritorno", 0.0
        elif rsi_hit:
            # RSI reached the target but selling now would lock in less than the
            # minimum net profit: keep waiting. Both conditions must be true
            # together — RSI at the target AND the price high enough.
            await _s3360_hold_log(p)
            continue
        else:
            continue
        if cfg.s3360_hold_below_entry and cur < floor_price:
            await _s3360_hold_log(p, "RSI tornato sotto la soglia ma il prezzo non copre i costi: attendo" if hit == "rsi_ritorno" else "Trailing: il prezzo non copre i costi, attendo" if hit in ("trailing_stop", "trailing_dopo_target") else "RSI al target ma prezzo sotto l'entrata: attendo il recupero")
            continue

        # The last price clears the floor; now ask what a market sell would
        # REALLY get (best bid / depth). With the simulation off this is `cur`
        # and costs no request. The book is read at most every 10s per position.
        if p.get("real"):
            if cfg.s3360_hold_below_entry and floor_price > 0 and cfg.sim_realistic_fills:
                # prima di mandare un ordine vero guardo il libro ordini: se il prezzo realistico e' sotto il minimo, attendo
                if not book_recheck_due(p["id"]):
                    continue
                est_price, _est = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
                if est_price < floor_price:
                    await _s3360_hold_log(p, "Vendita reale: il prezzo realistico dal libro ordini e' sotto il minimo: attendo")
                    continue
            if not real_sell_due(p["id"]):
                continue
            sell = await real_close_position(p, cfg, "s3360x")
            if not sell:
                await rlog(f"Vendita reale di {p['symbol']} non riuscita, riprovo tra 30 s", "errore")
                continue
            sell_price = sell["avg_price"]
            pnl = sell["net_value"] + sell.get("dust_value", 0.0) - p["notional"]
            await db.s3360_wallet.update_one({"_id": S3360_WALLET_ID}, {"$inc": {"cash": p["notional"] + pnl}}, upsert=True)
            await db.s3360_positions.update_one({"id": p["id"]}, {"$set": {
                "status": "closed", "close_price": sell_price, "close_reason": hit, "close_last_price": cur,
                "exit_fill_model": "reale", "fee_pct": cfg.sim_fee_pct, "pnl_usdt": round(pnl, 4),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "real_exit": {k: v for k, v in sell.items() if k != "dust"},
            }})
            await close_track(p["id"], cur)
            await _s3360_exit_diag(p, tf, hit, cur, _gb_diag)
            continue
        if cfg.sim_realistic_fills and not book_recheck_due(p["id"]):
            continue
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        if cfg.s3360_hold_below_entry and sell_price < floor_price:
            # the price we would really get is still too low
            await _s3360_hold_log(p, "RSI tornato sotto la soglia ma il prezzo di vendita vero non copre i costi: attendo" if hit == "rsi_ritorno" else "Trailing: il prezzo di vendita vero non copre i costi, attendo" if hit == "trailing_stop" else "RSI al target ma prezzo sotto l'entrata: attendo il recupero")
            continue

        gross_pnl = (sell_price - fill) * p["quantity"]
        exit_notional = sell_price * p["quantity"]
        fees = (p["notional"] + exit_notional) * (cfg.sim_fee_pct / 100.0)
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
                "fee_pct": cfg.sim_fee_pct,
                "pnl_usdt": round(pnl, 4),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        await close_track(p["id"], cur)
        await _s3360_exit_diag(p, tf, hit, cur, _gb_diag)


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
            level = required_exit_price(basis, cfg.s3360_min_net_profit_pct, cfg.sim_fee_pct)
            item["exit_level"] = level  # the real SELL price needed to close (with RSI at the target)
            item["pct_to_exit_level"] = round((level / cur - 1.0) * 100.0, 2) if cur else None
            item["break_even_price"] = required_exit_price(basis, 0.0, cfg.sim_fee_pct)
            if cfg.s3360_trailing_enabled and p.get("trailing_active"):
                peak = float(p.get("peak_price") or cur)
                item["trailing_active"] = True
                item["peak_price"] = peak
                item["trailing_stop"] = trailing_stop_price(basis, peak, cfg.s3360_trailing_pct, cfg.sim_fee_pct)
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
        "min_net_profit_pct": cfg.s3360_min_net_profit_pct,
        "fee_pct": cfg.sim_fee_pct,
        "trailing_enabled": cfg.s3360_trailing_enabled and cfg.s3360_hold_below_entry,
        "trailing_pct": cfg.s3360_trailing_pct,
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
# TRADING REALE su Bybit (spot) — collegato SOLO a 33/60, con i freni:
#   1. interruttore generale (spento di default) + la strategia deve essere nella lista "reali"
#   2. serve la password di accesso al bot (BOT_ACCESS_KEY): senza, il reale non si accende
#   3. tetto per ordine, tetto totale esposto e limite di perdita giornaliera
#   4. un solo ordine reale alla volta (lock) e controllo vero dell'esecuzione
#   5. se un ordine non e' chiaro, NON si apre nessuna posizione
# Le uscite (vendite) restano sempre permesse, anche con l'interruttore spento,
# cosi' una posizione reale non resta mai "bloccata".
# ============================================================================
import urllib.parse as _urlparse

try:
    import real_orders as RO
except ImportError:  # pragma: no cover
    from backend import real_orders as RO

REAL_HARD_MAX_ORDER = 100.0    # nessuna impostazione puo' superare questi tetti
REAL_HARD_MAX_TOTAL = 200.0
REAL_HARD_MAX_DAILY_LOSS = 50.0
REAL_ALLOWED_STRATEGIES = ("s3360", "ict", "xrp_acc")
REAL_SETTING_KEYS = ("real_trading_enabled", "real_max_order_usdc", "real_max_total_usdc",
                     "real_daily_loss_usdc", "live_strategies")

_real_lock = asyncio.Lock()
_real_sell_at: dict[str, float] = {}
_inst_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def rlog(text: str, level: str = "info") -> None:
    try:
        await db.real_log.insert_one({"at": datetime.now(timezone.utc).isoformat(), "level": level, "text": text})
        logger.log(logging.WARNING if level != "info" else logging.INFO, "REALE: %s", text)
    except Exception:  # noqa: BLE001
        pass


async def _bybit_signed(method: str, path: str, params: Optional[dict[str, Any]] = None,
                        body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Chiamata firmata a Bybit. Ritorna il JSON; solleva eccezione se la rete non risponde."""
    doc = await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 0})
    if not doc:
        raise RuntimeError("Nessuna chiave Bybit collegata")
    api_key = decrypt_str(doc["api_key"])
    api_secret = decrypt_str(doc["api_secret"])
    ts = str(int(time.time() * 1000))
    recv = "5000"
    headers = {"X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv}
    async with httpx.AsyncClient(base_url=BYBIT_BASE, timeout=12.0) as c:
        if method == "GET":
            query = _urlparse.urlencode(params or {})
            headers["X-BAPI-SIGN"] = RO.sign(api_secret, ts, api_key, recv, query)
            r = await c.get(path + (("?" + query) if query else ""), headers=headers)
        else:
            payload = RO.body_string(body or {})
            headers["X-BAPI-SIGN"] = RO.sign(api_secret, ts, api_key, recv, payload)
            headers["Content-Type"] = "application/json"
            r = await c.post(path, content=payload, headers=headers)
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        raise RuntimeError(f"Risposta Bybit non leggibile (HTTP {r.status_code})")


async def get_instrument(symbol: str) -> Optional[dict[str, Any]]:
    c = _inst_cache.get(symbol)
    if c and time.time() - c[0] < 3600:
        return c[1]
    try:
        resp = await _bybit_signed("GET", "/v5/market/instruments-info", {"category": "spot", "symbol": symbol})
    except Exception as e:  # noqa: BLE001
        await rlog(f"Regole della coppia {symbol} non lette: {e}", "errore")
        return None
    inst = RO.parse_instrument(resp, symbol)
    if inst:
        _inst_cache[symbol] = (time.time(), inst)
    else:
        await rlog(f"Regole della coppia {symbol} non trovate: {resp.get('retMsg')}", "errore")
    return inst


async def real_balance(coin: str) -> Optional[float]:
    try:
        resp = await _bybit_signed("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": coin})
    except Exception as e:  # noqa: BLE001
        await rlog(f"Saldo {coin} non letto: {e}", "errore")
        return None
    if resp.get("retCode") != 0:
        await rlog(f"Saldo {coin} non letto: {resp.get('retMsg')}", "errore")
        return None
    v = RO.coin_available(resp, coin)
    return None if v is None else float(v)


async def real_daily_pnl() -> float:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = await db.s3360_positions.find(
        {"status": "closed", "real": {"$exists": True}, "closed_at": {"$gte": start}}, {"pnl_usdt": 1, "_id": 0}
    ).to_list(1000)
    rows += await db.ict_positions.find(
        {"status": "closed", "real": {"$exists": True}, "closed_at": {"$gte": start}}, {"pnl_usdt": 1, "_id": 0}
    ).to_list(1000)
    rows += await db.manual_setups.find(
        {"status": "closed", "real": {"$exists": True}, "closed_at": {"$gte": start}}, {"pnl_usdt": 1, "_id": 0}
    ).to_list(1000)
    rows += await db.xrp_acc_positions.find(
        {"status": "closed", "real": {"$exists": True}, "closed_at": {"$gte": start}}, {"pnl_usdt": 1, "_id": 0}
    ).to_list(1000)
    extra = await db.real_roundtrips.find({"at": {"$gte": start}}, {"pnl": 1, "_id": 0}).to_list(1000)
    return float(sum(r.get("pnl_usdt", 0.0) for r in rows) + sum(r.get("pnl", 0.0) for r in extra))


async def real_open_exposure() -> float:
    rows = await db.s3360_positions.find({"status": "open", "real": {"$exists": True}}, {"notional": 1, "_id": 0}).to_list(500)
    rows += await db.ict_positions.find({"status": "open", "real": {"$exists": True}}, {"notional": 1, "_id": 0}).to_list(500)
    rows += await db.manual_setups.find({"status": "open", "real": {"$exists": True}}, {"notional": 1, "_id": 0}).to_list(500)
    rows += await db.xrp_acc_positions.find({"status": "open", "real": {"$exists": True}}, {"notional": 1, "_id": 0}).to_list(50)
    return float(sum(r.get("notional", 0.0) for r in rows))


async def real_guard(cfg: Config, strategy: str) -> tuple[str, Optional[str]]:
    """('paper'|'real'|'blocked', motivo). 'blocked' = la strategia e' reale ma non puo' operare ora."""
    if strategy not in cfg.live_strategies:
        return "paper", None
    if not cfg.real_trading_enabled:
        return "blocked", "reale spento (interruttore generale)"
    if not BOT_ACCESS_KEY:
        return "blocked", "manca la password di accesso al bot"
    if not await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 1}):
        return "blocked", "nessuna chiave Bybit collegata"
    if await real_daily_pnl() <= -abs(cfg.real_daily_loss_usdc):
        return "blocked", "limite di perdita giornaliera raggiunto"
    return "real", None


async def real_execute(symbol: str, side: str, amount: Any, tag: str) -> Optional[dict[str, Any]]:
    """Manda UN ordine di mercato vero e ne verifica l'esecuzione. Ritorna i numeri veri, o None
    se non e' stato eseguito con certezza (in quel caso nessuna posizione viene aperta/chiusa)."""
    async with _real_lock:
        inst = await get_instrument(symbol)
        if not inst or not inst["trading"]:
            await rlog(f"{symbol}: coppia non negoziabile o regole non disponibili", "errore")
            return None
        link = f"{tag}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        body = RO.buy_body(symbol, amount, link, inst) if side == "Buy" else RO.sell_body(symbol, amount, link, inst)
        q = RO.D(body["qty"])
        if (side == "Buy" and q < inst["min_amt"]) or (side == "Sell" and q < inst["min_qty"]) or q <= 0:
            await rlog(f"{symbol}: {side} {body['qty']} sotto il minimo ({inst['min_amt']} / {inst['min_qty']})", "errore")
            return None
        await rlog(f"INVIO {side} {symbol} {body['qty']} {'USD' if side == 'Buy' else inst['base']} [{link}]")
        order_id = None
        try:
            r = await _bybit_signed("POST", "/v5/order/create", body=body)
        except Exception as e:  # noqa: BLE001
            r = None
            await rlog(f"Rete incerta durante l'invio: {e}. Controllo se l'ordine e' partito.", "errore")
        if r is not None:
            if r.get("retCode") != 0:
                await rlog(f"Ordine RIFIUTATO da Bybit: {r.get('retCode')} {r.get('retMsg')}", "errore")
                return None
            order_id = (r.get("result") or {}).get("orderId")
        else:
            await asyncio.sleep(2.0)
            try:
                st0, o0 = RO.order_status(await _bybit_signed("GET", "/v5/order/history", {"category": "spot", "orderLinkId": link}))
            except Exception:  # noqa: BLE001
                st0, o0 = "Error", {}
            if st0 in ("NotFound", "Error"):
                await rlog("Ordine NON partito (nessuna traccia su Bybit).", "errore")
                return None
            order_id = o0.get("orderId")
        st, o = "New", {}
        for _ in range(15):
            await asyncio.sleep(0.8)
            try:
                st, o = RO.order_status(await _bybit_signed("GET", "/v5/order/history", {"category": "spot", "orderLinkId": link}))
            except Exception:  # noqa: BLE001
                continue
            if st in RO.FINAL_STATUSES:
                break
        if st not in RO.FINAL_STATUSES:
            try:
                await _bybit_signed("POST", "/v5/order/cancel", body={"category": "spot", "symbol": symbol, "orderLinkId": link})
            except Exception:  # noqa: BLE001
                pass
            await rlog(f"ATTENZIONE: ordine {link} senza esito definitivo (stato {st}); provato ad annullarlo. Controlla su Bybit.", "errore")
            return None
        ex = None
        try:
            ex = await _bybit_signed("GET", "/v5/execution/list", {"category": "spot", "orderId": order_id or o.get("orderId", "")})
        except Exception:  # noqa: BLE001
            ex = None
        f = RO.parse_fills(ex, o, inst["base"], inst["quote"])
        if f is None:
            await rlog(f"Ordine {link} chiuso senza esecuzione (stato {st}, motivo {o.get('rejectReason') or '-'}).", "errore")
            return None
        res = {
            "order_id": order_id, "link_id": link, "base": inst["base"], "quote": inst["quote"],
            "avg_price": float(f["avg_price"]), "qty": float(f["qty"]), "value": float(f["value"]),
            "fee_base": float(f["fee_base"]), "fee_quote": float(f["fee_quote"]),
            "net_qty": float(RO.net_base_after_buy(f)), "net_value": float(RO.net_quote_after_sell(f)),
            "status": st,
        }
        await rlog(f"ESEGUITO {side} {symbol}: {res['qty']} a {res['avg_price']:.6g} (valore {res['value']:.4f}, "
                   f"commissioni {res['fee_base']:.6g} {inst['base']} + {res['fee_quote']:.6g} {inst['quote']})")
        return res


async def real_open_buy(symbol: str, notional: float, cfg: Config, tag: str, cash_cap: Optional[float] = None) -> Optional[dict[str, Any]]:
    """Acquisto reale per aprire una posizione: rispetta tetto per ordine, tetto totale e saldo."""
    inst = await get_instrument(symbol)
    if not inst:
        return None
    room = cfg.real_max_total_usdc - await real_open_exposure()
    amount = min(notional, cfg.real_max_order_usdc, REAL_HARD_MAX_ORDER, room)
    bal = await real_balance(inst["quote"])
    if bal is None:
        return None
    amount = min(amount, bal * 0.995)
    min_need = float(inst["min_amt"]) * 1.1
    if amount < min_need:
        # con capitali piccoli l'importo calcolato puo' stare sotto il minimo di Bybit:
        # lo alzo al minimo (+10%) se budget, tetti e saldo lo permettono
        upper = min(cfg.real_max_order_usdc, REAL_HARD_MAX_ORDER, room, bal * 0.995, cash_cap if cash_cap is not None else 1e12)
        if upper >= min_need:
            await rlog(f"{symbol}: importo {amount:.2f} sotto il minimo di Bybit ({inst['min_amt']}): lo porto a {min_need:.2f}")
            amount = min_need
    if amount < float(inst["min_amt"]) or amount < 1.0:
        await rlog(f"{symbol}: importo {amount:.2f} troppo basso (minimo {inst['min_amt']}, saldo {bal:.2f}, spazio {room:.2f})", "errore")
        return None
    res = await real_execute(symbol, "Buy", amount, tag)
    if not res:
        return None
    res["value_spent"] = res["value"]
    return res


async def real_close_position(p: dict[str, Any], cfg: Config, tag: str) -> Optional[dict[str, Any]]:
    """Vendita reale di una posizione. Ritorna net_value (dollari entrati) o None se non riuscita."""
    r = p.get("real") or {}
    inst = await get_instrument(p["symbol"])
    if not inst:
        return None
    bal = await real_balance(inst["base"])
    if bal is None:
        return None
    qty = min(float(r.get("net_qty") or p["quantity"]), bal)
    qty_dec = RO.step_floor(qty, inst["base_step"])
    if qty_dec < inst["min_qty"] or qty_dec <= 0:
        await rlog(f"{p['symbol']}: nulla da vendere (saldo {bal}, minimo {inst['min_qty']}); posizione chiusa come polvere", "errore")
        cur = price_feed.get(p["symbol"]) or p["entry"]
        return {"avg_price": cur, "net_value": float(qty) * cur, "dust": True, "qty": float(qty)}
    res = await real_execute(p["symbol"], "Sell", qty_dec, tag)
    if res:
        # i resti sotto il passo minimo restano nel conto: li conto al prezzo di vendita
        res["dust_value"] = max(0.0, float(r.get("net_qty") or p["quantity"]) - float(qty_dec)) * res["avg_price"]
    return res


def real_sell_due(key: str, seconds: float = 30.0) -> bool:
    now = time.time()
    if now - _real_sell_at.get(key, 0.0) < seconds:
        return False
    _real_sell_at[key] = now
    return True


def _clamp_real_settings(new_cfg: Config) -> None:
    if not (0 < new_cfg.real_max_order_usdc <= REAL_HARD_MAX_ORDER):
        raise HTTPException(status_code=400, detail=f"Tetto per ordine tra 0 e {REAL_HARD_MAX_ORDER:g}")
    if not (0 < new_cfg.real_max_total_usdc <= REAL_HARD_MAX_TOTAL):
        raise HTTPException(status_code=400, detail=f"Tetto totale tra 0 e {REAL_HARD_MAX_TOTAL:g}")
    if not (0 < new_cfg.real_daily_loss_usdc <= REAL_HARD_MAX_DAILY_LOSS):
        raise HTTPException(status_code=400, detail=f"Perdita giornaliera tra 0 e {REAL_HARD_MAX_DAILY_LOSS:g}")
    for s in new_cfg.live_strategies:
        if s not in REAL_ALLOWED_STRATEGIES:
            raise HTTPException(status_code=400, detail=f"La strategia '{s}' non e' ancora collegabile al reale")


# ============================================================================
# CONTROLLO PRIMA DI ENTRARE: costi coperti, rischio/rendimento, libro ordini, perdita massima.
# Se non passa, il bot NON compra e scrive il motivo in "Diagnosi: perche' non apre?".
# Le regole di calcolo stanno in pre_entry_check.py.
# ============================================================================
try:
    import pre_entry_check as PEC
except ImportError:  # pragma: no cover
    from backend import pre_entry_check as PEC


async def precheck_settings() -> dict[str, Any]:
    doc = await db.precheck_config.find_one({"_id": "main"}, {"_id": 0})
    return PEC.clean_settings(doc)


async def precheck_entry(symbol: str, tf: Optional[str], notional: float, cfg: Config, mode: str, strategy: str,
                         *, stop: Optional[float] = None, target: Optional[float] = None,
                         use_max_loss: bool = True) -> tuple[bool, Optional[str], float]:
    """(ok, motivo, importo). Con un errore interno NON blocca (e scrive il problema nel diagnostico)."""
    try:
        s = await precheck_settings()
        if not s["enabled"] or (mode != "real" and not s["also_paper"]):
            return True, None, notional
        price = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
        if not price:
            return True, None, notional
        if (stop is None or target is None) and tf:
            candles = await exchange.get_klines(symbol, tf)
            highs = [c[3] for c in candles]
            lows = [c[4] for c in candles]
            closes = [c[2] for c in candles]
            atr = atr_wilder(highs, lows, closes, 14)
            stop, target = PEC.reference_levels(price, highs, lows, atr, s)
        _, info = await simulate_fill(symbol, "buy", price, cfg, quote_budget=notional)
        r = PEC.evaluate(price=price, notional=notional, fee_pct=cfg.sim_fee_pct, stop=stop, target=target,
                         book_cost_pct=float(info.get("cost_pct") or 0.0), spread_pct=info.get("spread_pct"),
                         book_model=str(info.get("fill_model")), s=s, use_max_loss=use_max_loss)
        if not r["ok"]:
            why = "controllo ingresso: " + "; ".join(r["reasons"])
            await log_reject(symbol, tf or "-", strategy, why)
            return False, why, notional
        return True, None, r["notional"]
    except Exception as e:  # noqa: BLE001
        await log_reject(symbol, tf or "-", strategy, f"controllo ingresso non riuscito ({e}): procedo senza")
        return True, None, notional


@api.get("/precheck/config")
async def precheck_get() -> dict[str, Any]:
    return {"settings": await precheck_settings(), "defaults": PEC.DEFAULTS}


@api.put("/precheck/config")
async def precheck_put(body: dict[str, Any]) -> dict[str, Any]:
    for k in body:
        if k not in PEC.DEFAULTS:
            raise HTTPException(status_code=400, detail=f"Impostazione sconosciuta: {k}")
    cur = await precheck_settings()
    cur.update(body)
    new = PEC.clean_settings(cur)
    await db.precheck_config.update_one({"_id": "main"}, {"$set": new}, upsert=True)
    return {"settings": new}


@api.get("/real/status")
async def real_status() -> dict[str, Any]:
    cfg = await get_config()
    creds = bool(await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 1}))
    log = await db.real_log.find({}, {"_id": 0}).sort("at", -1).to_list(30)
    open_rows = await db.s3360_positions.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    open_rows += await db.ict_positions.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    open_rows += await db.manual_setups.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    open_rows += await db.xrp_acc_positions.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    return {
        "enabled": cfg.real_trading_enabled,
        "live_strategies": cfg.live_strategies,
        "max_order": cfg.real_max_order_usdc,
        "max_total": cfg.real_max_total_usdc,
        "daily_loss_limit": cfg.real_daily_loss_usdc,
        "daily_pnl": round(await real_daily_pnl(), 4),
        "exposure": round(await real_open_exposure(), 4),
        "password_active": bool(BOT_ACCESS_KEY),
        "key_connected": creds,
        "open_positions": [{"symbol": r["symbol"], "notional": r.get("notional"), "fill_price": r.get("fill_price"),
                            "quantity": r.get("quantity"), "opened_at": r.get("opened_at")} for r in open_rows],
        "log": log,
    }


@api.put("/real/settings")
async def real_put_settings(body: dict[str, Any]) -> dict[str, Any]:
    cfg = await get_config()
    data = cfg.model_dump()
    for k, v in body.items():
        if k not in REAL_SETTING_KEYS:
            raise HTTPException(status_code=400, detail=f"Impostazione sconosciuta: {k}")
        data[k] = v
    try:
        new_cfg = Config(**data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Valore non valido: {e}")
    _clamp_real_settings(new_cfg)
    if new_cfg.real_trading_enabled and not cfg.real_trading_enabled:
        if not BOT_ACCESS_KEY:
            raise HTTPException(status_code=400, detail="Prima attiva la password di accesso al bot (variabile BOT_ACCESS_KEY su Railway).")
        if not await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 1}):
            raise HTTPException(status_code=400, detail="Prima collega la chiave Bybit.")
    await db.config.update_one({"_id": CONFIG_ID}, {"$set": {k: getattr(new_cfg, k) for k in body}}, upsert=True)
    await rlog("Impostazioni reale cambiate: " + ", ".join(f"{k}={getattr(new_cfg, k)}" for k in body))
    return {k: getattr(new_cfg, k) for k in REAL_SETTING_KEYS}


@api.get("/real/diagnosi")
async def real_diagnosi() -> dict[str, Any]:
    """Perche' non apre? Motivi di scarto piu' frequenti (ultime 6 ore) e RSI minimo visto ogni ora."""
    since = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    rows = await db.strategy_debug_log.find({"at": {"$gte": since}}, {"_id": 0}).to_list(20000)
    out: dict[str, list[dict[str, Any]]] = {}
    for strat in ("xrp_acc", "s3360"):
        cnt: dict[str, int] = {}
        for r in rows:
            if r.get("strategy") == strat:
                k = f"{r.get('symbol')} {r.get('timeframe')}: {r.get('reason')}"
                cnt[k] = cnt.get(k, 0) + 1
        out[strat] = [{"motivo": k, "volte": v} for k, v in sorted(cnt.items(), key=lambda x: -x[1])[:6]]
    rsi_rows = await db.rsi_watch.find({"strategy": "xrp_acc"}, {"_id": 0}).sort("hour", -1).to_list(200)
    cfg = await get_config()
    return {
        "motivi": out,
        "rsi_ore": [{"ora": r["hour"], "tf": r["tf"], "rsi_min": r.get("rsi_min"), "ultimo": r.get("last")} for r in rsi_rows[:48]],
        "xrp_acc": {"timeframes": cfg.xrp_acc_timeframes, "soglia": cfg.xrp_acc_low_threshold,
                    "filtro_trend_moneta": cfg.xrp_acc_require_coin_trend, "filtro_trend_btc": cfg.xrp_acc_require_btc_trend,
                    "filtro_sopra_media_lunga": cfg.xrp_acc_require_above_long_ema},
        "s3360": {"timeframes": cfg.s3360_timeframes, "soglia": cfg.s3360_low_threshold,
                  "filtro_trend_moneta": cfg.s3360_require_coin_trend, "filtro_trend_btc": cfg.s3360_require_btc_trend,
                  "filtro_sopra_media_lunga": cfg.s3360_require_above_long_ema},
    }


@api.post("/real/stop")
async def real_stop() -> dict[str, Any]:
    """Tasto STOP: spegne il reale (nessun nuovo ordine). Le posizioni aperte restano gestite e si possono chiudere."""
    await db.config.update_one({"_id": CONFIG_ID}, {"$set": {"real_trading_enabled": False}}, upsert=True)
    await rlog("STOP: reale spento dall'utente", "errore")
    return {"ok": True}


class RealTestRequest(BaseModel):
    symbol: str = "XRPUSDC"
    amount: float = 6.0


@api.post("/real/test-roundtrip")
async def real_test_roundtrip(req: RealTestRequest) -> dict[str, Any]:
    """Ordine di PROVA: compra per pochi dollari e rivende subito. Serve a vedere che tutto funziona e quanto costa."""
    cfg = await get_config()
    if not BOT_ACCESS_KEY:
        raise HTTPException(status_code=400, detail="Serve la password di accesso al bot.")
    if not cfg.real_trading_enabled:
        raise HTTPException(status_code=400, detail="Accendi prima l'interruttore del reale.")
    if not (0 < req.amount <= min(cfg.real_max_order_usdc, 25.0)):
        raise HTTPException(status_code=400, detail="Importo di prova troppo alto (massimo 25 e non oltre il tetto per ordine).")
    sym = req.symbol.upper()
    inst = await get_instrument(sym)
    if not inst:
        raise HTTPException(status_code=400, detail="Regole della coppia non disponibili: vedi il registro.")
    bal = await real_balance(inst["quote"])
    if bal is None or bal < req.amount:
        raise HTTPException(status_code=400, detail=f"Saldo {inst['quote']} insufficiente ({bal}).")
    buy = await real_execute(sym, "Buy", req.amount, "prova")
    if not buy:
        raise HTTPException(status_code=502, detail="Acquisto di prova non eseguito: vedi il registro.")
    bal_base = await real_balance(inst["base"])
    qty = min(buy["net_qty"], bal_base if bal_base is not None else buy["net_qty"])
    sell = None
    for _ in range(3):
        sell = await real_execute(sym, "Sell", qty, "prova")
        if sell:
            break
        await asyncio.sleep(2.0)
    if not sell:
        raise HTTPException(status_code=502, detail=f"Comprato {buy['net_qty']} {inst['base']} ma la vendita NON e' riuscita: vendi a mano su Bybit.")
    dust = max(0.0, buy["net_qty"] - sell["qty"]) * sell["avg_price"]
    pnl = sell["net_value"] + dust - buy["value"]
    await db.real_roundtrips.insert_one({"at": datetime.now(timezone.utc).isoformat(), "symbol": sym, "pnl": pnl})
    return {"symbol": sym, "comprato_a": buy["avg_price"], "venduto_a": sell["avg_price"], "speso": round(buy["value"], 4),
            "incassato": round(sell["net_value"], 4), "resti_rimasti": round(dust, 4), "costo_totale": round(-pnl, 4),
            "costo_pct": round(-pnl / buy["value"] * 100.0, 3)}


@api.post("/real/close-all")
async def real_close_all() -> dict[str, Any]:
    """Vende subito, a mercato, tutte le posizioni reali aperte di 33/60."""
    cfg = await get_config()
    rows = await db.s3360_positions.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    done, failed = 0, 0
    for p in rows:
        sell = await real_close_position(p, cfg, "chiudi")
        if not sell:
            failed += 1
            continue
        cur = sell["avg_price"]
        pnl = sell["net_value"] + sell.get("dust_value", 0.0) - p["notional"]
        await db.s3360_wallet.update_one({"_id": S3360_WALLET_ID}, {"$inc": {"cash": p["notional"] + pnl}}, upsert=True)
        await db.s3360_positions.update_one({"id": p["id"]}, {"$set": {
            "status": "closed", "close_price": cur, "close_reason": "chiusura_manuale_reale", "close_last_price": cur,
            "exit_fill_model": "reale", "fee_pct": cfg.sim_fee_pct, "pnl_usdt": round(pnl, 4),
            "closed_at": datetime.now(timezone.utc).isoformat(), "real_exit": {k: v for k, v in sell.items() if k != "dust"},
        }})
        await close_track(p["id"], cur)
        done += 1
    d2, f2 = await _ict_close_all_real()
    dx, fx = await _xrp_close_all_real()
    done += dx
    failed += fx
    d3, f3 = await _manual_close_all_real()
    return {"chiuse": done + d2 + d3, "non_riuscite": failed + f2 + f3}


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


_rsi_watch_state: dict[str, float] = {}
_rsi_watch_prune_at = 0.0


async def rsi_watch(strategy: str, symbol: str, tf: str, rsi: Optional[float]) -> None:
    """Diagnosi: tiene l'RSI MINIMO visto dal bot in ogni ora, per ogni timeframe.
    Scrive solo quando il minimo scende (o al massimo una volta al minuto): pochissimo carico."""
    global _rsi_watch_prune_at
    if rsi is None:
        return
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H")
    key = f"{strategy}|{symbol}|{tf}|{hour}"
    last = _rsi_watch_state.get(key)
    if last is not None and rsi >= last - 0.05 and time.time() - _rsi_watch_state.get(key + "|t", 0.0) < 60:
        return
    _rsi_watch_state[key] = min(rsi, last) if last is not None else rsi
    _rsi_watch_state[key + "|t"] = time.time()
    try:
        await db.rsi_watch.update_one(
            {"_id": key},
            {"$min": {"rsi_min": round(rsi, 2)}, "$set": {"strategy": strategy, "symbol": symbol, "tf": tf, "hour": hour, "last": round(rsi, 2), "at": now.isoformat()}},
            upsert=True,
        )
        if time.time() - _rsi_watch_prune_at > 3600:
            _rsi_watch_prune_at = time.time()
            old = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H")
            await db.rsi_watch.delete_many({"hour": {"$lt": old}})
            for k in [k for k in _rsi_watch_state if k.split("|")[-1] < old and not k.endswith("|t")]:
                _rsi_watch_state.pop(k, None)
    except Exception:  # noqa: BLE001
        pass


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
        await rsi_watch("xrp_acc", XRP_ACC_SYMBOL, tf, live_rsi)
        if cfg.xrp_acc_entry_mode in EC.CONFIRM_MODES:
            _ev = EC.evaluate(closes, live_price, rsi_wilder, mode=cfg.xrp_acc_entry_mode, period=cfg.xrp_acc_rsi_period,
                              low=cfg.xrp_acc_low_threshold, level=cfg.xrp_acc_confirm_level, lookback=cfg.xrp_acc_confirm_lookback,
                              ma_period=cfg.xrp_acc_confirm_ma_period, closed_only=cfg.xrp_acc_confirm_closed_only)
            if not _ev["ok"]:
                await _xrp_acc_log(tf, "nessun nuovo ingresso (" + _ev["reason"] + ")")
                continue
            if _ev["rsi"] is not None:
                live_rsi = _ev["rsi"]
        elif live_rsi > cfg.xrp_acc_low_threshold:
            await _xrp_acc_log(tf, "nessun nuovo ingresso sotto soglia bassa")
            continue
        tf_sec = TF_SECONDS.get(tf, 3600)
        forming_candle_t = candles[-1][0] + tf_sec
        if _xrp_acc_last_candle.get(tf) == forming_candle_t:
            await _xrp_acc_log(tf, "stessa candela già tentata")
            continue
        if cfg.xrp_acc_require_coin_trend or cfg.xrp_acc_require_btc_trend or cfg.xrp_acc_require_above_long_ema:
            why = await trend_filter_block_reason(
                XRP_ACC_SYMBOL, cfg, cfg.xrp_acc_require_coin_trend, cfg.xrp_acc_require_btc_trend,
                cfg.xrp_acc_require_above_long_ema, live_price,
            )
            if why:
                await _xrp_acc_log(tf, why)
                continue
        _xrp_acc_last_candle[tf] = forming_candle_t

        _pc_mode = "real" if (await real_guard(cfg, "xrp_acc"))[0] == "real" else "paper"
        _pc_ok, _pc_why, cash = await precheck_entry(XRP_ACC_SYMBOL, tf, cash, cfg, _pc_mode, "xrp_acc")
        if not _pc_ok:
            return
        # Reale (solo se "xrp_acc" e' tra le strategie con soldi veri) oppure simulazione.
        mode, why_real = await real_guard(cfg, "xrp_acc")
        if mode == "blocked":
            await log_reject(XRP_ACC_SYMBOL, tf, "xrp_acc", f"reale bloccato: {why_real}")
            return
        real_info = None
        notional = cash
        if mode == "real":
            rb = await real_open_buy(XRP_ACC_SYMBOL, cash, cfg, "xrp", cash_cap=cash)
            if not rb:
                await log_reject(XRP_ACC_SYMBOL, tf, "xrp_acc", "ordine reale non eseguito (vedi registro reale)")
                return
            real_info = rb
            notional = rb["value"]
            fill_price = rb["avg_price"]
            fill_info = {"fill_model": "reale", "cost_pct": 0.0, "spread_pct": None}
            quantity = rb["net_qty"]
        else:
            # Paper fill at the real ask (walking the book if needed) instead of the last traded price.
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
            "notional": notional,
            "status": "open",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        if real_info:
            doc["real"] = {k: real_info[k] for k in ("order_id", "link_id", "net_qty", "fee_base", "fee_quote", "qty")}
        await db.xrp_acc_positions.insert_one(dict(doc))
        await db.xrp_acc_wallet.update_one(
            {"_id": XRP_ACC_WALLET_ID}, {"$inc": {"cash": -notional}}, upsert=True
        )
        break


async def _xrp_acc_finish_real(p: dict[str, Any], sell: dict[str, Any], total_qty: float, reason: str, cur: float, cfg: Config) -> None:
    """Contabilita' di una vendita vera: il capitale torna in cassa, l'XRP NON venduto (il guadagno) entra nel gruzzolo permanente."""
    if sell.get("dust"):
        sold_qty, net_value, avg = 0.0, 0.0, float(sell.get("avg_price") or cur)
    else:
        sold_qty, net_value, avg = float(sell.get("qty") or 0.0), float(sell["net_value"]), float(sell["avg_price"])
    remaining = max(0.0, total_qty - sold_qty)
    cash_back = min(net_value, p["notional"])
    profit_usdt = net_value + remaining * avg - p["notional"]
    await db.xrp_acc_wallet.update_one({"_id": XRP_ACC_WALLET_ID}, {"$inc": {"cash": cash_back, "permanent_xrp": remaining}}, upsert=True)
    await db.xrp_acc_positions.update_one({"id": p["id"]}, {"$set": {
        "status": "closed", "close_price": avg, "close_reason": reason, "close_last_price": cur,
        "exit_fill_model": "reale", "fee_pct": cfg.sim_fee_pct,
        "profit_usdt": round(profit_usdt, 4), "pnl_usdt": round(profit_usdt, 4), "profit_xrp": round(remaining, 6),
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "real_exit": {k: v for k, v in sell.items() if k != "dust"},
    }})
    await close_track(p["id"], cur)


async def _xrp_close_all_real() -> tuple[int, int]:
    """STOP / chiudi tutto: vende subito tutta la posizione reale di XRP Accumulation."""
    cfg = await get_config()
    done, failed = 0, 0
    for p in await db.xrp_acc_positions.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(5):
        sell = await real_close_position(p, cfg, "chiudi")
        if not sell:
            failed += 1
            continue
        total = float((p.get("real") or {}).get("net_qty") or p["quantity"])
        await _xrp_acc_finish_real(p, sell, total, "chiusura_manuale_reale", sell.get("avg_price") or p["fill_price"], cfg)
        done += 1
    return done, failed


_xrp_acc_hold_log_at: dict[str, float] = {}


async def _xrp_acc_hold_log(p: dict[str, Any], reason: str = "RSI al target ma prezzo sotto l'entrata: attendo il recupero") -> None:
    """The monitor runs every 3 seconds; write the 'waiting' note at most once
    every 5 minutes per position so it stays visible without flooding the log."""
    now = time.time()
    if now - _xrp_acc_hold_log_at.get(p["id"], 0.0) < 300:
        return
    _xrp_acc_hold_log_at[p["id"]] = now
    await log_reject(
        p["symbol"], p.get("timeframe", "?"), "xrp_acc",
        reason,
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
        await record_track("xrp_acc", db.xrp_acc_positions, p, cur)
        tf = p.get("timeframe", "1h")
        candles = await exchange.get_klines(p["symbol"], tf)
        closes = [c[2] for c in candles] + [cur]
        rsis = rsi_wilder(closes, cfg.xrp_acc_rsi_period) if len(closes) > cfg.xrp_acc_rsi_period else []
        rsi_hit = bool(rsis and rsis[-1] >= cfg.xrp_acc_high_threshold)

        fill = p.get("fill_price", p["entry"])
        margin_price = required_exit_price(fill, cfg.xrp_acc_min_net_profit_pct, cfg.sim_fee_pct)
        break_even = required_exit_price(fill, 0.0, cfg.sim_fee_pct)
        # Trailing: armed when the last price first clears the minimum-net level,
        # then it follows the highest price. It sells if the price falls back by
        # xrp_acc_trailing_pct, but never below break-even.
        trail_hit = False
        if cfg.xrp_acc_hold_below_entry and cfg.xrp_acc_trailing_enabled:
            active, peak = await update_trailing_state(db.xrp_acc_positions, p, cur, margin_price)
            trail_hit = active and cur <= trailing_stop_price(fill, peak, cfg.xrp_acc_trailing_pct, cfg.sim_fee_pct)

        # The RSI target stays the main exit; the trailing protects the profit
        # when the price turns back before the target is reached.
        if rsi_hit and (not cfg.xrp_acc_hold_below_entry or cur >= margin_price):
            hit, floor_price = "target_rsi_raggiunto", margin_price
        elif trail_hit:
            hit, floor_price = "trailing_stop", break_even
        elif rsi_hit:
            # RSI reached the target but selling now would lock in less than the
            # minimum net profit: keep waiting. All the trading capital sits in
            # this one position, so while it waits no new entry can open.
            await _xrp_acc_hold_log(p)
            continue
        else:
            continue
        if cfg.xrp_acc_hold_below_entry and cur < floor_price:
            await _xrp_acc_hold_log(p, "Trailing: il prezzo non copre i costi, attendo" if hit == "trailing_stop" else "RSI al target ma prezzo sotto l'entrata: attendo il recupero")
            continue

        # The last price clears the floor; now ask what a market sell would
        # REALLY get (best bid / depth). With the simulation off this is `cur`
        # and costs no request. The book is read at most every 10s.
        if p.get("real"):
            # Vendita VERA. Prima il libro ordini (come la 33/60): se il prezzo realistico e' sotto il minimo, attendo.
            if cfg.xrp_acc_hold_below_entry and floor_price > 0 and cfg.sim_realistic_fills:
                if not book_recheck_due(p["id"]):
                    continue
                est_price, _est = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
                if est_price < floor_price:
                    await _xrp_acc_hold_log(p, "Vendita reale: il prezzo realistico dal libro ordini e' sotto il minimo: attendo")
                    continue
            if not real_sell_due(p["id"]):
                continue
            total_qty = float((p.get("real") or {}).get("net_qty") or p["quantity"])
            fee_k = 1.0 - cfg.sim_fee_pct / 100.0
            sell_p = dict(p)
            if cur * total_qty * fee_k > p["notional"] * 1.003:
                # c'e' guadagno: vendo SOLO la parte che riporta il capitale (+0,4% di margine); il resto resta in XRP
                part = min(total_qty, p["notional"] * 1.004 / (cur * fee_k))
                sell_p["real"] = {**(p.get("real") or {}), "net_qty": part}
                sell_p["quantity"] = part
            sell = await real_close_position(sell_p, cfg, "xrpx")
            if not sell:
                await rlog(f"Vendita reale XRP Accumulation non riuscita, riprovo tra 30 s", "errore")
                continue
            await _xrp_acc_finish_real(p, sell, total_qty, hit, cur, cfg)
            continue
        if cfg.sim_realistic_fills and not book_recheck_due(p["id"]):
            continue
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        if cfg.xrp_acc_hold_below_entry and sell_price < floor_price:
            # the price we would really get is still too low
            await _xrp_acc_hold_log(p, "Trailing: il prezzo di vendita vero non copre i costi, attendo" if hit == "trailing_stop" else "RSI al target ma prezzo sotto l'entrata: attendo il recupero")
            continue

        proceeds = sell_price * p["quantity"]
        fees = (p["notional"] + proceeds) * (cfg.sim_fee_pct / 100.0)
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
                "status": "closed", "close_price": sell_price, "close_reason": hit,
                "close_last_price": cur,
                "exit_fill_model": sell_info["fill_model"],
                "exit_cost_pct": sell_info["cost_pct"],
                "exit_spread_pct": sell_info.get("spread_pct"),
                "fee_pct": cfg.sim_fee_pct,
                "profit_usdt": round(profit_usdt, 4),
                "profit_xrp": round(profit_xrp, 6),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        await close_track(p["id"], cur)


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
            level = required_exit_price(basis, cfg.xrp_acc_min_net_profit_pct, cfg.sim_fee_pct)
            item["exit_level"] = level  # the real SELL price needed to close (with RSI at the target)
            item["pct_to_exit_level"] = round((level / cur - 1.0) * 100.0, 2) if cur else None
            item["break_even_price"] = required_exit_price(basis, 0.0, cfg.sim_fee_pct)
            if cfg.xrp_acc_trailing_enabled and p.get("trailing_active"):
                peak = float(p.get("peak_price") or cur)
                item["trailing_active"] = True
                item["peak_price"] = peak
                item["trailing_stop"] = trailing_stop_price(basis, peak, cfg.xrp_acc_trailing_pct, cfg.sim_fee_pct)
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
        "min_net_profit_pct": cfg.xrp_acc_min_net_profit_pct,
        "fee_pct": cfg.sim_fee_pct,
        "trailing_enabled": cfg.xrp_acc_trailing_enabled and cfg.xrp_acc_hold_below_entry,
        "trailing_pct": cfg.xrp_acc_trailing_pct,
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


# ============================================================================
# SETUP MANUALE — incolli un segnale (moneta, ingresso, stop, target, importo), il bot lo legge,
# mostra rischio/guadagno e SOLO dopo la tua conferma lo esegue (paper o reale, scelto per ogni setup).
# Ingresso a limite = il bot aspetta che il prezzo scenda al livello e poi compra a mercato.
# Stop e target sono sorvegliati dal bot ogni 3 secondi (NON sono ordini su Bybit): se il bot e' spento, non scattano.
# Solo long, spot, coppie USDC. Le regole di calcolo stanno in manual_logic.py.
# ============================================================================
try:
    import manual_logic as ML
except ImportError:  # pragma: no cover
    from backend import manual_logic as ML

MANUAL_MAX_ACTIVE = 5
MANUAL_EXPIRE_H = 48.0
_manual_lock = asyncio.Lock()


async def _manual_price(symbol: str) -> Optional[float]:
    try:
        p = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
    except Exception:  # noqa: BLE001
        p = None
    return float(p) if p and p > 0 else None


async def _manual_real_state(cfg: Config) -> tuple[str, Optional[str]]:
    """Il reale per il setup manuale chiede: interruttore generale acceso, password, chiave Bybit, limite giornaliero."""
    cfg2 = cfg.model_copy(update={"live_strategies": list(cfg.live_strategies) + ["manual"]})
    return await real_guard(cfg2, "manual")


def _manual_clean(setup: Any) -> dict[str, Any]:
    if not isinstance(setup, dict):
        raise HTTPException(status_code=400, detail="Setup mancante.")
    try:
        sym = str(setup["symbol"]).strip().upper()
        entry = setup.get("entry")
        out = {"symbol": sym, "entry": None if entry in (None, "", 0) else float(entry),
               "stop": float(setup["stop"]), "target": float(setup["target"]), "amount": float(setup["amount"]),
               "risk": None if setup.get("risk") in (None, "", 0) else float(setup["risk"])}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Setup incompleto o con numeri non validi.")
    return out


async def _manual_cancel(sid: str, reason: str) -> None:
    await db.manual_setups.update_one({"id": sid, "status": "waiting"}, {"$set": {
        "status": "cancelled", "close_reason": reason, "closed_at": datetime.now(timezone.utc).isoformat()}})


async def _manual_open(s: dict[str, Any], cfg: Config, live: float) -> tuple[bool, str]:
    """Compra (paper o reale) per un setup 'waiting'. In caso di errore il setup viene annullato con il motivo."""
    async with _manual_lock:
        cur_doc = await db.manual_setups.find_one({"id": s["id"], "status": "waiting"}, {"_id": 0})
        if not cur_doc:
            return False, "setup non piu' in attesa"
        sym, amount = s["symbol"], float(s["amount"])
        real_info = None
        _pc_ok, _pc_why, _ = await precheck_entry(
            sym, None, amount, cfg, s["mode"], "manuale",
            stop=float(s["stop"]), target=float(s["target"]), use_max_loss=False)
        if not _pc_ok:
            await _manual_cancel(s["id"], _pc_why or "controllo ingresso")
            return False, _pc_why or "controllo ingresso"
        if s["mode"] == "real":
            mode, why = await _manual_real_state(cfg)
            if mode != "real":
                await _manual_cancel(s["id"], f"reale non disponibile: {why}")
                return False, f"reale non disponibile: {why}"
            rb = await real_open_buy(sym, amount, cfg, "man", cash_cap=amount)
            if not rb:
                await _manual_cancel(s["id"], "ordine reale non eseguito (vedi registro reale)")
                return False, "ordine reale non eseguito (vedi registro reale)"
            real_info = rb
            fill_price, notional, quantity = rb["avg_price"], rb["value"], rb["net_qty"]
            fill_info = {"fill_model": "reale", "cost_pct": 0.0}
        else:
            fill_price, fill_info = await simulate_fill(sym, "buy", live, cfg, quote_budget=amount)
            if s.get("entry") is not None and fill_price > s["entry"]:
                fill_price = float(s["entry"])  # ordine a limite: riempimento al limite o meglio
            if fill_price <= s["stop"] or fill_price >= s["target"]:
                await _manual_cancel(s["id"], "prezzo di riempimento fuori dal setup")
                return False, "prezzo di riempimento fuori dal setup"
            notional, quantity = amount, amount / fill_price
        now = datetime.now(timezone.utc)
        upd = {"status": "open", "entry": fill_price, "fill_price": fill_price, "fill_model": fill_info["fill_model"],
               "entry_cost_pct": fill_info["cost_pct"], "quantity": quantity, "notional": notional,
               "opened_at": now.isoformat(), "opened_ts": now.timestamp(), "side": "long", "timeframe": "manuale"}
        if real_info:
            upd["real"] = {k: real_info[k] for k in ("order_id", "link_id", "net_qty", "fee_base", "fee_quote", "qty")}
        await db.manual_setups.update_one({"id": s["id"]}, {"$set": upd})
        return True, "aperta"


async def _manual_close(p: dict[str, Any], cfg: Config, reason: str, cur: float, force: bool = False) -> bool:
    if p.get("real"):
        if not force and not real_sell_due(p["id"]):
            return False
        sell = await real_close_position(p, cfg, "manx")
        if not sell:
            await rlog(f"Vendita reale del setup manuale {p['symbol']} non riuscita, riprovo tra 30 s", "errore")
            return False
        sell_price = sell["avg_price"]
        pnl = sell["net_value"] + sell.get("dust_value", 0.0) - p["notional"]
        extra = {"exit_fill_model": "reale", "real_exit": {k: v for k, v in sell.items() if k != "dust"}}
    else:
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        gross = (sell_price - p["fill_price"]) * p["quantity"]
        fees = (p["notional"] + sell_price * p["quantity"]) * (cfg.sim_fee_pct / 100.0)
        pnl = gross - fees
        extra = {"exit_fill_model": sell_info["fill_model"], "exit_cost_pct": sell_info["cost_pct"]}
    res = await db.manual_setups.update_one({"id": p["id"], "status": "open"}, {"$set": {
        "status": "closed", "close_price": sell_price, "close_reason": reason, "close_last_price": cur,
        "fee_pct": cfg.sim_fee_pct, "pnl_usdt": round(pnl, 4), "closed_at": datetime.now(timezone.utc).isoformat(), **extra}})
    await close_track(p["id"], cur)
    return bool(res.modified_count)


async def manual_loop() -> None:
    await asyncio.sleep(14)
    while True:
        try:
            cfg = await get_config()
            now_s = time.time()
            for s in await db.manual_setups.find({"status": "waiting"}, {"_id": 0}).to_list(20):
                cur = await _manual_price(s["symbol"])
                if cur is None:
                    continue
                ch = ML.waiting_check(s, cur, now_s) if s.get("entry") else "trigger"
                if ch == "trigger":
                    await _manual_open(s, cfg, cur)
                elif ch == "expired":
                    await _manual_cancel(s["id"], "scaduto (nessun ingresso entro %d ore)" % int(MANUAL_EXPIRE_H))
                elif ch == "invalid":
                    await _manual_cancel(s["id"], "il prezzo ha toccato stop o target prima dell'ingresso")
            for p in await db.manual_setups.find({"status": "open"}, {"_id": 0}).to_list(20):
                cur = await _manual_price(p["symbol"])
                if cur is None:
                    continue
                await record_track("manual", db.manual_setups, p, cur)
                hit = ML.exit_check(p, cur)
                if hit:
                    await _manual_close(p, cfg, hit, cur)
        except Exception as e:  # noqa: BLE001
            logger.exception("Setup manuale: errore nel ciclo: %s", e)
        await asyncio.sleep(3)


async def _manual_close_all_real() -> tuple[int, int]:
    cfg = await get_config()
    rows = await db.manual_setups.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    done, failed = 0, 0
    for p in rows:
        cur = await _manual_price(p["symbol"]) or p["fill_price"]
        if await _manual_close(p, cfg, "chiusura_manuale_reale", cur, force=True):
            done += 1
        else:
            failed += 1
    return done, failed


@api.post("/manual/parse")
async def manual_parse(body: dict[str, Any]) -> dict[str, Any]:
    cfg = await get_config()
    try:
        setup = ML.parse_signal(str(body.get("text", "")))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if body.get("amount") not in (None, ""):
        try:
            setup["amount"] = float(body["amount"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "Importo non valido."}
    price = await _manual_price(setup["symbol"])
    if price is None:
        return {"ok": False, "error": f"Non trovo il prezzo di {setup['symbol']} su Bybit: controlla il nome della moneta."}
    pl = ML.plan(setup, price, cfg.sim_fee_pct, real=False)
    mode, why = await _manual_real_state(cfg)
    real_why = None
    if mode != "real":
        real_why = why
    elif setup["amount"] > min(cfg.real_max_order_usdc, REAL_HARD_MAX_ORDER):
        real_why = "importo sopra il tetto per ordine (%.2f) della pagina Trading reale" % min(cfg.real_max_order_usdc, REAL_HARD_MAX_ORDER)
    elif setup["amount"] < 5.5:
        real_why = "importo sotto il minimo reale (5,50)"
    return {"ok": True, "setup": setup, "price": price, "plan": pl,
            "real": {"available": real_why is None, "why": real_why}}


@api.post("/manual/create")
async def manual_create(body: dict[str, Any]) -> dict[str, Any]:
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Serve la conferma.")
    mode = body.get("mode")
    if mode not in ("paper", "real"):
        raise HTTPException(status_code=400, detail="Scegli simulazione o reale.")
    cfg = await get_config()
    setup = _manual_clean(body.get("setup"))
    price = await _manual_price(setup["symbol"])
    if price is None:
        raise HTTPException(status_code=400, detail="Prezzo non disponibile per " + setup["symbol"])
    pl = ML.plan(setup, price, cfg.sim_fee_pct, real=(mode == "real"))
    if pl["errors"]:
        raise HTTPException(status_code=400, detail=" ".join(pl["errors"]))
    if mode == "real":
        st, why = await _manual_real_state(cfg)
        if st != "real":
            raise HTTPException(status_code=400, detail=f"Reale non disponibile: {why}")
        if setup["amount"] > min(cfg.real_max_order_usdc, REAL_HARD_MAX_ORDER):
            raise HTTPException(status_code=400, detail="Importo sopra il tetto per ordine della pagina Trading reale.")
    active = await db.manual_setups.find({"status": {"$in": ["waiting", "open"]}}, {"symbol": 1, "_id": 0}).to_list(50)
    if len(active) >= MANUAL_MAX_ACTIVE:
        raise HTTPException(status_code=400, detail=f"Hai gia' {MANUAL_MAX_ACTIVE} setup attivi: chiudine o annullane uno.")
    if any(a["symbol"] == setup["symbol"] for a in active):
        raise HTTPException(status_code=400, detail=f"C'e' gia' un setup attivo su {setup['symbol']}.")
    now = datetime.now(timezone.utc)
    market = setup["entry"] is None
    doc = {"id": str(uuid.uuid4()), "symbol": setup["symbol"], "mode": mode, "status": "waiting",
           "limit_entry": setup["entry"], "entry": setup["entry"] if not market else price,
           "stop": setup["stop"], "target": setup["target"], "amount": setup["amount"],
           "plan": {k: pl[k] for k in ("risk_pct", "gain_pct", "loss_net", "gain_net", "rr")},
           "created_at": now.isoformat(), "expires_ts": now.timestamp() + MANUAL_EXPIRE_H * 3600.0}
    await db.manual_setups.insert_one(dict(doc))
    if market or price <= doc["entry"]:
        ok, msg = await _manual_open(doc, cfg, price)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True, "status": "open", "id": doc["id"]}
    return {"ok": True, "status": "waiting", "id": doc["id"]}


@api.get("/manual/list")
async def manual_list() -> dict[str, Any]:
    cfg = await get_config()
    waiting = await db.manual_setups.find({"status": "waiting"}, {"_id": 0}).sort("created_at", -1).to_list(20)
    open_docs = await db.manual_setups.find({"status": "open"}, {"_id": 0}).sort("opened_at", -1).to_list(20)
    closed = await db.manual_setups.find({"status": {"$in": ["closed", "cancelled"]}}, {"_id": 0}).sort("closed_at", -1).to_list(60)
    w_out = []
    for w in waiting:
        w_out.append({**w, "current_price": await _manual_price(w["symbol"])})
    o_out = []
    for p in open_docs:
        cur = await _manual_price(p["symbol"]) or p["fill_price"]
        val = cur * p["quantity"]
        fl = val - p["notional"] - (p["notional"] + val) * (cfg.sim_fee_pct / 100.0)
        o_out.append({**p, "current_price": cur, "floating_pnl": round(fl, 4)})
    done = [c for c in closed if c["status"] == "closed"]
    return {"waiting": w_out, "open": o_out, "closed": closed,
            "trades": len(done), "wins": sum(1 for c in done if c.get("pnl_usdt", 0) > 0),
            "realized": round(sum(c.get("pnl_usdt", 0.0) for c in done), 4)}


@api.post("/manual/cancel/{sid}")
async def manual_cancel(sid: str) -> dict[str, Any]:
    if not await db.manual_setups.find_one({"id": sid, "status": "waiting"}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Nessun setup in attesa con questo codice.")
    await _manual_cancel(sid, "annullato da te")
    return {"ok": True}


@api.post("/manual/close/{sid}")
async def manual_close(sid: str) -> dict[str, Any]:
    cfg = await get_config()
    p = await db.manual_setups.find_one({"id": sid, "status": "open"}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Nessuna posizione aperta con questo codice.")
    cur = await _manual_price(p["symbol"]) or p["fill_price"]
    if not await _manual_close(p, cfg, "chiusura_manuale", cur, force=True):
        raise HTTPException(status_code=400, detail="Chiusura non riuscita: vedi il registro reale e riprova.")
    return {"ok": True}


# ============================================================================
# MEDIE MOBILI — strategia a incrocio di medie (SIMULAZIONE, nessun soldo vero).
# Compra quando la media veloce incrocia al rialzo quella lenta (sull'ultima candela CHIUSA),
# vende quando la veloce scende sotto la lenta. Solo acquisti (spot), coppie USDC.
# Le impostazioni si cambiano dalla pagina "Medie mobili" dell'app (non da Impostazioni).
# Le regole di calcolo stanno in ma_logic.py.
# ============================================================================
try:
    import ma_logic as MA
except ImportError:  # pragma: no cover
    from backend import ma_logic as MA

_ma_lock = asyncio.Lock()
_ma_candle_cache: dict[str, tuple[float, list[list[float]]]] = {}
_ma_prefix = "ma_"


async def _ma_price(symbol: str) -> Optional[float]:
    try:
        p = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
    except Exception:  # noqa: BLE001
        p = None
    return float(p) if p and p > 0 else None


async def _ma_get_cfg() -> dict[str, Any]:
    doc = await db.ma_config.find_one({"_id": "ma_config"}, {"_id": 0}) or {}
    out = dict(MA.DEFAULTS)
    out.update({k: v for k, v in doc.items() if k in MA.DEFAULTS})
    return out


async def _ma_event(symbol: str, text: str) -> None:
    try:
        await db.ma_events.insert_one({"at": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "text": text})
        n = await db.ma_events.count_documents({})
        if n > 150:
            old = await db.ma_events.find({}, {"_id": 1}).sort("at", 1).to_list(n - 100)
            await db.ma_events.delete_many({"_id": {"$in": [o["_id"] for o in old]}})
    except Exception:  # noqa: BLE001
        pass


async def _ma_candles(symbol: str, tf: str) -> list[list[float]]:
    """Candele CHIUSE (la ancora in formazione e' gia' esclusa). Copia riusata per un po': cambiano solo a candela chiusa."""
    key = f"{symbol}|{tf}"
    now = time.time()
    ttl = 60 if tf == "1h" else 180
    c = _ma_candle_cache.get(key)
    if c and c[1] and now - c[0] < ttl:
        return c[1]
    candles = await exchange.get_klines(symbol, tf, limit=500)
    if candles:
        _ma_candle_cache[key] = (now, candles)
        return candles
    if c and c[1] and now - c[0] < 1800:
        return c[1]
    return []


async def _ma_analyze(symbol: str, tf: str, kind: str, fast: int, slow: int) -> tuple[dict[str, Any], float]:
    candles = await _ma_candles(symbol, tf)
    if not candles:
        return {"ok": False, "reason": "candele non disponibili"}, 0.0
    an = MA.analyze([c[2] for c in candles], kind, fast, slow)
    return an, float(candles[-1][0])


async def _ma_open(symbol: str, cfg: Config, mc: dict[str, Any], an: dict[str, Any], live: float) -> bool:
    async with _ma_lock:
        if await db.ma_positions.find_one({"symbol": symbol, "status": "open"}, {"_id": 1}):
            return False
        amount = float(mc["amount"])
        fill_price, fill_info = await simulate_fill(symbol, "buy", live, cfg, quote_budget=amount)
        now = datetime.now(timezone.utc)
        doc = {"id": str(uuid.uuid4()), "symbol": symbol, "status": "open", "side": "long", "timeframe": mc["tf"],
               "kind": mc["kind"], "fast": mc["fast"], "slow": mc["slow"], "stop_pct": mc["stop_pct"],
               "entry": fill_price, "fill_price": fill_price, "fill_model": fill_info["fill_model"],
               "entry_cost_pct": fill_info["cost_pct"], "notional": amount, "quantity": amount / fill_price,
               "opened_at": now.isoformat(), "opened_ts": now.timestamp(),
               "entry_fast": an.get("fast"), "entry_slow": an.get("slow")}
        await db.ma_positions.insert_one(doc)
        await _ma_event(symbol, "COMPRATO a %.6g (%s %d/%d su %s, importo %.2f)" % (fill_price, mc["kind"], mc["fast"], mc["slow"], mc["tf"], amount))
        return True


async def _ma_close(p: dict[str, Any], cfg: Config, reason: str, cur: float) -> bool:
    async with _ma_lock:
        fresh = await db.ma_positions.find_one({"id": p["id"], "status": "open"}, {"_id": 0})
        if not fresh:
            return False
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        gross = (sell_price - p["fill_price"]) * p["quantity"]
        fees = (p["notional"] + sell_price * p["quantity"]) * (cfg.sim_fee_pct / 100.0)
        pnl = gross - fees
        res = await db.ma_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {
            "status": "closed", "close_price": sell_price, "close_reason": reason, "close_last_price": cur,
            "fee_pct": cfg.sim_fee_pct, "pnl_usdt": round(pnl, 4), "closed_at": datetime.now(timezone.utc).isoformat(),
            "exit_fill_model": sell_info["fill_model"], "exit_cost_pct": sell_info["cost_pct"]}})
        if res.modified_count:
            await _ma_event(p["symbol"], "VENDUTO a %.6g (%s) risultato %+.2f" % (sell_price, reason.replace("_", " "), pnl))
        return bool(res.modified_count)


async def ma_pass() -> None:
    cfg = await get_config()
    mc = await _ma_get_cfg()
    # 1) uscite: sempre sorvegliate, anche con la strategia spenta (con i parametri usati all'ingresso)
    for p in await db.ma_positions.find({"status": "open"}, {"_id": 0}).to_list(50):
        cur = await _ma_price(p["symbol"])
        if cur is None:
            continue
        an, _ts = await _ma_analyze(p["symbol"], p["timeframe"], p["kind"], p["fast"], p["slow"])
        hit = MA.exit_decision(an, cur, p["fill_price"], float(p.get("stop_pct") or 0.0))
        if hit:
            await _ma_close(p, cfg, hit, cur)
    # 2) ingressi: solo con la strategia accesa
    if not mc["enabled"]:
        return
    open_rows = await db.ma_positions.find({"status": "open"}, {"symbol": 1, "_id": 0}).to_list(50)
    open_syms = {r["symbol"] for r in open_rows}
    for sym in mc["symbols"]:
        an, cand_ts = await _ma_analyze(sym, mc["tf"], mc["kind"], mc["fast"], mc["slow"])
        if not an.get("ok") or an["cross"] != "up":
            continue
        st = await db.ma_state.find_one({"_id": sym + "|" + mc["tf"] + "|" + mc["kind"] + str(mc["fast"]) + "/" + str(mc["slow"])}) or {}
        last_done = float(st.get("ts", 0.0))
        act, why = MA.entry_decision(an, cand_ts, last_done, len(open_syms), int(mc["max_positions"]), sym in open_syms)
        if act == "skip" and why == "incrocio gia' valutato":
            continue
        await db.ma_state.update_one({"_id": sym + "|" + mc["tf"] + "|" + mc["kind"] + str(mc["fast"]) + "/" + str(mc["slow"])},
                                     {"$set": {"ts": cand_ts}}, upsert=True)
        if act != "buy":
            await _ma_event(sym, "Incrocio al rialzo ma non compro: " + why)
            continue
        live = await _ma_price(sym)
        if live is None:
            await _ma_event(sym, "Incrocio al rialzo ma prezzo non disponibile: salto")
            continue
        if await _ma_open(sym, cfg, mc, an, live):
            open_syms.add(sym)


async def ma_loop() -> None:
    await asyncio.sleep(16)
    while True:
        try:
            await ma_pass()
        except Exception as e:  # noqa: BLE001
            logger.exception("Medie mobili: errore nel ciclo: %s", e)
        await asyncio.sleep(5)


@api.get("/ma/status")
async def ma_status() -> dict[str, Any]:
    cfg = await get_config()
    mc = await _ma_get_cfg()
    open_docs = await db.ma_positions.find({"status": "open"}, {"_id": 0}).sort("opened_at", -1).to_list(50)
    open_syms = {p["symbol"] for p in open_docs}
    coins = []
    for sym in mc["symbols"]:
        row: dict[str, Any] = {"symbol": sym, "in_position": sym in open_syms, "price": await _ma_price(sym)}
        an, _ = await _ma_analyze(sym, mc["tf"], mc["kind"], mc["fast"], mc["slow"])
        if an.get("ok"):
            row.update({"fast": an["fast"], "slow": an["slow"], "above": an["above"], "dist_pct": round(an["dist_pct"], 2), "cross": an["cross"]})
        else:
            row["note"] = an.get("reason", "")
        coins.append(row)
    o_out = []
    for p in open_docs:
        cur = await _ma_price(p["symbol"]) or p["fill_price"]
        val = cur * p["quantity"]
        fl = val - p["notional"] - (p["notional"] + val) * (cfg.sim_fee_pct / 100.0)
        o_out.append({**p, "current_price": cur, "floating_pnl": round(fl, 4)})
    closed = await db.ma_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(60)
    all_closed = await db.ma_positions.find({"status": "closed"}, {"pnl_usdt": 1, "_id": 0}).to_list(5000)
    events = await db.ma_events.find({}, {"_id": 0}).sort("at", -1).to_list(25)
    return {"config": mc, "coins": coins, "open": o_out, "closed": closed, "events": events,
            "trades": len(all_closed), "wins": sum(1 for c in all_closed if c.get("pnl_usdt", 0) > 0),
            "realized": round(sum(c.get("pnl_usdt", 0.0) for c in all_closed), 4)}


@api.post("/ma/config")
async def ma_set_config(body: dict[str, Any]) -> dict[str, Any]:
    cur = await _ma_get_cfg()
    try:
        new = MA.clean_config(body, cur)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if new["symbols"] != cur["symbols"]:
        for s in new["symbols"]:
            if await _ma_price(s) is None:
                raise HTTPException(status_code=400, detail="Non trovo il prezzo di %s su Bybit: controlla il nome (esempio BTCUSDC)." % s)
    await db.ma_config.update_one({"_id": "ma_config"}, {"$set": new}, upsert=True)
    return {"ok": True, "config": new}


@api.post("/ma/close/{pid}")
async def ma_close_now(pid: str) -> dict[str, Any]:
    cfg = await get_config()
    p = await db.ma_positions.find_one({"id": pid, "status": "open"}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Nessuna posizione aperta con questo codice.")
    cur = await _ma_price(p["symbol"]) or p["fill_price"]
    if not await _ma_close(p, cfg, "chiusura_manuale", cur):
        raise HTTPException(status_code=400, detail="Chiusura non riuscita, riprova.")
    return {"ok": True}


# ============================================================================
# GESTIONE TRADE
# Ogni N minuti il bot rilegge il grafico delle posizioni APERTE (solo quelle di Analisi e rischio, non quelle del Terminale):
# se la situazione e' peggiorata puo' stringere lo stop o chiudere in anticipo, ma SOLO in guadagno netto, cioe' oltre tutti i costi
# (commissioni di entrata e uscita, funding maturato, piccolo margine). In perdita non interviene: decide lo stop di sempre.
# Ogni uscita anticipata / stop stretto viene confrontata con "cosa sarebbe successo senza" (operazione ombra).
# ============================================================================
MNG_DEFAULT: dict[str, Any] = {"enabled": True, "mode": "chiudi", "every_min": 15}
MNG_MODES = ("chiudi", "stringi", "avvisa")
MNG_GRACE_MIN = 30           # nei primi 30 minuti non tocca nulla
MNG_SLIP_PCT = 0.05          # margine extra sui costi (scivolamento in uscita), in % del prezzo
MNG_SHADOW_H = 72            # l'operazione ombra si chiude da sola dopo 72 ore
_mng_cache: dict[str, Any] = {"at": 0.0, "v": dict(MNG_DEFAULT)}


async def mng_cfg(force: bool = False) -> dict[str, Any]:
    if not force and time.time() - float(_mng_cache["at"]) < 10:
        return dict(_mng_cache["v"])
    doc = await db.crus_config.find_one({"_id": "mng"}, {"_id": 0}) or {}
    v = dict(MNG_DEFAULT)
    v.update({k: doc[k] for k in MNG_DEFAULT if k in doc})
    _mng_cache["at"], _mng_cache["v"] = time.time(), v
    return dict(v)


def mng_cost_px(side: str, fill: float, qty: float, fee_pct: float, carry_usdc: float, slip_pct: float = MNG_SLIP_PCT) -> float:
    """Prezzo oltre il quale chiudere ora dà un guadagno NETTO: commissioni (entrata + uscita), funding maturato, piccolo margine."""
    unit = fill * (2.0 * fee_pct + slip_pct) / 100.0 + (carry_usdc / qty if qty > 0 else 0.0)
    return fill + unit if side == "long" else fill - unit


def mng_assess(side: str, cur: float, atr1: float, c1h: list[list[float]], an: dict[str, Any], entry_bias: str,
               peak_r: float, r_now: float) -> dict[str, Any]:
    """Quanti segnali di pericolo ci sono ora (0 = tutto come all'apertura)."""
    sg = 1.0 if side == "long" else -1.0
    opp = "short" if side == "long" else "long"
    danger = 0
    sig: list[str] = []
    bias = an.get("bias")
    if bias == opp:
        danger += 2
        sig.append("l'indicazione di mercato è passata a %s" % opp)
    elif bias == "attendi" and entry_bias == side:
        danger += 1
        sig.append("le tendenze non sono più d'accordo")
    closed = c1h[:-1] if len(c1h) > 14 else []      # l'ultima candela e' ancora in formazione
    if closed:
        last, prev = closed[-1], closed[-9:-1]
        if prev and side == "long" and last[2] < min(c[4] for c in prev):
            danger += 2
            sig.append("rotto l'ultimo minimo (grafico 1h)")
        if prev and side == "short" and last[2] > max(c[3] for c in prev):
            danger += 2
            sig.append("rotto l'ultimo massimo (grafico 1h)")
        closes = [c[2] for c in closed]
        e20, e20b = DK.ema(closes, 20), DK.ema(closes[:-3], 20)
        if e20 is not None and e20b is not None and (last[2] - e20) * sg < 0 and (e20 - e20b) * sg < 0:
            danger += 1
            sig.append("prezzo oltre la media a 20 (1h) contro la posizione, e la media gira")
        if atr1 > 0 and (last[2] - last[1]) * sg < -1.5 * atr1:
            danger += 1
            sig.append("candela 1h molto forte contro la posizione")
    if peak_r >= 1.0 and r_now <= peak_r * 0.5:
        danger += 2
        sig.append("ha restituito metà del guadagno massimo (%+.1fR → %+.1fR)" % (peak_r, r_now))
    return {"danger": danger, "signals": sig}


def mng_decide(*, side: str, stop: float, cur: float, atr1: float, mode: str, danger: int, prev_danger: int,
               be_px: float, grace_ok: bool) -> dict[str, Any]:
    """Decide: tieni / stringi (nuovo stop) / chiudi / avvisa. In perdita (o sotto i costi) NON chiude e NON stringe."""
    sg = 1.0 if side == "long" else -1.0
    hold = {"action": "tieni", "reason": None, "new_stop": None}
    if not grace_ok or danger < 2 or atr1 <= 0:
        return hold
    if (cur - be_px) * sg <= 0.15 * atr1:                      # non e' chiaramente in guadagno netto
        return {"action": "avvisa" if danger >= 3 else "tieni", "new_stop": None,
                "reason": "pericolo %d, ma la posizione è sotto il pareggio dei costi: non esco, lascio decidere lo stop" % danger}
    if mode == "avvisa":
        return {"action": "avvisa" if danger >= 3 else "tieni", "new_stop": None, "reason": "pericolo %d (modalità solo avviso)" % danger}
    if mode == "chiudi" and danger >= 4 and prev_danger >= 3:    # confermato da due controlli di fila
        return {"action": "chiudi", "new_stop": None, "reason": "pericolo %d confermato da due controlli: chiudo in guadagno netto" % danger}
    gap = atr1 * (0.5 if danger >= 4 else 1.0)
    cand = cur - sg * gap
    new = max(stop, cand, be_px) if side == "long" else min(stop, cand, be_px)     # mai sotto il pareggio dei costi
    new = min(new, cur - 0.1 * atr1) if side == "long" else max(new, cur + 0.1 * atr1)
    if (new - stop) * sg <= 0.1 * atr1:
        return hold                                              # lo stop protegge gia' abbastanza
    return {"action": "stringi", "new_stop": new, "reason": "pericolo %d: stringo lo stop per proteggere il guadagno" % danger}


def mng_view(o: dict[str, Any]) -> dict[str, Any]:
    managed = o.get("source") != "terminale"
    return {"managed": managed, "danger": o.get("mng_danger"), "signals": o.get("mng_signals") or [], "action": o.get("mng_action"),
            "reason": o.get("mng_reason"), "at": o.get("mng_last_ts"), "tightened": bool(o.get("mng_orig_stop")),
            "peak_r": o.get("mng_peak_r"), "orig_stop": o.get("mng_orig_stop")}


async def mng_review(p: dict[str, Any], cfg: Config, mc: dict[str, Any], cur: float, r_now: float, peak: float) -> None:
    sym = p["symbol"]
    frames = {tf: await _desk_candles(sym, tf) for tf in ("1d", "4h", "1h")}
    if not frames["1h"]:
        return
    an = DK.analyze_market(frames)
    if not an.get("ok"):
        return
    atr1 = DK.atr(frames["1h"]) or 0.0
    now = time.time()
    side = p["side"]
    days = max(0.0, (now - float(p["opened_ts"])) / 86400.0)
    carry = p["notional"] * (p["funding_day_pct"] / 100.0) * days if (p["leverage"] > 1.0 or side == "short") else 0.0
    be_px = mng_cost_px(side, float(p["fill_price"]), float(p["quantity"]), _desk_fee(cfg), carry)
    a = mng_assess(side, cur, atr1, frames["1h"], an, str(p.get("bias_at_entry") or ""), peak, r_now)
    prev = int(p.get("mng_danger") or 0)
    dec = mng_decide(side=side, stop=float(p["stop"]), cur=cur, atr1=atr1, mode=str(mc["mode"]), danger=a["danger"], prev_danger=prev,
                     be_px=be_px, grace_ok=now - float(p["opened_ts"]) >= MNG_GRACE_MIN * 60)
    why = "; ".join(a["signals"]) if a["signals"] else "nessun segnale di pericolo"
    upd: dict[str, Any] = {"mng_last_ts": now, "mng_danger": a["danger"], "mng_signals": a["signals"], "mng_action": dec["action"],
                           "mng_reason": dec["reason"], "mng_peak_r": round(peak, 3), "mng_be_px": be_px}
    if dec["action"] == "stringi":
        new = float(dec["new_stop"])
        upd["stop"] = new
        if not p.get("mng_orig_stop"):
            upd["mng_orig_stop"] = p["stop"]
        r = await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
        if r.modified_count:
            await _desk_event(sym, "GESTIONE: stop stretto da %.6g a %.6g (guadagno protetto oltre i costi). Motivi: %s" % (p["stop"], new, why))
        return
    await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
    if dec["action"] == "chiudi":
        await _desk_event(sym, "GESTIONE: chiudo in anticipo in guadagno netto. Motivi: %s" % why)
        await _desk_close(p, cfg, "uscita_anticipata", cur)
    elif dec["action"] == "avvisa":
        sig = __import__("re").sub(r"\d+(\.\d+)?", "#", why + str(dec["reason"]))
        if p.get("mng_alert_sig") != sig or now - float(p.get("mng_alert_ts") or 0) > 3600:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_alert_sig": sig, "mng_alert_ts": now}})
            await _desk_event(sym, "GESTIONE: attenzione. %s. Motivi: %s" % (dec["reason"], why))


async def mng_manage(cfg: Config) -> None:
    mc = await mng_cfg()
    if not mc["enabled"]:
        return
    every = max(5, int(mc["every_min"])) * 60
    for p in await db.desk_positions.find({"status": "open"}, {"_id": 0}).to_list(50):
        if p.get("source") == "terminale":
            continue
        cur = await _desk_price(p["symbol"])
        if cur is None:
            continue
        fill, stop = float(p["fill_price"]), float(p["stop"])
        sg = 1.0 if p["side"] == "long" else -1.0
        risk0 = float(p.get("mng_risk0") or 0.0)
        if risk0 <= 0:                                           # distanza di rischio iniziale (non cambia quando lo stop si sposta)
            rr = float(p.get("rr") or 0.0)
            risk0 = abs(fill - stop) if (not p.get("tp1_done") and not p.get("mng_orig_stop")) else (abs(float(p["target"]) - fill) / rr if rr > 0 else abs(fill - stop))
            if risk0 > 0:
                await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_risk0": risk0}})
        if risk0 <= 0:
            continue
        r_now = (cur - fill) * sg / risk0
        peak = max(float(p.get("mng_peak_r") if p.get("mng_peak_r") is not None else r_now), r_now)
        if peak > float(p.get("mng_peak_r") if p.get("mng_peak_r") is not None else -999) + 0.02:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_peak_r": round(peak, 3)}})
        if time.time() - float(p.get("mng_last_ts") or 0) >= every:
            try:
                await mng_review(p, cfg, mc, cur, r_now, peak)
            except Exception as e:  # noqa: BLE001
                logger.exception("Gestione trade: errore su %s: %s", p.get("symbol"), e)


async def mng_shadow_scan() -> None:
    """Per ogni uscita anticipata o stop stretto fa partire l'operazione ombra: cosa sarebbe successo lasciando stop e obiettivo originali."""
    closed = await db.desk_positions.find(
        {"status": "closed", "$or": [{"close_reason": "uscita_anticipata"}, {"close_reason": "stop", "mng_orig_stop": {"$exists": True}}]},
        {"_id": 0}).sort("closed_at", -1).to_list(150)
    if not closed:
        return
    have = {d["pos_id"] for d in await db.mng_shadow.find({}, {"_id": 0, "pos_id": 1}).to_list(2000)}
    for p in closed:
        if p["id"] in have:
            continue
        orig = float(p.get("mng_orig_stop") or p["stop"])
        now = time.time()
        await db.mng_shadow.insert_one({
            "pos_id": p["id"], "symbol": p["symbol"], "side": p["side"], "fill": float(p["fill_price"]), "qty": float(p["quantity"]),
            "margin": float(p["margin"]), "notional": float(p["notional"]), "lev": float(p["leverage"]), "stop": orig, "target": float(p["target"]),
            "liq": p.get("liquidation"), "fd": float(p["funding_day_pct"]), "opened_ts": float(p["opened_ts"]),
            "start_price": p.get("close_price"), "partial": float(p.get("realized_partial") or 0.0), "actual": float(p.get("pnl_usdt") or 0.0),
            "kind": "uscita" if p.get("close_reason") == "uscita_anticipata" else "stop_stretto", "status": "open",
            "created_ts": now, "created_at": datetime.now(timezone.utc).isoformat()})


async def mng_shadow_resolve(cfg: Config) -> None:
    for s in await db.mng_shadow.find({"status": "open"}, {"_id": 0}).to_list(100):
        cur = await _desk_price(s["symbol"])
        if cur is None:
            continue
        hit = DK.exit_hit(s["side"], cur, s["stop"], s["target"], s.get("liq"))
        ex, out = None, None
        if hit == "liquidazione":
            ex, out = float(s["liq"]), "liquidazione"
        elif hit == "stop":
            ex, out = s["stop"], "stop"
        elif hit == "obiettivo":
            ex, out = s["target"], "obiettivo"
        elif time.time() - float(s["created_ts"]) > MNG_SHADOW_H * 3600:
            ex, out = cur, "scaduta"
        if out is None:
            continue
        days = max(0.0, (time.time() - float(s["opened_ts"])) / 86400.0)
        carry = s["notional"] * (s["fd"] / 100.0) * days if (s["lev"] > 1.0 or s["side"] == "short") else 0.0
        rest = DK.pnl(s["side"], s["fill"], ex, s["qty"], _desk_fee(cfg), carry, s["margin"])
        total = s["partial"] + rest
        await db.mng_shadow.update_one({"pos_id": s["pos_id"], "status": "open"}, {"$set": {
            "status": "done", "outcome": out, "shadow_pnl": round(total, 4), "delta": round(s["actual"] - total, 4),
            "done_at": datetime.now(timezone.utc).isoformat()}})


async def mng_summary() -> dict[str, Any]:
    docs = await db.mng_shadow.find({}, {"_id": 0}).sort("created_ts", -1).to_list(500)
    done = [d for d in docs if d.get("status") == "done"]
    helped = [d for d in done if d["delta"] > 0.005]
    hurt = [d for d in done if d["delta"] < -0.005]
    def net(k: str) -> float:
        return round(sum(d["delta"] for d in done if d["kind"] == k), 4)
    return {"n_done": len(done), "n_pending": len([d for d in docs if d.get("status") == "open"]), "helped": len(helped), "hurt": len(hurt),
            "net": round(sum(d["delta"] for d in done), 4), "net_uscita": net("uscita"), "net_stop": net("stop_stretto"),
            "n_uscita": len([d for d in done if d["kind"] == "uscita"]), "n_stop": len([d for d in done if d["kind"] == "stop_stretto"]),
            "items": [{"symbol": d["symbol"], "side": d["side"], "kind": d["kind"], "status": d["status"], "actual": round(d["actual"], 4),
                       "shadow": d.get("shadow_pnl"), "delta": d.get("delta"), "outcome": d.get("outcome"), "at": d.get("created_at")}
                      for d in docs[:10]]}


@api.get("/gestione")
async def mng_get() -> dict[str, Any]:
    return {"config": await mng_cfg(True), "summary": await mng_summary()}


@api.post("/gestione")
async def mng_set(body: dict[str, Any]) -> dict[str, Any]:
    cur = await mng_cfg(True)
    new = dict(cur)
    if "enabled" in body:
        new["enabled"] = bool(body["enabled"])
    if "mode" in body:
        if body["mode"] not in MNG_MODES:
            raise HTTPException(status_code=400, detail="Modalità non valida.")
        new["mode"] = body["mode"]
    if "every_min" in body:
        try:
            v = int(float(body["every_min"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Minuti non validi.")
        if not (5 <= v <= 240):
            raise HTTPException(status_code=400, detail="Ogni quanti minuti: da 5 a 240.")
        new["every_min"] = v
    await db.crus_config.update_one({"_id": "mng"}, {"$set": new}, upsert=True)
    await mng_cfg(True)
    return {"ok": True, "config": new}


async def mng_loop() -> None:
    await asyncio.sleep(25)
    while True:
        try:
            cfg = await get_config()
            await mng_manage(cfg)
            await mng_shadow_scan()
            await mng_shadow_resolve(cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("Gestione trade: errore nel ciclo: %s", e)
        await asyncio.sleep(30)


# ============================================================================
# CRUSCOTTO
# Una sola schermata con tutto: portafoglio simulato dedicato (long e short), curva del capitale, esposizione,
# limiti di rischio, posizioni con distanza da stop/obiettivo/liquidazione, stato di bot e Terminale, eventi.
# Il portafoglio REALE su Bybit e' in SOLA LETTURA (saldi): nessun ordine parte da qui.
# ============================================================================
_crus_real_cache: dict[str, Any] = {"at": 0.0, "val": None}


def crus_stats(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Statistiche sulle posizioni chiuse (piu' recenti per prime nell'input)."""
    def part(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pn = [float(r.get("pnl_usdt") or 0.0) for r in rows]
        w = [x for x in pn if x > 0]
        l = [x for x in pn if x < 0]
        gl = -sum(l)
        return {"n": len(pn), "wins": len(w), "pnl": round(sum(pn), 4),
                "win_rate": round(100.0 * len(w) / len(pn), 1) if pn else None,
                "avg_win": round(sum(w) / len(w), 4) if w else None,
                "avg_loss": round(sum(l) / len(l), 4) if l else None,
                "profit_factor": round(sum(w) / gl, 2) if gl > 1e-12 else None,
                "expectancy": round(sum(pn) / len(pn), 4) if pn else None}
    out = part(closed)
    out["long"] = part([c for c in closed if c.get("side") == "long"])
    out["short"] = part([c for c in closed if c.get("side") == "short"])
    pn = [float(c.get("pnl_usdt") or 0.0) for c in closed]
    out["best"] = round(max(pn), 4) if pn else None
    out["worst"] = round(min(pn), 4) if pn else None
    return out


def crus_drawdown(values: list[float]) -> float:
    """Massimo calo percentuale dal picco, sulla curva del capitale."""
    peak, dd = None, 0.0
    for v in values:
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = max(dd, (peak - v) / peak * 100.0)
    return round(dd, 2)


def crus_position_view(p: dict[str, Any], cur: float) -> dict[str, Any]:
    """Distanze in % dal prezzo attuale e avanzamento fra stop (0) e obiettivo (100)."""
    side = p["side"]
    stop, target, liq, fill = float(p["stop"]), float(p["target"]), p.get("liquidation"), float(p["fill_price"])
    sgn = 1.0 if side == "long" else -1.0
    span = (target - stop) * sgn
    prog = ((cur - stop) * sgn / span * 100.0) if span > 0 else None
    risk_px = abs(fill - stop)
    r_now = ((cur - fill) * sgn / risk_px) if risk_px > 0 else None
    return {"to_stop_pct": round(abs(cur - stop) / cur * 100.0, 2), "to_target_pct": round(abs(target - cur) / cur * 100.0, 2),
            "to_liq_pct": round(abs(float(liq) - cur) / cur * 100.0, 2) if liq else None,
            "progress": None if prog is None else round(max(-20.0, min(120.0, prog)), 1),
            "r_now": None if r_now is None else round(r_now, 2),
            "move_pct": round((cur - fill) * sgn / fill * 100.0, 2)}


async def crus_snapshot(force: bool = False) -> None:
    """Salva un punto della curva del capitale (al massimo uno ogni 4 minuti)."""
    last = await db.desk_equity.find_one({}, {"_id": 0, "t": 1}, sort=[("t", -1)])
    now = time.time()
    if last and not force and now - float(last["t"]) < 240:
        return
    s = await desk_status()
    await db.desk_equity.insert_one({"t": now, "equity": s["equity"], "cash": s["cash"], "realized": s["realized"]})
    n = await db.desk_equity.count_documents({})
    if n > 6000:
        old = await db.desk_equity.find({}, {"_id": 1}).sort("t", 1).to_list(n - 5000)
        await db.desk_equity.delete_many({"_id": {"$in": [o["_id"] for o in old]}})


async def crus_real() -> dict[str, Any]:
    """Portafoglio reale su Bybit, SOLO LETTURA. Cache 30 secondi."""
    if _crus_real_cache["val"] is not None and time.time() - _crus_real_cache["at"] < 30:
        return _crus_real_cache["val"]
    out: dict[str, Any] = {"connected": False, "error": None, "total_usd": None, "coins": [],
                           "note": "Solo lettura dei saldi. Bybit EU (spot) non permette lo short con questa chiave."}
    try:
        has = await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 1})
        if not has:
            out["error"] = "Nessuna chiave Bybit collegata"
        else:
            resp = await _bybit_signed("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            if resp.get("retCode") != 0:
                out["error"] = str(resp.get("retMsg"))
            else:
                acc = ((resp.get("result") or {}).get("list") or [{}])[0]
                coins = []
                for c in acc.get("coin") or []:
                    try:
                        qty, usd = float(c.get("walletBalance") or 0), float(c.get("usdValue") or 0)
                    except (TypeError, ValueError):
                        continue
                    if qty > 0 and usd >= 0.01:
                        coins.append({"coin": c.get("coin"), "qty": qty, "usd": round(usd, 2)})
                coins.sort(key=lambda x: -x["usd"])
                tot = acc.get("totalEquity")
                out.update({"connected": True, "coins": coins[:12],
                            "total_usd": round(float(tot), 2) if tot not in (None, "") else round(sum(c["usd"] for c in coins), 2)})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
    _crus_real_cache.update({"at": time.time(), "val": out})
    return out


_crus_kr_cache: dict[str, Any] = {"at": 0.0, "val": None}
_KR_NAMES = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XXRP": "XRP", "XLTC": "LTC", "XXDG": "DOGE", "ZEUR": "EUR", "ZUSD": "USD", "ZGBP": "GBP"}


def kr_sign(secret_b64: str, path: str, nonce: int, postdata: str) -> str:
    """Firma Kraken (API spot): HMAC-SHA512 di percorso + SHA256(nonce + dati), con il segreto decodificato base64."""
    import base64, hashlib, hmac
    sha = hashlib.sha256((str(nonce) + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret_b64), path.encode() + sha, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


async def kr_private(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Chiamata firmata a Kraken. SOLO percorsi di lettura (Balance, TradeBalance, OpenOrders...): gli ordini non sono previsti."""
    from urllib.parse import urlencode
    if path.split("/")[-1] not in ("Balance", "TradeBalance", "OpenOrders", "ClosedOrders", "TradesHistory", "Ledgers"):
        raise RuntimeError("Percorso Kraken non consentito: solo lettura")
    doc = await db.exchange_creds.find_one({"_id": "kraken"}, {"_id": 0})
    if not doc:
        raise RuntimeError("Nessuna chiave Kraken collegata")
    nonce = time.time_ns() // 1000
    post = urlencode({"nonce": nonce, **(params or {})})
    headers = {"API-Key": decrypt_str(doc["api_key"]), "API-Sign": kr_sign(decrypt_str(doc["api_secret"]), path, nonce, post),
               "Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post("https://api.kraken.com" + path, content=post, headers=headers)
    return r.json()


def kr_error_text(errs: list[str]) -> str:
    t = "; ".join(errs)
    if "Permission denied" in t:
        return "Manca un permesso sulla chiave (serve 'Query' fra i fondi). " + t
    if "Invalid key" in t or "Invalid signature" in t:
        return "Chiave o segreto non corretti. " + t
    return t


async def crus_kraken() -> dict[str, Any]:
    """Saldi Kraken (spot), SOLA LETTURA. Cache 30 secondi."""
    if _crus_kr_cache["val"] is not None and time.time() - _crus_kr_cache["at"] < 30:
        return _crus_kr_cache["val"]
    out: dict[str, Any] = {"connected": False, "error": None, "total_eur": None, "coins": [], "masked": None}
    try:
        doc = await db.exchange_creds.find_one({"_id": "kraken"}, {"_id": 0, "api_key_masked": 1})
        if not doc:
            out["error"] = "Nessuna chiave Kraken collegata"
        else:
            out["masked"] = doc.get("api_key_masked")
            b = await kr_private("/0/private/Balance")
            if b.get("error"):
                out["error"] = kr_error_text(b["error"])
            else:
                coins = []
                for k, v in (b.get("result") or {}).items():
                    try:
                        q = float(v)
                    except (TypeError, ValueError):
                        continue
                    if q > 0:
                        coins.append({"coin": _KR_NAMES.get(k, k), "qty": q})
                tb = await kr_private("/0/private/TradeBalance", {"asset": "ZEUR"})
                tot = None
                if not tb.get("error"):
                    try:
                        tot = round(float((tb.get("result") or {}).get("eb")), 2)
                    except (TypeError, ValueError):
                        tot = None
                out.update({"connected": True, "coins": coins[:15], "total_eur": tot})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
    _crus_kr_cache.update({"at": time.time(), "val": out})
    return out


@api.post("/kraken/connect")
async def kraken_connect(body: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("ENCRYPTION_SECRET", "").strip():
        raise HTTPException(status_code=400, detail="Prima imposta la variabile ENCRYPTION_SECRET su Railway.")
    key, sec = str(body.get("api_key") or "").strip(), str(body.get("api_secret") or "").strip()
    if not key or not sec:
        raise HTTPException(status_code=400, detail="Servono chiave e segreto")
    try:
        import base64
        base64.b64decode(sec, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Il segreto non sembra corretto: deve essere copiato per intero.")
    await db.exchange_creds.update_one({"_id": "kraken"}, {"$set": {
        "api_key": encrypt_str(key), "api_secret": encrypt_str(sec),
        "api_key_masked": (key[:4] + "…" + key[-4:]) if len(key) > 8 else "***",
        "connected_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    _crus_kr_cache.update({"at": 0.0, "val": None})
    res = await crus_kraken()
    if not res["connected"]:
        await db.exchange_creds.delete_one({"_id": "kraken"})
        _crus_kr_cache.update({"at": 0.0, "val": None})
        raise HTTPException(status_code=400, detail="Collegamento non riuscito: " + str(res.get("error")))
    return res


@api.post("/kraken/disconnect")
async def kraken_disconnect() -> dict[str, Any]:
    await db.exchange_creds.delete_one({"_id": "kraken"})
    _crus_kr_cache.update({"at": 0.0, "val": None})
    return {"ok": True}


_kr_cache: dict[str, Any] = {"tickers": (0.0, {}), "candles": {}}
_KR_BASE = {"BTC": "XBT"}


async def kr_pub(path: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Dati PUBBLICI dei future Kraken (nessuna chiave). None se non risponde."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://futures.kraken.com" + path, params=params)
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception:  # noqa: BLE001
        return None


async def kr_tickers() -> dict[str, dict[str, Any]]:
    at, cur = _kr_cache["tickers"]
    if cur and time.time() - at < 4:
        return cur
    j = await kr_pub("/derivatives/api/v3/tickers")
    if j and j.get("result") == "success":
        t = {x["symbol"]: x for x in j.get("tickers", []) if x.get("symbol") and x.get("tag") == "perpetual" and not x.get("suspended")}
        if t:
            _kr_cache["tickers"] = (time.time(), t)
            return t
    return cur if cur and time.time() - at < 120 else {}


def kr_symbol(bybit_symbol: str, tickers: dict[str, dict[str, Any]]) -> Optional[str]:
    """BTCUSDC -> PF_XBTUSD (se esiste fra i perpetui di Kraken)."""
    if not bybit_symbol.endswith("USDC"):
        return None
    base = bybit_symbol[:-4]
    for b in (_KR_BASE.get(base, base), base):
        sym = "PF_%sUSD" % b
        if sym in tickers:
            return sym
    return None


async def kr_price(bybit_symbol: str) -> Optional[float]:
    t = await kr_tickers()
    sym = kr_symbol(bybit_symbol, t)
    if not sym:
        return None
    x = t[sym]
    for k in ("last", "markPrice"):
        try:
            v = float(x.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


async def kr_book(bybit_symbol: str) -> Optional[tuple[list[list[float]], list[list[float]]]]:
    t = await kr_tickers()
    sym = kr_symbol(bybit_symbol, t)
    if not sym:
        return None
    j = await kr_pub("/derivatives/api/v3/orderbook", {"symbol": sym})
    try:
        ob = (j or {}).get("orderBook") or {}
        # l'ordine delle righe non e' garantito: ordino io (offerte dal prezzo piu' alto, richieste dal piu' basso)
        bids = sorted(([float(r[0]), float(r[1])] for r in ob.get("bids", [])), key=lambda r: -r[0])[:50]
        asks = sorted(([float(r[0]), float(r[1])] for r in ob.get("asks", [])), key=lambda r: r[0])[:50]
        if not bids or not asks or bids[0][0] <= 0 or asks[0][0] < bids[0][0]:
            return None
        if (asks[0][0] / bids[0][0] - 1.0) * 100.0 > 1.0:       # scarto oltre l'1%: il libro non e' affidabile, si usa Bybit
            return None
        return bids, asks
    except (TypeError, ValueError, IndexError):
        return None


async def kr_candles(bybit_symbol: str, tf: str) -> list[list[float]]:
    """Candele dei future Kraken nel formato del bot [tempo_s, apertura, chiusura, massimo, minimo, volume], senza l'ultima ancora in formazione."""
    if tf not in ("15m", "1h", "4h", "1d"):
        return []
    t = await kr_tickers()
    sym = kr_symbol(bybit_symbol, t)
    if not sym:
        return []
    key = sym + "|" + tf
    hit = _kr_cache["candles"].get(key)
    if hit and time.time() - hit[0] < (60 if tf in ("15m", "1h") else 180):
        return hit[1]
    j = await kr_pub("/api/charts/v1/trade/%s/%s" % (sym, tf), {"count": 301})
    out: list[list[float]] = []
    try:
        for c in (j or {}).get("candles", []):
            out.append([float(c["time"]) / 1000.0, float(c["open"]), float(c["close"]), float(c["high"]), float(c["low"]), float(c.get("volume") or 0)])
    except (TypeError, ValueError, KeyError):
        out = []
    out.sort(key=lambda r: r[0])
    if len(out) > 1:
        out = out[:-1]
    out = out[-300:]
    if len(out) >= 50:
        _kr_cache["candles"][key] = (time.time(), out)
        return out
    return hit[1] if hit and time.time() - hit[0] < 1800 else []


async def _desk_fill(symbol: str, side: str, ref_price: float, cfg: Any, *, quote_budget: Optional[float] = None, base_qty: Optional[float] = None) -> tuple[float, dict[str, Any]]:
    """Prezzo a cui verrebbe eseguito un ordine a mercato in Analisi e rischio: libro di Kraken se il modo e' Kraken e la moneta c'e' li', altrimenti Bybit."""
    if _crus_cost["mode"] == "kraken" and cfg.sim_realistic_fills:
        book = await kr_book(symbol)
        if book:
            bids, asks = book
            avg = walk_book(asks, quote_budget=quote_budget) if side == "buy" else walk_book(bids, base_qty=base_qty)
            if avg:
                spread = (asks[0][0] / bids[0][0] - 1.0) * 100.0
                cost = (avg / ref_price - 1.0) * 100.0 if side == "buy" else (1.0 - avg / ref_price) * 100.0
                return avg, {"fill_model": "book", "source": "kraken", "spread_pct": round(spread, 4), "cost_pct": round(cost, 4)}
    return await simulate_fill(symbol, side, ref_price, cfg, quote_budget=quote_budget, base_qty=base_qty)


@api.get("/cruscotto/confronto")
async def cruscotto_confronto() -> dict[str, Any]:
    """Prezzi di Bybit e di Kraken a confronto, per controllare che i dati corrispondano a quello che vedi sui due siti."""
    dc = await _desk_cfg()
    t = await kr_tickers()
    rows = []
    for sym in list(dc["symbols"])[:8]:
        try:
            bp = price_feed.get(sym) or await price_feed.price_or_rest(sym)
        except Exception:  # noqa: BLE001
            bp = None
        ks = kr_symbol(sym, t)
        x = t.get(ks) if ks else None
        row: dict[str, Any] = {"symbol": sym, "bybit": float(bp) if bp else None, "kraken_symbol": ks, "kraken": None, "diff_pct": None,
                               "kraken_spread_pct": None, "funding_day_pct": None}
        if x:
            try:
                row["kraken"] = float(x.get("last") or x.get("markPrice"))
                if x.get("bid") and x.get("ask"):
                    row["kraken_spread_pct"] = round((float(x["ask"]) / float(x["bid"]) - 1.0) * 100.0, 4)
                if x.get("relativeFundingRate") is not None:
                    row["funding_day_pct"] = round(float(x["relativeFundingRate"]) * 24.0 * 100.0, 4)
                if row["bybit"] and row["kraken"]:
                    row["diff_pct"] = round((row["kraken"] / row["bybit"] - 1.0) * 100.0, 3)
            except (TypeError, ValueError):
                pass
        rows.append(row)
    return {"kraken_ok": bool(t), "rows": rows,
            "note": "Finanziamento: stima ricavata dal tasso relativo di Kraken per 24 ore. Positivo = i long pagano e gli short incassano (da verificare sul sito)."}


_crus_cost: dict[str, Any] = {"mode": "bybit", "fee": None}
KRAKEN_PERP_FEE_SIDE = 0.05      # % per lato: taker dei future Kraken (maker 0,02%): scelta prudente, tutti gli ordini a mercato


def _desk_fee(cfg: Any) -> float:
    """Commissione per lato usata da Analisi e rischio: quella di Bybit spot (impostazioni del bot) oppure quella dei future Kraken."""
    f = _crus_cost.get("fee")
    return float(f) if f is not None else float(cfg.sim_fee_pct)


async def crus_cost_load() -> None:
    doc = await db.crus_config.find_one({"_id": "cost"}, {"_id": 0}) or {}
    mode = doc.get("mode") if doc.get("mode") in ("bybit", "kraken") else "bybit"
    _crus_cost.update({"mode": mode, "fee": KRAKEN_PERP_FEE_SIDE if mode == "kraken" else None})
    rr = (await db.crus_config.find_one({"_id": "rr"}, {"_id": 0}) or {}).get("rr")
    _crus_rr["rr"] = float(rr) if rr else None


_crus_rr: dict[str, Any] = {"rr": None}      # None = automatico (come prima: obiettivo a 4 ATR, rendimento/rischio fra 1,6 e 2,7 circa)


def _desk_risk_plan(side: str, price: float, candles_1h: list[list[float]], atr_val: float, **kw: Any) -> dict[str, Any]:
    """Piano di Analisi e rischio; se hai scelto un rendimento/rischio (RR) fisso, l'obiettivo si mette a RR volte la distanza dello stop."""
    pl = DK.risk_plan(side, price, candles_1h, atr_val, **kw)
    rr = _crus_rr.get("rr")
    if rr and pl.get("risk_pct_price"):
        risk_frac = pl["risk_pct_price"] / 100.0
        gain_frac = float(rr) * risk_frac
        pl["target"] = price * (1.0 + gain_frac) if side == "long" else price * (1.0 - gain_frac)
        pl["gain_pct_price"] = gain_frac * 100.0
        pl["rr"] = float(rr)
        pl["expected_gain"] = pl["notional"] * (gain_frac - pl["cost_pct"] / 100.0)
    return pl


@api.post("/cruscotto/impostazioni")
async def cruscotto_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Capitale del portafoglio simulato e rendimento/rischio (RR) delle nuove analisi."""
    out: dict[str, Any] = {}
    if "rr" in body:
        try:
            v = float(body["rr"] or 0.0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="RR non valido")
        if v != 0.0 and not (0.8 <= v <= 6.0):
            raise HTTPException(status_code=400, detail="RR tra 0,8 e 6 (oppure automatico).")
        await db.crus_config.update_one({"_id": "rr"}, {"$set": {"rr": v if v else None}}, upsert=True)
        await crus_cost_load()
        out["rr"] = _crus_rr["rr"]
    if "capital" in body:
        try:
            c = float(body["capital"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Capitale non valido")
        await desk_set_config({"capital": c})                  # controlla i limiti, rifiuta se ci sono posizioni aperte e rimette il contante a c
        await db.desk_equity.delete_many({})                    # la curva riparte dal nuovo capitale
        await crus_snapshot(force=True)
        out["capital"] = c
    return {"ok": True, **out}


@api.post("/cruscotto/costs")
async def cruscotto_set_costs(body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or "")
    if mode not in ("bybit", "kraken"):
        raise HTTPException(status_code=400, detail="Scelta non valida")
    if await db.desk_positions.find_one({"status": "open"}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Chiudi prima le posizioni aperte: cambiare i costi a meta' falserebbe i risultati.")
    if mode == "kraken" and not await kr_tickers():
        raise HTTPException(status_code=400, detail="Kraken non risponde adesso: resto su Bybit. Riprova fra poco.")
    await db.crus_config.update_one({"_id": "cost"}, {"$set": {"mode": mode}}, upsert=True)
    await crus_cost_load()
    return {"ok": True, "mode": mode}


def _crus_hold_h(a: Any, b: Any) -> Optional[float]:
    try:
        return round((datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds() / 3600.0, 2)
    except (TypeError, ValueError):
        return None


def crus_report_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    st = crus_stats(rows)
    chron = sorted(rows, key=lambda r: str(r.get("closed_at") or ""))
    run = mx = 0
    for r in chron:
        if float(r.get("pnl_usdt") or 0.0) < 0:
            run += 1
            mx = max(mx, run)
        else:
            run = 0
    holds = [h for h in (_crus_hold_h(r.get("opened_at"), r.get("closed_at")) for r in rows) if h is not None]
    st["max_loss_streak"] = mx
    st["avg_hold_h"] = round(sum(holds) / len(holds), 2) if holds else None
    st["carry_total"] = round(sum(float(r.get("carry_usdt") or 0.0) for r in rows), 4)
    st["auto_n"] = len([r for r in rows if r.get("auto")])
    return st


@api.get("/cruscotto/report")
async def cruscotto_report(side: str = "", esito: str = "", symbol: str = "", page: int = 0, per: int = 30) -> dict[str, Any]:
    """Report completo di tutte le operazioni chiuse di Analisi e rischio, con filtri e pagine."""
    docs = await db.desk_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(5000)
    rows = [d for d in docs
            if (side not in ("long", "short") or d.get("side") == side)
            and (esito != "vinte" or float(d.get("pnl_usdt") or 0.0) > 0)
            and (esito != "perse" or float(d.get("pnl_usdt") or 0.0) <= 0)
            and (not symbol or d.get("symbol") == symbol)]
    per = max(5, min(int(per), 100))
    page = max(0, int(page))
    by_sym: dict[str, list[dict[str, Any]]] = {}
    by_rsn: dict[str, list[dict[str, Any]]] = {}
    by_day: dict[str, float] = {}
    for r in rows:
        by_sym.setdefault(str(r.get("symbol")), []).append(r)
        by_rsn.setdefault(str(r.get("close_reason") or "-"), []).append(r)
        day = str(r.get("closed_at") or "")[:10]
        by_day[day] = by_day.get(day, 0.0) + float(r.get("pnl_usdt") or 0.0)
    def agg(g: dict[str, list[dict[str, Any]]], key: str) -> list[dict[str, Any]]:
        out = []
        for k, v in g.items():
            p = crus_stats(v)
            out.append({key: k, "n": p["n"], "win_rate": p["win_rate"], "pnl": p["pnl"]})
        return sorted(out, key=lambda x: -x["pnl"])
    trades = []
    for r in rows[page * per:(page + 1) * per]:
        pnl = float(r.get("pnl_usdt") or 0.0)
        ml = float(r.get("max_loss") or 0.0)
        mg = float(r.get("margin") or 0.0)
        trades.append({"id": r.get("id"), "symbol": r.get("symbol"), "side": r.get("side"), "leverage": r.get("leverage"), "auto": bool(r.get("auto")),
                       "opened_at": r.get("opened_at"), "closed_at": r.get("closed_at"), "hold_h": _crus_hold_h(r.get("opened_at"), r.get("closed_at")),
                       "entry": r.get("fill_price"), "exit": r.get("close_price"), "stop": r.get("stop"), "target": r.get("target"),
                       "notional": r.get("notional"), "margin": r.get("margin"), "pnl": round(pnl, 4),
                       "pnl_pct_margin": round(pnl / mg * 100.0, 2) if mg > 0 else None, "r": round(pnl / ml, 2) if ml > 0 else None,
                       "reason": r.get("close_reason"), "carry": r.get("carry_usdt")})
    symbols = sorted({str(d.get("symbol")) for d in docs})
    return {"total": len(rows), "page": page, "per": per, "symbols": symbols, "summary": crus_report_summary(rows) if rows else None,
            "by_symbol": agg(by_sym, "symbol"), "by_reason": agg(by_rsn, "reason"),
            "by_day": [{"day": d, "pnl": round(v, 4)} for d, v in sorted(by_day.items())[-14:]], "trades": trades, "gestione": await mng_summary()}


@api.get("/cruscotto")
async def cruscotto() -> dict[str, Any]:
    st = await desk_status()
    dc = st["config"]
    await crus_snapshot()
    curve_docs = await db.desk_equity.find({}, {"_id": 0, "t": 1, "equity": 1}).sort("t", -1).to_list(600)
    curve_docs.reverse()
    if len(curve_docs) > 120:                                   # al massimo 120 punti da disegnare
        step = len(curve_docs) / 120.0
        curve_docs = [curve_docs[int(i * step)] for i in range(120)] + [curve_docs[-1]]
    curve = [[round(c["t"]), round(c["equity"], 4)] for c in curve_docs]
    cap0 = float(dc["capital"])
    allc = await db.desk_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(2000)
    stats = crus_stats(allc)
    longs = [o for o in st["open"] if o["side"] == "long"]
    shorts = [o for o in st["open"] if o["side"] == "short"]
    eq = float(st["equity"])
    gross_l = sum(o["notional"] for o in longs)
    gross_s = sum(o["notional"] for o in shorts)
    margins = sum(o["margin"] for o in st["open"])
    floating = sum(float(o.get("floating_pnl") or 0.0) for o in st["open"])
    positions = []
    for o in st["open"]:
        v = crus_position_view(o, float(o["current_price"]))
        positions.append({"id": o["id"], "symbol": o["symbol"], "side": o["side"], "leverage": o["leverage"], "auto": bool(o.get("auto")),
                          "fill_price": o["fill_price"], "current_price": o["current_price"], "stop": o["stop"], "target": o["target"],
                          "liquidation": o.get("liquidation"), "notional": o["notional"], "margin": o["margin"], "pnl": o["floating_pnl"],
                          "lots_left": o.get("lots_left"), "lots_total": o.get("lots_total"), "tp1_done": bool(o.get("tp1_done")),
                          "opened_ts": o.get("opened_ts"), "mng": mng_view(o), **v})
    risk = st["risk"]
    term: dict[str, Any] = {"installed": False}
    try:
        if "_term_cfg" in globals():
            tc = await _term_cfg()
            lab = await db.lab_results.find_one({"_id": "lab"}, {"_id": 0, "rows": 1, "at": 1})
            nq = None
            if lab:
                nq = len([r for r in lab.get("rows", []) if LAB.qualifies(r, tc["rigor"])])
            setups = await db.term_setups.find({}, {"_id": 0}).sort("created_ts", -1).to_list(3)
            term = {"installed": True, "auto": bool(tc["auto_enabled"]), "rigor": tc["rigor"], "qualified": nq,
                    "tested": len(lab.get("rows", [])) if lab else None, "archive_at": lab.get("at") if lab else None,
                    "last_setups": [{"symbol": s.get("symbol"), "side": s.get("side")} for s in setups]}
    except Exception as e:  # noqa: BLE001
        term = {"installed": True, "error": str(e)[:120]}
    scan = st["scan"]
    return {
        "portfolio": {"capital0": cap0, "equity": eq, "cash": st["cash"], "margin": round(margins, 4), "floating": round(floating, 4),
                      "realized": st["realized"], "return_pct": round((eq - cap0) / cap0 * 100.0, 2) if cap0 else None,
                      "today_pnl": risk["today_pnl"], "max_drawdown_pct": crus_drawdown([c[1] for c in curve]),
                      "curve": curve},
        "exposure": {"long": round(gross_l, 2), "short": round(gross_s, 2), "net": round(gross_l - gross_s, 2),
                     "gross": round(gross_l + gross_s, 2), "gross_x_equity": round((gross_l + gross_s) / eq, 2) if eq > 0 else None,
                     "n_long": len(longs), "n_short": len(shorts)},
        "risk": {**risk, "max_open": dc["max_open"], "open_n": len(st["open"]), "max_trades_day": dc["max_trades_day"],
                 "daily_loss_pct": dc["daily_loss_pct"], "max_total_risk_pct": dc["max_total_risk_pct"]},
        "stats": stats, "positions": positions, "recent_closed": [
            {"symbol": c["symbol"], "side": c["side"], "pnl": c.get("pnl_usdt"), "reason": c.get("close_reason"), "closed_at": c.get("closed_at")}
            for c in allc[:6]],
        "bots": {"desk_auto": bool(dc["auto_enabled"]), "scan_enabled": bool(dc["scan_enabled"]), "scan_at": scan.get("at"),
                 "scan_top": [r.get("symbol") for r in (scan.get("rows") or [])[:5]], "terminal": term},
        "events": st["events"][:8],
        "real": await crus_real(),
        "settings": {"capital": cap0, "rr": _crus_rr["rr"], "can_change_capital": len(st["open"]) == 0},
        "kraken": await crus_kraken(),
        "costs": {"mode": _crus_cost["mode"], "fee_side_pct": round(_desk_fee(await get_config()), 4),
                  "round_trip_pct": round(2 * _desk_fee(await get_config()), 4)},
    }


async def crus_loop() -> None:
    await asyncio.sleep(3)
    while True:
        try:
            await crus_cost_load()
            await crus_snapshot()
        except Exception as e:  # noqa: BLE001
            logger.exception("Cruscotto: errore nel salvataggio della curva: %s", e)
        await asyncio.sleep(120)


# ============================================================================
# TERMINALE — schermata a coppie (grafico, volume, RSI, libro ordini) con il tasto ANALIZZA.
# Un ARCHIVIO di strategie note viene provato sui prezzi passati CON i tuoi costi (1 volta al giorno).
# Solo le strategie con un vantaggio statistico sufficiente (e positive anche nell'ultimo terzo del periodo) possono dare un setup.
# Analizza dice: LONG da..a con stop e obiettivo, SHORT (solo simulazione), oppure PASSA. Poi un secondo tocco apre in SIMULAZIONE.
# Modo automatico: analizza e apre da solo, con gli stessi limiti di rischio di "Analisi e rischio". NESSUN ordine reale.
# Le regole stanno in lab_logic.py.
# ============================================================================
try:
    import lab_logic as LAB
except ImportError:  # pragma: no cover
    from backend import lab_logic as LAB

_TERM_STABLES = {"USDT", "USDC", "USDE", "DAI", "FDUSD", "TUSD", "PYUSD", "EUR", "EURC", "USD1", "USDD", "BUSD"}
_lab: dict[str, Any] = {"running": False, "res": None, "err": None}


async def _term_cfg() -> dict[str, Any]:
    doc = await db.term_config.find_one({"_id": "term"}, {"_id": 0}) or {}
    out = dict(LAB.DEFAULTS)
    out.update({k: v for k, v in doc.items() if k in LAB.DEFAULTS})
    return out


async def _term_pairs_raw(min_volume: float = 20000.0) -> list[dict[str, Any]]:
    rows = []
    for t in await exchange.get_tickers():
        s = str(t.get("symbol") or "")
        base = s[:-4]
        if not s.endswith("USDC") or base in _TERM_STABLES or any(base.endswith(x) for x in ("UP", "DOWN", "3L", "3S")):
            continue
        try:
            rows.append({"symbol": s, "last": float(t.get("last") or 0), "change": float(t.get("changeRate") or 0) * 100.0,
                         "turnover": float(t.get("volValue") or 0)})
        except (TypeError, ValueError):
            continue
    rows = [r for r in rows if r["turnover"] >= min_volume and r["last"] > 0]
    rows.sort(key=lambda r: -r["turnover"])
    return rows


async def _term_syms(tc: dict[str, Any]) -> list[str]:
    syms = list(tc["symbols"])
    for r in await _term_pairs_raw():
        if len(syms) >= int(tc["max_universe"]):
            break
        if r["symbol"] not in syms:
            syms.append(r["symbol"])
    return syms


async def _lab_history(symbol: str, tf: str, years: float) -> list[list[float]]:
    """Storia a pagine da Bybit (1000 candele per chiamata, dalla piu' recente indietro). Formato del bot."""
    need = int(years * 365 * 86400 / LAB.SECS[tf])
    out: dict[int, list[float]] = {}
    end = None
    for _ in range(40):
        params: dict[str, Any] = {"category": exchange.category, "symbol": symbol, "interval": TF_MAP[tf], "limit": 1000}
        if end:
            params["end"] = end
        data = await exchange._get("/v5/market/kline", params=params)
        if not data or data.get("retCode") != 0:
            break
        lst = data.get("result", {}).get("list", [])
        if not lst:
            break
        for row in lst:
            try:
                out[int(row[0]) // 1000] = [float(row[0]) / 1000.0, float(row[1]), float(row[4]), float(row[2]), float(row[3]), float(row[5])]
            except (ValueError, IndexError):
                continue
        if len(lst) < 1000 or len(out) >= need:
            break
        end = int(lst[-1][0]) - 1
        await asyncio.sleep(0.1)
    cs = [out[k] for k in sorted(out)]
    return cs[:-1] if len(cs) > 1 else cs         # via la candela ancora aperta


async def lab_run() -> None:
    if _lab["running"]:
        return
    _lab["running"], _lab["err"] = True, None
    try:
        tc, cfg = await _term_cfg(), await get_config()
        data: dict[tuple[str, str], list[tuple]] = {}
        for s in await _term_syms(tc):
            for tf in LAB.TFS:
                cs = await _lab_history(s, tf, float(tc["lab_years"]))
                if len(cs) >= 300:
                    data[(s, tf)] = LAB.to_rows(cs)
        if not data:
            raise RuntimeError("nessun dato storico disponibile da Bybit")
        fee = float(cfg.sim_fee_pct)
        res = await asyncio.to_thread(LAB.run_lab, data, fee)
        res["fee_side"] = fee
        res["at"] = datetime.now(timezone.utc).isoformat()
        _lab["res"] = res
        await db.lab_results.update_one({"_id": "lab"}, {"$set": res}, upsert=True)
        await _desk_event("-", "Terminale: archivio strategie aggiornato (%d prove, %d monete)" % (len(res["rows"]), len(res["symbols"])))
    except Exception as e:  # noqa: BLE001
        logger.exception("Terminale: errore nel calcolo dell'archivio: %s", e)
        _lab["err"] = str(e)
    finally:
        _lab["running"] = False


async def _lab_get() -> Optional[dict[str, Any]]:
    if _lab["res"] is None:
        doc = await db.lab_results.find_one({"_id": "lab"}, {"_id": 0})
        if doc:
            _lab["res"] = doc
    return _lab["res"]


def _lab_qualified(res: dict[str, Any], rigor: str) -> list[dict[str, Any]]:
    return [r for r in res["rows"] if LAB.qualifies(r, rigor)]


async def _term_plan(symbol: str, side: str, price: float, stop: float, target: float, dc: dict[str, Any], cfg: Config, risk_pct: float) -> dict[str, Any]:
    s = await precheck_settings()
    pl = LAB.plan_custom(side, price, stop, target, capital=float(dc["capital"]), risk_pct=risk_pct, fee_pct=cfg.sim_fee_pct,
                         funding_day_pct=float(dc["funding_day_pct"]), max_loss_usdc=s["max_loss_usdc"] if s["check_max_loss"] else None)
    _, info = await simulate_fill(symbol, "buy" if side == "long" else "sell", price, cfg,
                                  quote_budget=pl["notional"] if side == "long" else None,
                                  base_qty=None if side == "long" else pl["notional"] / price)
    st_m, tg_m = DK.mirror_for_check(pl)
    ev = PEC.evaluate(price=price, notional=pl["notional"], fee_pct=cfg.sim_fee_pct, stop=st_m, target=tg_m,
                      book_cost_pct=abs(float(info.get("cost_pct") or 0.0)), spread_pct=info.get("spread_pct"),
                      book_model=str(info.get("fill_model")), s=s, use_max_loss=False)
    pl["checks"] = {"ok": ev["ok"], "reasons": ev["reasons"]}
    return pl


def _term_explain(q: dict[str, Any]) -> str:
    t = "%s su grafico %s. %s" % (q["name"], q["tf"], q["text"])
    t += " Nel passato, su %d operazioni in %d monete, ha reso in media %+.2fR a operazione DOPO i costi (t=%.1f, vinte %.0f%%)." % (
        q["n"], q["coins"], q["net"], q["t"], q["win"])
    t += " E' positiva in entrambe le meta' del periodo (%+.2fR e %+.2fR) e anche nell'ultimo terzo (%+.2fR)." % (q["h1"], q["h2"], q.get("oos") or 0.0)
    return t


async def _term_analyze(symbol: str, tc: dict[str, Any], cfg: Config, risk_pct: Optional[float] = None) -> dict[str, Any]:
    dc = await _desk_cfg()
    price = await _desk_price(symbol)
    if price is None:
        raise HTTPException(status_code=400, detail="Prezzo non disponibile per " + symbol)
    now = datetime.now(timezone.utc)
    base: dict[str, Any] = {"symbol": symbol, "price": price, "at": now.isoformat(), "rigor": tc["rigor"], "verdict": "PASSA", "reasons": []}
    res = await _lab_get()
    if not res:
        base["reasons"] = ["L'archivio delle strategie non e' ancora pronto: il primo calcolo richiede qualche minuto."]
        base["lab"] = {"tested": 0, "qualified": 0}
        return base
    qualified = _lab_qualified(res, tc["rigor"])
    base["lab"] = {"tested": len([r for r in res["rows"] if r.get("n", 0) >= 10]), "qualified": len(qualified), "at": res.get("at")}
    if not qualified:
        base["reasons"] = ["Nessuna strategia dell'archivio ha un vantaggio statistico sufficiente con i tuoi costi (%d provate). Meglio non entrare." % base["lab"]["tested"]]
        return base
    frames = {tf: await _desk_candles(symbol, tf) for tf in LAB.TFS}
    sigs = LAB.live_signals(frames, qualified, price)
    best, why = LAB.choose(sigs, float(cfg.sim_fee_pct))
    if not best:
        names = ", ".join("%s (%s, %s)" % (q["name"], q["tf"], q["side"]) for q in sorted(qualified, key=lambda x: -x["t"])[:3])
        base["reasons"] = ["%d strategie qualificate, ma nessuna ha un segnale valido adesso su %s. Qualificate: %s." % (len(qualified), symbol, names)] + why
        return base
    q, side = best["q"], ("long" if best["side"] == 1 else "short")
    a = best["atr"]
    rp = float(risk_pct if risk_pct is not None else dc["risk_pct"])
    pl = await _term_plan(symbol, side, price, best["stop"], best["target"], dc, cfg, rp)
    setup = {"id": str(uuid.uuid4()), "symbol": symbol, "side": side, "entry": price, "stop": best["stop"], "target": best["target"],
             "atr": a, "strategy": q["id"], "strategy_name": q["name"], "tf": q["tf"], "created_ts": now.timestamp()}
    await db.term_setups.insert_one(dict(setup))
    await db.term_setups.delete_many({"created_ts": {"$lt": now.timestamp() - 3600}})
    base.update({"verdict": side.upper(), "setup_id": setup["id"], "zone_from": price - 0.1 * a, "zone_to": price + 0.1 * a,
                 "stop": best["stop"], "target": best["target"], "plan": pl, "simulation_only": side == "short",
                 "strategy": {k: q.get(k) for k in ("id", "name", "family", "tf", "side", "rr", "n", "coins", "net", "gross", "t", "win", "h1", "h2", "oos")},
                 "explain": _term_explain(q), "reasons": why})
    return base


async def _term_open_core(setup: dict[str, Any], dc: dict[str, Any], cfg: Config, auto: bool, risk_pct: float) -> dict[str, Any]:
    async with _desk_lock:
        symbol, side = setup["symbol"], setup["side"]
        price = await _desk_price(symbol)
        if price is None:
            raise HTTPException(status_code=400, detail="Prezzo non disponibile")
        why: list[str] = []
        if abs(price - setup["entry"]) > 0.5 * setup["atr"]:
            why.append("il prezzo si e' mosso troppo da quando hai analizzato: rianalizza")
        stop, target = float(setup["stop"]), float(setup["target"])
        if (side == "long" and not (stop < price < target)) or (side == "short" and not (target < price < stop)):
            why.append("il prezzo e' gia' fuori da stop/obiettivo del setup")
        if why:
            raise HTTPException(status_code=400, detail="NON apro: " + "; ".join(why))
        pl = await _term_plan(symbol, side, price, stop, target, dc, cfg, risk_pct)
        why += pl["checks"]["reasons"]
        if pl["notional"] < 5.5:
            why.append("importo %.2f USDC sotto il minimo di 5,5" % pl["notional"])
        opens = await db.desk_positions.find({"status": "open"}, {"symbol": 1, "_id": 0}).to_list(50)
        if len(opens) >= int(dc["max_open"]):
            why.append("hai gia' %d posizioni aperte" % len(opens))
        if any(o["symbol"] == symbol for o in opens):
            why.append("c'e' gia' una posizione su " + symbol)
        w = await _desk_wallet()
        if pl["margin"] > w["cash"]:
            why.append("margine %.2f USDC piu' del contante libero %.2f" % (pl["margin"], w["cash"]))
        stt = await _desk_day_stats(dc)
        why += DK.risk_gate(capital=float(dc["capital"]), open_risks=stt["open_risks"], new_risk=pl["max_loss"],
                            max_total_risk_pct=float(dc["max_total_risk_pct"]), today_pnl=stt["today_pnl"], daily_loss_pct=float(dc["daily_loss_pct"]))
        if auto:
            if stt["opened_today"] >= int(dc["max_trades_day"]):
                why.append("gia' %d aperture automatiche oggi" % stt["opened_today"])
            sb = DK.streak_block([c.get("pnl_usdt", 0.0) for c in stt["closed"]], stt["since_last_close_h"], int(dc["streak_pause"]), float(dc["streak_pause_h"]))
            if sb:
                why.append(sb)
            last = next((c for c in stt["closed"] if c["symbol"] == symbol), None)
            if last and dc["cooldown_min"]:
                try:
                    mins = (datetime.now(timezone.utc) - datetime.fromisoformat(last["closed_at"])).total_seconds() / 60.0
                    if mins < float(dc["cooldown_min"]):
                        why.append("attesa dopo l'ultima chiusura su %s (ancora %d minuti)" % (symbol, float(dc["cooldown_min"]) - mins))
                except Exception:  # noqa: BLE001
                    pass
        if why:
            raise HTTPException(status_code=400, detail="NON apro: " + "; ".join(why))
        if side == "long":
            fill, info = await simulate_fill(symbol, "buy", price, cfg, quote_budget=pl["notional"])
        else:
            fill, info = await simulate_fill(symbol, "sell", price, cfg, base_qty=pl["notional"] / price)
        qty = pl["notional"] / fill
        lots = DK.lots_for(pl["notional"], int(dc["lots"]))
        now = datetime.now(timezone.utc)
        doc = {"id": str(uuid.uuid4()), "symbol": symbol, "status": "open", "side": side, "leverage": 1.0,
               "entry": price, "fill_price": fill, "fill_model": info.get("fill_model"), "quantity": qty, "qty0": qty,
               "notional": pl["notional"], "margin": pl["margin"], "stop": stop, "target": target,
               "tp1": DK.tp1_price(side, fill, stop, float(dc["tp1_r"])) if lots > 1 else None,
               "lots_total": lots, "lots_left": lots, "tp1_done": False, "realized_partial": 0.0,
               "liquidation": None, "max_loss": pl["max_loss"], "rr": pl["rr"], "bias_at_entry": "terminale",
               "forced": False, "auto": bool(auto), "funding_day_pct": float(dc["funding_day_pct"]),
               "source": "terminale", "strategy": setup.get("strategy"), "strategy_name": setup.get("strategy_name"),
               "opened_at": now.isoformat(), "opened_ts": now.timestamp()}
        await db.desk_positions.insert_one(dict(doc))
        await db.desk_wallet.update_one({"_id": "w"}, {"$inc": {"cash": -pl["margin"]}}, upsert=True)
        await _desk_event(symbol, "%s %s a %.6g [%s] (importo %.2f in %d lotti, stop %.6g, obiettivo %.6g, perdita max %.2f)" % (
            "APERTA DAL TERMINALE (auto)" if auto else "APERTA DAL TERMINALE", side, fill, setup.get("strategy_name"), pl["notional"], lots, stop, target, pl["max_loss"]))
    doc.pop("_id", None)
    return doc


async def term_auto_pass() -> None:
    tc = await _term_cfg()
    if not tc["auto_enabled"] or not await _lab_get():
        return
    dc, cfg = await _desk_cfg(), await get_config()
    for sym in tc["symbols"]:
        if await db.desk_positions.find_one({"symbol": sym, "status": "open"}, {"_id": 1}):
            continue
        try:
            an = await _term_analyze(sym, tc, cfg, risk_pct=float(tc["auto_risk_pct"]))
            if an["verdict"] not in ("LONG", "SHORT"):
                continue
            setup = await db.term_setups.find_one({"id": an["setup_id"]}, {"_id": 0})
            await _term_open_core(setup, dc, cfg, True, float(tc["auto_risk_pct"]))
        except HTTPException as e:
            key = "term|" + sym
            st = await db.desk_state.find_one({"_id": key}) or {}
            sig = __import__("re").sub(r"\d+(\.\d+)?", "#", str(e.detail))
            if st.get("why") != sig or time.time() - float(st.get("ts", 0)) > 3600:
                await db.desk_state.update_one({"_id": key}, {"$set": {"why": sig, "ts": time.time()}}, upsert=True)
                await _desk_event(sym, "Terminale auto: setup trovato ma non apro - %s" % e.detail)
        except Exception as e:  # noqa: BLE001
            logger.warning("Terminale auto %s: %s", sym, e)


async def term_loop() -> None:
    last_auto = 0.0
    await asyncio.sleep(40)
    while True:
        try:
            tc = await _term_cfg()
            if tc["enabled"]:
                res = await _lab_get()
                try:
                    age = time.time() - datetime.fromisoformat(res["at"]).timestamp() if res else 1e12
                except Exception:  # noqa: BLE001
                    age = 1e12
                if not _lab["running"] and age > 86400:
                    asyncio.create_task(lab_run())
                if tc["auto_enabled"] and time.time() - last_auto > float(tc["auto_every_min"]) * 60:
                    last_auto = time.time()
                    await term_auto_pass()
        except Exception as e:  # noqa: BLE001
            logger.exception("Terminale: errore nel ciclo: %s", e)
        await asyncio.sleep(30)


@api.get("/terminal/pairs")
async def term_pairs() -> dict[str, Any]:
    tc = await _term_cfg()
    rows = await _term_pairs_raw()
    by = {r["symbol"]: r for r in rows}
    out = [by[s] for s in tc["symbols"] if s in by]
    for r in rows:
        if len(out) >= 24:
            break
        if r["symbol"] not in tc["symbols"]:
            out.append(r)
    return {"pairs": out}


@api.get("/terminal/chart")
async def term_chart(symbol: str, tf: str = "1h") -> dict[str, Any]:
    if tf not in TF_MAP:
        raise HTTPException(status_code=400, detail="Grafico non valido")
    cs = await exchange.get_klines(symbol, tf, limit=130)
    if not cs:
        raise HTTPException(status_code=400, detail="Dati non disponibili per " + symbol)
    rows = LAB.to_rows(cs)
    rsi_all = LAB.rsi_series(rows)
    show = 70
    candles = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows[-show:]]
    rsi = [None if x is None else round(x, 2) for x in rsi_all[-show:]]
    tk: dict[str, Any] = {}
    try:
        d = await exchange._get("/v5/market/tickers", params={"category": exchange.category, "symbol": symbol})
        it = (d or {}).get("result", {}).get("list", [{}])[0]
        tk = {"last": float(it.get("lastPrice") or 0), "change": float(it.get("price24hPcnt") or 0) * 100.0,
              "high": float(it.get("highPrice24h") or 0), "low": float(it.get("lowPrice24h") or 0), "turnover": float(it.get("turnover24h") or 0)}
    except Exception:  # noqa: BLE001
        pass
    book: dict[str, Any] = {"bids": [], "asks": []}
    try:
        d = await exchange._get("/v5/market/orderbook", params={"category": exchange.category, "symbol": symbol, "limit": 12})
        res = (d or {}).get("result", {})
        book = {"bids": [[float(p), float(q)] for p, q in res.get("b", [])[:12]], "asks": [[float(p), float(q)] for p, q in res.get("a", [])[:12]]}
    except Exception:  # noqa: BLE001
        pass
    pos = await db.desk_positions.find_one({"symbol": symbol, "status": "open"}, {"_id": 0, "side": 1, "fill_price": 1, "stop": 1, "target": 1})
    return {"symbol": symbol, "tf": tf, "candles": candles, "rsi": rsi, "ticker": tk, "book": book, "position": pos}


@api.post("/terminal/analyze")
async def term_analyze(body: dict[str, Any]) -> dict[str, Any]:
    symbol = str(body.get("symbol") or "")
    if not symbol:
        raise HTTPException(status_code=400, detail="Manca la coppia")
    return await _term_analyze(symbol, await _term_cfg(), await get_config())


@api.post("/terminal/open")
async def term_open(body: dict[str, Any]) -> dict[str, Any]:
    setup = await db.term_setups.find_one({"id": str(body.get("setup_id") or "")}, {"_id": 0})
    if not setup:
        raise HTTPException(status_code=400, detail="Setup non trovato: premi di nuovo Analizza")
    if time.time() - float(setup["created_ts"]) > 1200:
        raise HTTPException(status_code=400, detail="Setup scaduto (oltre 20 minuti): premi di nuovo Analizza")
    dc, cfg = await _desk_cfg(), await get_config()
    return {"ok": True, "position": await _term_open_core(setup, dc, cfg, False, float(dc["risk_pct"]))}


@api.get("/terminal/lab")
async def term_lab() -> dict[str, Any]:
    tc = await _term_cfg()
    res = await _lab_get()
    out: dict[str, Any] = {"running": _lab["running"], "error": _lab["err"], "rigor": tc["rigor"], "config": tc}
    if res:
        rows = [r for r in res["rows"] if r.get("n", 0) >= 10]
        for r in rows:
            r["qualified"] = LAB.qualifies(r, tc["rigor"])
        rows.sort(key=lambda r: -r.get("t", -99))
        out.update({"at": res.get("at"), "fee_side": res.get("fee_side"), "symbols": res.get("symbols"), "tested": len(rows),
                    "qualified": sum(1 for r in rows if r["qualified"]), "top": rows[:12],
                    "candles": res.get("candles"), "all_candidates": len(res["rows"])})
    return out


@api.post("/terminal/lab/refresh")
async def term_lab_refresh() -> dict[str, Any]:
    if not _lab["running"]:
        asyncio.create_task(lab_run())
    return {"ok": True, "running": True}


@api.post("/terminal/config")
async def term_set_config(body: dict[str, Any]) -> dict[str, Any]:
    cur = await _term_cfg()
    new: dict[str, Any] = {}
    for k, v in body.items():
        if k not in LAB.DEFAULTS:
            continue
        if k in ("enabled", "auto_enabled"):
            new[k] = bool(v)
        elif k == "rigor":
            if v not in LAB.RIGOR:
                raise HTTPException(status_code=400, detail="Rigore non valido")
            new[k] = v
        elif k == "symbols":
            syms = [str(x).upper().strip() for x in (v or []) if str(x).upper().strip().endswith("USDC")][:12]
            if not syms:
                raise HTTPException(status_code=400, detail="Serve almeno una coppia USDC")
            new[k] = syms
        else:
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Valore non valido per " + k)
            lo, hi = LAB.CFG_LIMITS[k]
            if not (lo <= x <= hi):
                raise HTTPException(status_code=400, detail="%s deve essere tra %g e %g" % (k, lo, hi))
            new[k] = int(x) if k in ("auto_every_min", "max_universe") else x
    cur.update(new)
    await db.term_config.update_one({"_id": "term"}, {"$set": cur}, upsert=True)
    return {"ok": True, "config": cur}


# ============================================================================
# ANALISI E RISCHIO — prima analizza il mercato (1d/4h/1h), poi applica i controlli prima di entrare
# e ti suggerisce stop, obiettivo, importo, leva e prezzo di liquidazione. Long, SHORT e LEVA.
# SOLO SIMULAZIONE: nessun ordine va a Bybit. Puoi aprire tu col bottone oppure accendere l'APERTURA AUTOMATICA:
# il bot apre da solo solo se mercato e controlli sono d'accordo, divide la posizione in lotti, chiude il primo lotto
# a 1R, porta lo stop a pareggio e rispetta i limiti di rischio totale (giornaliero, per serie di perdite, ecc.).
# Le regole di calcolo stanno in desk_logic.py.
# ============================================================================
try:
    import desk_logic as DK
except ImportError:  # pragma: no cover
    from backend import desk_logic as DK

_desk_lock = asyncio.Lock()
_desk_candle_cache: dict[str, tuple[float, list[list[float]]]] = {}


async def _desk_price(symbol: str) -> Optional[float]:
    if _crus_cost["mode"] == "kraken":
        _kp = await kr_price(symbol)
        if _kp:
            return _kp
    try:
        p = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
    except Exception:  # noqa: BLE001
        p = None
    return float(p) if p and p > 0 else None


async def _desk_cfg() -> dict[str, Any]:
    doc = await db.desk_config.find_one({"_id": "desk"}, {"_id": 0}) or {}
    out = dict(DK.DEFAULTS)
    out.update({k: v for k, v in doc.items() if k in DK.DEFAULTS})
    return out


async def _desk_event(symbol: str, text: str) -> None:
    try:
        await db.desk_events.insert_one({"at": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "text": text})
        n = await db.desk_events.count_documents({})
        if n > 150:
            old = await db.desk_events.find({}, {"_id": 1}).sort("at", 1).to_list(n - 100)
            await db.desk_events.delete_many({"_id": {"$in": [o["_id"] for o in old]}})
    except Exception:  # noqa: BLE001
        pass


async def _desk_candles(symbol: str, tf: str) -> list[list[float]]:
    if _crus_cost["mode"] == "kraken":
        _kc = await kr_candles(symbol, tf)
        if _kc:
            return _kc
    key = f"{symbol}|{tf}"
    now = time.time()
    c = _desk_candle_cache.get(key)
    if c and c[1] and now - c[0] < (60 if tf == "1h" else 180):
        return c[1]
    candles = await exchange.get_klines(symbol, tf, limit=300)
    if candles:
        _desk_candle_cache[key] = (now, candles)
        return candles
    return c[1] if c and c[1] and now - c[0] < 1800 else []


async def _desk_symbols(dc: dict[str, Any]) -> list[str]:
    """Monete analizzate: se la pre-analisi e' accesa e ha gia' un elenco, quello; altrimenti l'elenco fisso."""
    if dc.get("scan_enabled"):
        u = await db.desk_state.find_one({"_id": "universe"}, {"_id": 0}) or {}
        syms = [r["symbol"] for r in u.get("rows", [])]
        if syms:
            return syms
    return list(dc["symbols"])


_desk_scan_running = False


async def desk_scan() -> None:
    """PRE-ANALISI: scandaglia tutto il panorama USDC di Bybit e tiene le migliori coppie."""
    global _desk_scan_running
    if _desk_scan_running:
        return
    _desk_scan_running = True
    try:
        dc = await _desk_cfg()
        cfg = await get_config()
        tickers = await exchange.get_tickers()
        cands = DK.scan_candidates(tickers, float(dc["scan_min_volume"]), int(dc["scan_pool"]))
        if not cands:   # rete o Bybit non risponde: tengo l'elenco di prima
            await _desk_event("-", "PRE-ANALISI: nessuna coppia letta da Bybit, tengo l'elenco precedente")
            return
        sem = asyncio.Semaphore(4)

        async def one(c: dict[str, Any]) -> Optional[dict[str, Any]]:
            async with sem:
                try:
                    b = await _desk_build(c["symbol"], 1.0, float(dc["auto_risk_pct"]), dc, cfg)
                except Exception:  # noqa: BLE001
                    return None
            bias = b["analysis"]["bias"]
            pl = b["plans"].get(bias) if bias in ("long", "short") else None
            return {**c, "bias": bias, "score": b["analysis"]["score"], "checks_ok": bool(pl and pl["checks"]["ok"]),
                    "why": (pl["checks"]["reasons"][0] if pl and pl["checks"]["reasons"] else None),
                    "rr": pl["rr"] if pl else None}

        res = [r for r in await asyncio.gather(*[one(c) for c in cands]) if r]
        chosen = DK.rank_scan(res, int(dc["scan_top"]))
        await db.desk_state.update_one({"_id": "universe"}, {"$set": {
            "rows": chosen, "at": datetime.now(timezone.utc).isoformat(), "scanned": len(cands), "analyzed": len(res),
            "total_pairs": len([t for t in tickers if str(t.get("symbol") or "").endswith("USDC")])}}, upsert=True)
        await _desk_event("-", "PRE-ANALISI: scandagliate %d coppie USDC, scelte %d: %s" % (
            len(res), len(chosen), ", ".join("%s %s" % (r["symbol"][:-4], r["bias"]) for r in chosen) or "nessuna con tendenza chiara"))
    except Exception as e:  # noqa: BLE001
        logger.exception("Pre-analisi: errore: %s", e)
    finally:
        _desk_scan_running = False


async def _desk_wallet() -> dict[str, Any]:
    w = await db.desk_wallet.find_one({"_id": "w"}, {"_id": 0})
    if w is None:
        dc = await _desk_cfg()
        w = {"cash": float(dc["capital"])}
        await db.desk_wallet.update_one({"_id": "w"}, {"$set": w}, upsert=True)
    return w


async def _desk_build(symbol: str, leverage: float, risk_pct: float, dc: dict[str, Any], cfg: Config) -> dict[str, Any]:
    price = await _desk_price(symbol)
    frames = {tf: await _desk_candles(symbol, tf) for tf in ("1d", "4h", "1h")}
    if price is None or not frames["1h"]:
        raise HTTPException(status_code=400, detail="Dati di mercato non disponibili per " + symbol)
    an = DK.analyze_market(frames)
    if not an.get("ok"):
        raise HTTPException(status_code=400, detail="Dati insufficienti per analizzare " + symbol)
    a1 = DK.atr(frames["1h"]) or 0.0
    if a1 <= 0:
        raise HTTPException(status_code=400, detail="Volatilita' non calcolabile per " + symbol)
    s = await precheck_settings()
    plans = {}
    for side in ("long", "short"):
        pl = _desk_risk_plan(side, price, frames["1h"], a1, capital=float(dc["capital"]), risk_pct=risk_pct, leverage=leverage,
                          fee_pct=_desk_fee(cfg), funding_day_pct=float(dc["funding_day_pct"]), max_leverage=float(dc["max_leverage"]),
                          max_loss_usdc=s["max_loss_usdc"] if s["check_max_loss"] else None)
        _, info = await _desk_fill(symbol, "buy" if side == "long" else "sell", price, cfg,
                                      quote_budget=pl["notional"] if side == "long" else None,
                                      base_qty=None if side == "long" else pl["notional"] / price)
        st_m, tg_m = DK.mirror_for_check(pl)
        ev = PEC.evaluate(price=price, notional=pl["notional"], fee_pct=_desk_fee(cfg), stop=st_m, target=tg_m,
                          book_cost_pct=abs(float(info.get("cost_pct") or 0.0)), spread_pct=info.get("spread_pct"),
                          book_model=str(info.get("fill_model")), s=s, use_max_loss=False)
        pl["checks"] = {"ok": ev["ok"], "reasons": ev["reasons"]}
        plans[side] = pl
    return {"symbol": symbol, "price": price, "analysis": an, "plans": plans, "atr_1h": a1}


@api.get("/desk/analisi")
async def desk_analisi(symbol: str, leverage: float = 1.0, risk_pct: float = 1.0) -> dict[str, Any]:
    cfg = await get_config()
    dc = await _desk_cfg()
    if symbol not in await _desk_symbols(dc):
        raise HTTPException(status_code=400, detail="Moneta non in elenco.")
    return await _desk_build(symbol, max(1.0, min(leverage, float(DK.MAX_LEVERAGE))), max(0.1, min(risk_pct, 3.0)), dc, cfg)


async def _desk_close(p: dict[str, Any], cfg: Config, reason: str, cur: float) -> bool:
    async with _desk_lock:
        fresh = await db.desk_positions.find_one({"id": p["id"], "status": "open"}, {"_id": 0})
        if not fresh:
            return False
        p = fresh
        days = max(0.0, (time.time() - p["opened_ts"]) / 86400.0)
        carry = p["notional"] * (p["funding_day_pct"] / 100.0) * days if (p["leverage"] > 1.0 or p["side"] == "short") else 0.0
        if reason == "liquidazione":
            exit_price = p["liquidation"]
        else:
            if p["side"] == "long":
                exit_price, _ = await _desk_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
            else:
                exit_price, _ = await _desk_fill(p["symbol"], "buy", cur, cfg, quote_budget=cur * p["quantity"])
        res_pnl = DK.pnl(p["side"], p["fill_price"], exit_price, p["quantity"], _desk_fee(cfg), carry, p["margin"])
        done = float(p.get("realized_partial") or 0.0)
        r = await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {
            "status": "closed", "close_price": exit_price, "close_reason": reason, "pnl_usdt": round(res_pnl + done, 4),
            "pnl_last_lot": round(res_pnl, 4),
            "carry_usdt": round(carry, 4), "closed_at": datetime.now(timezone.utc).isoformat()}})
        if r.modified_count:
            await db.desk_wallet.update_one({"_id": "w"}, {"$inc": {"cash": p["margin"] + res_pnl}}, upsert=True)
            await _desk_event(p["symbol"], "CHIUSA %s a %.6g (%s) risultato totale %+.2f" % (p["side"], exit_price, reason, res_pnl + done))
        return bool(r.modified_count)


async def _desk_take_lot(p: dict[str, Any], cfg: Config, cur: float) -> None:
    """Chiude il PRIMO lotto al primo obiettivo e porta lo stop a pareggio sul resto."""
    async with _desk_lock:
        p = await db.desk_positions.find_one({"id": p["id"], "status": "open"}, {"_id": 0})
        if not p or p.get("tp1_done") or int(p.get("lots_left", 1)) < 2:
            return
        qty0 = float(p["qty0"]); part_qty = qty0 / int(p["lots_total"])
        frac = min(1.0, part_qty / p["quantity"])
        part_margin, part_notional = p["margin"] * frac, p["notional"] * frac
        if p["side"] == "long":
            ex, _ = await _desk_fill(p["symbol"], "sell", cur, cfg, base_qty=part_qty)
        else:
            ex, _ = await _desk_fill(p["symbol"], "buy", cur, cfg, quote_budget=cur * part_qty)
        days = max(0.0, (time.time() - p["opened_ts"]) / 86400.0)
        carry = part_notional * (p["funding_day_pct"] / 100.0) * days if (p["leverage"] > 1.0 or p["side"] == "short") else 0.0
        pp = DK.pnl(p["side"], p["fill_price"], ex, part_qty, _desk_fee(cfg), carry, part_margin)
        dc = await _desk_cfg()
        new_stop = p["fill_price"] if dc["be_after_tp1"] else p["stop"]
        if p["side"] == "long":
            new_stop = max(new_stop, p["stop"]) if dc["be_after_tp1"] else p["stop"]
        else:
            new_stop = min(new_stop, p["stop"]) if dc["be_after_tp1"] else p["stop"]
        r = await db.desk_positions.update_one({"id": p["id"], "status": "open", "tp1_done": {"$ne": True}}, {"$set": {
            "tp1_done": True, "stop": new_stop, "quantity": p["quantity"] - part_qty, "margin": p["margin"] - part_margin,
            "notional": p["notional"] - part_notional, "lots_left": int(p["lots_left"]) - 1,
            "realized_partial": round(float(p.get("realized_partial") or 0.0) + pp, 4)}})
        if r.modified_count:
            await db.desk_wallet.update_one({"_id": "w"}, {"$inc": {"cash": part_margin + pp}}, upsert=True)
            await _desk_event(p["symbol"], "PRIMO LOTTO chiuso a %.6g (%+.2f)%s" % (ex, pp, ", stop a pareggio" if dc["be_after_tp1"] else ""))


async def desk_pass() -> None:
    cfg = await get_config()
    for p in await db.desk_positions.find({"status": "open"}, {"_id": 0}).to_list(50):
        cur = await _desk_price(p["symbol"])
        if cur is None:
            continue
        hit = DK.exit_hit(p["side"], cur, p["stop"], p["target"], p.get("liquidation"))
        if hit:
            await _desk_close(p, cfg, hit, cur)
            continue
        if p.get("tp1") and not p.get("tp1_done") and int(p.get("lots_left", 1)) >= 2 and DK.tp1_hit(p["side"], cur, p["tp1"]):
            await _desk_take_lot(p, cfg, cur)


async def _desk_day_stats(dc: dict[str, Any]) -> dict[str, Any]:
    """Numeri per i limiti di rischio: perdita di oggi, aperture di oggi, serie di perdite, rischio aperto."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    closed = await db.desk_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(200)
    today_pnl = sum(c.get("pnl_usdt", 0.0) for c in closed if str(c.get("closed_at", "")).startswith(today))
    opened_today = await db.desk_positions.count_documents({"opened_at": {"$regex": "^" + today}, "auto": True})
    opens = await db.desk_positions.find({"status": "open"}, {"_id": 0}).to_list(50)
    open_risks = [0.0 if o.get("tp1_done") and dc["be_after_tp1"] else float(o.get("max_loss") or 0.0) for o in opens]
    since = None
    if closed:
        try:
            since = (datetime.now(timezone.utc) - datetime.fromisoformat(closed[0]["closed_at"])).total_seconds() / 3600.0
        except Exception:  # noqa: BLE001
            since = None
    return {"today_pnl": today_pnl, "opened_today": opened_today, "open_risks": open_risks, "closed": closed, "since_last_close_h": since}


async def _desk_open_core(symbol: str, side: str, lev: float, rp: float, force: bool, dc: dict[str, Any], cfg: Config, auto: bool = False) -> dict[str, Any]:
    async with _desk_lock:
        b = await _desk_build(symbol, lev, rp, dc, cfg)
        if auto and lev > b["plans"][side]["safe_leverage"]:      # il bot abbassa da solo la leva se serve
            lev = b["plans"][side]["safe_leverage"]
            b = await _desk_build(symbol, lev, rp, dc, cfg)
        pl, an = b["plans"][side], b["analysis"]
        why: list[str] = []
        if an["bias"] != side and not force:
            why.append("il mercato dice '%s', non '%s' (puoi forzare, ma e' contro la tendenza)" % (an["bias"], side))
        why += pl["checks"]["reasons"]
        if lev > float(dc["max_leverage"]) and not force and not auto:
            why.append("leva sopra il massimo scelto (%gx)" % dc["max_leverage"])
        if pl["liq_dist_pct"] is not None and pl["liq_dist_pct"] < 1.5 * pl["risk_pct_price"]:
            why.append("la liquidazione e' troppo vicina allo stop: usa leva %gx o meno" % pl["safe_leverage"])
        if pl["notional"] < 5.5:
            why.append("importo %.2f USDC sotto il minimo di 5,5" % pl["notional"])
        opens = await db.desk_positions.find({"status": "open"}, {"symbol": 1, "_id": 0}).to_list(50)
        if len(opens) >= int(dc["max_open"]):
            why.append("hai gia' %d posizioni aperte" % len(opens))
        if any(o["symbol"] == symbol for o in opens):
            why.append("c'e' gia' una posizione su " + symbol)
        w = await _desk_wallet()
        if pl["margin"] > w["cash"]:
            why.append("margine %.2f USDC piu' del contante libero %.2f" % (pl["margin"], w["cash"]))
        # rischio TOTALE (vale anche per le aperture manuali)
        stt = await _desk_day_stats(dc)
        why += DK.risk_gate(capital=float(dc["capital"]), open_risks=stt["open_risks"], new_risk=pl["max_loss"],
                            max_total_risk_pct=float(dc["max_total_risk_pct"]), today_pnl=stt["today_pnl"], daily_loss_pct=float(dc["daily_loss_pct"]))
        if auto:
            if stt["opened_today"] >= int(dc["max_trades_day"]):
                why.append("gia' %d aperture automatiche oggi" % stt["opened_today"])
            sb = DK.streak_block([c.get("pnl_usdt", 0.0) for c in stt["closed"]], stt["since_last_close_h"], int(dc["streak_pause"]), float(dc["streak_pause_h"]))
            if sb:
                why.append(sb)
            last = next((c for c in stt["closed"] if c["symbol"] == symbol), None)
            if last and dc["cooldown_min"]:
                try:
                    mins = (datetime.now(timezone.utc) - datetime.fromisoformat(last["closed_at"])).total_seconds() / 60.0
                    if mins < float(dc["cooldown_min"]):
                        why.append("attesa dopo l'ultima chiusura su %s (ancora %d minuti)" % (symbol, float(dc["cooldown_min"]) - mins))
                except Exception:  # noqa: BLE001
                    pass
        if why:
            raise HTTPException(status_code=400, detail="NON apro: " + "; ".join(why))
        price = b["price"]
        if side == "long":
            fill, info = await _desk_fill(symbol, "buy", price, cfg, quote_budget=pl["notional"])
        else:
            fill, info = await _desk_fill(symbol, "sell", price, cfg, base_qty=pl["notional"] / price)
        qty = pl["notional"] / fill
        liq = DK.liquidation_price(side, fill, pl["leverage"])
        lots = DK.lots_for(pl["notional"], int(dc["lots"]))
        now = datetime.now(timezone.utc)
        doc = {"id": str(uuid.uuid4()), "symbol": symbol, "status": "open", "side": side, "leverage": pl["leverage"],
               "entry": price, "fill_price": fill, "fill_model": info.get("fill_model"), "quantity": qty, "qty0": qty,
               "notional": pl["notional"], "margin": pl["margin"], "stop": pl["stop"], "target": pl["target"],
               "tp1": DK.tp1_price(side, fill, pl["stop"], float(dc["tp1_r"])) if lots > 1 else None,
               "lots_total": lots, "lots_left": lots, "tp1_done": False, "realized_partial": 0.0,
               "liquidation": liq, "max_loss": pl["max_loss"], "rr": pl["rr"], "bias_at_entry": an["bias"],
               "forced": bool(force), "auto": bool(auto), "funding_day_pct": float(dc["funding_day_pct"]),
               "opened_at": now.isoformat(), "opened_ts": now.timestamp()}
        await db.desk_positions.insert_one(dict(doc))
        await db.desk_wallet.update_one({"_id": "w"}, {"$inc": {"cash": -pl["margin"]}}, upsert=True)
        await _desk_event(symbol, "%s %s %gx a %.6g (importo %.2f in %d lotti, margine %.2f, stop %.6g, obiettivo %.6g, perdita max %.2f)" % (
            "APERTA DAL BOT" if auto else "APERTA", side, pl["leverage"], fill, pl["notional"], lots, pl["margin"], pl["stop"], pl["target"], pl["max_loss"]))
    doc.pop("_id", None)
    return doc


async def desk_auto_pass() -> None:
    """Apertura automatica: per ogni moneta, se il mercato indica long/short e TUTTI i controlli passano, apre."""
    dc = await _desk_cfg()
    if not dc["auto_enabled"]:
        return
    cfg = await get_config()
    for sym in await _desk_symbols(dc):
        if await db.desk_positions.find_one({"symbol": sym, "status": "open"}, {"_id": 1}):
            continue
        try:
            b = await _desk_build(sym, float(dc["auto_leverage"]), float(dc["auto_risk_pct"]), dc, cfg)
        except HTTPException:
            continue
        bias = b["analysis"]["bias"]
        if bias not in ("long", "short"):
            continue
        try:
            await _desk_open_core(sym, bias, float(dc["auto_leverage"]), float(dc["auto_risk_pct"]), False, dc, cfg, auto=True)
        except HTTPException as e:
            # un solo messaggio per motivo: niente spam ogni minuto
            key = "auto|" + sym
            st = await db.desk_state.find_one({"_id": key}) or {}
            sig = __import__("re").sub(r"\d+(\.\d+)?", "#", str(e.detail))  # i numeri che cambiano non contano
            if st.get("why") != sig or time.time() - float(st.get("ts", 0)) > 3600:
                await db.desk_state.update_one({"_id": key}, {"$set": {"why": sig, "ts": time.time()}}, upsert=True)
                await log_reject(sym, "-", "desk", "controllo ingresso (auto, " + bias + "): " + str(e.detail))
                await _desk_event(sym, "Auto: %s indicato ma non apro - %s" % (bias, e.detail))


_desk_auto_last = 0.0
_desk_scan_last = 0.0


async def desk_loop() -> None:
    global _desk_auto_last, _desk_scan_last
    await asyncio.sleep(18)
    while True:
        try:
            await desk_pass()
            _dcl = await _desk_cfg()
            if _dcl["scan_enabled"] and time.time() - _desk_scan_last > float(_dcl["scan_every_min"]) * 60:
                _desk_scan_last = time.time()
                asyncio.create_task(desk_scan())
            if time.time() - _desk_auto_last > 45:
                _desk_auto_last = time.time()
                await desk_auto_pass()
        except Exception as e:  # noqa: BLE001
            logger.exception("Analisi e rischio: errore nel ciclo: %s", e)
        await asyncio.sleep(4)


@api.post("/desk/open")
async def desk_open(body: dict[str, Any]) -> dict[str, Any]:
    cfg = await get_config()
    dc = await _desk_cfg()
    symbol, side = body.get("symbol"), body.get("side")
    if symbol not in await _desk_symbols(dc) or side not in ("long", "short"):
        raise HTTPException(status_code=400, detail="Scegli moneta e direzione.")
    try:
        lev = float(body.get("leverage", 1.0)); rp = float(body.get("risk_pct", dc["risk_pct"]))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Numeri non validi.")
    if not (1.0 <= lev <= DK.MAX_LEVERAGE) or not (0.1 <= rp <= 3.0):
        raise HTTPException(status_code=400, detail="Leva tra 1 e %d, rischio tra 0,1%% e 3%%." % DK.MAX_LEVERAGE)
    try:
        doc = await _desk_open_core(symbol, side, lev, rp, bool(body.get("force")), dc, cfg)
    except HTTPException as e:
        await log_reject(symbol, "-", "desk", "controllo ingresso: " + str(e.detail))
        raise
    return {"ok": True, "position": doc}


@api.get("/desk/status")
async def desk_status() -> dict[str, Any]:
    cfg = await get_config()
    dc = await _desk_cfg()
    w = await _desk_wallet()
    open_docs = await db.desk_positions.find({"status": "open"}, {"_id": 0}).sort("opened_at", -1).to_list(50)
    o_out, margins, floating = [], 0.0, 0.0
    for p in open_docs:
        cur = await _desk_price(p["symbol"]) or p["fill_price"]
        days = max(0.0, (time.time() - p["opened_ts"]) / 86400.0)
        carry = p["notional"] * (p["funding_day_pct"] / 100.0) * days if (p["leverage"] > 1.0 or p["side"] == "short") else 0.0
        fl = DK.pnl(p["side"], p["fill_price"], cur, p["quantity"], _desk_fee(cfg), carry, p["margin"])
        margins += p["margin"]; floating += fl
        o_out.append({**p, "current_price": cur, "floating_pnl": round(fl + float(p.get("realized_partial") or 0.0), 4)})
    closed = await db.desk_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(40)
    allc = await db.desk_positions.find({"status": "closed"}, {"pnl_usdt": 1, "_id": 0}).to_list(5000)
    events = await db.desk_events.find({}, {"_id": 0}).sort("at", -1).to_list(20)
    stt = await _desk_day_stats(dc)
    risk = {"open_risk": round(sum(stt["open_risks"]), 4), "open_risk_limit": round(float(dc["capital"]) * float(dc["max_total_risk_pct"]) / 100.0, 4),
            "today_pnl": round(stt["today_pnl"], 4), "daily_limit": round(-float(dc["capital"]) * float(dc["daily_loss_pct"]) / 100.0, 4),
            "auto_today": stt["opened_today"],
            "pause": DK.streak_block([c.get("pnl_usdt", 0.0) for c in stt["closed"]], stt["since_last_close_h"], int(dc["streak_pause"]), float(dc["streak_pause_h"]))}
    uni = await db.desk_state.find_one({"_id": "universe"}, {"_id": 0}) or {}
    scan = {"running": _desk_scan_running, "at": uni.get("at"), "scanned": uni.get("scanned"), "analyzed": uni.get("analyzed"),
            "total_pairs": uni.get("total_pairs"), "rows": uni.get("rows", [])}
    return {"config": dc, "risk": risk, "scan": scan, "symbols_active": await _desk_symbols(dc), "cash": round(w["cash"], 4), "equity": round(w["cash"] + margins + floating, 4),
            "open": o_out, "closed": closed, "events": events, "trades": len(allc),
            "wins": sum(1 for c in allc if c.get("pnl_usdt", 0) > 0), "realized": round(sum(c.get("pnl_usdt", 0.0) for c in allc), 4)}


@api.post("/desk/scan")
async def desk_scan_now() -> dict[str, Any]:
    if _desk_scan_running:
        return {"ok": True, "running": True}
    asyncio.create_task(desk_scan())
    return {"ok": True, "running": True}


@api.post("/desk/close/{pid}")
async def desk_close_now(pid: str) -> dict[str, Any]:
    cfg = await get_config()
    p = await db.desk_positions.find_one({"id": pid, "status": "open"}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Posizione non trovata.")
    cur = await _desk_price(p["symbol"])
    if cur is None:
        raise HTTPException(status_code=400, detail="Prezzo non disponibile.")
    return {"ok": await _desk_close(p, cfg, "chiusura_manuale", cur)}


@api.post("/desk/config")
async def desk_set_config(body: dict[str, Any]) -> dict[str, Any]:
    cur = await _desk_cfg()
    new = dict(cur)
    try:
        if "symbols" in body:
            syms = [str(x).strip().upper() for x in body["symbols"] if str(x).strip()]
            if not syms or len(syms) > 12 or any(not s.endswith("USDC") for s in syms):
                raise ValueError("Monete: da 1 a 12, tutte in USDC (esempio BTCUSDC).")
            new["symbols"] = syms
        for k, (lo, hi) in DK.CFG_LIMITS.items():
            if k in body:
                v = float(body[k])
                if not (lo <= v <= hi):
                    raise ValueError("%s deve stare tra %g e %g." % (k, lo, hi))
                new[k] = int(v) if k in DK.INT_KEYS else v
        for k in ("auto_enabled", "be_after_tp1", "scan_enabled"):
            if k in body:
                new[k] = bool(body[k])
        if "capital" in body:
            c = float(body["capital"])
            if not (10 <= c <= 100000):
                raise ValueError("Capitale tra 10 e 100000.")
            if await db.desk_positions.find_one({"status": "open"}, {"_id": 1}):
                raise ValueError("Chiudi prima le posizioni aperte per cambiare il capitale.")
            new["capital"] = c
            await db.desk_wallet.update_one({"_id": "w"}, {"$set": {"cash": c}}, upsert=True)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.desk_config.update_one({"_id": "desk"}, {"$set": new}, upsert=True)
    return {"ok": True, "config": new}


# ============================================================================
# ICT (Inner Circle Trader) — versione MECCANICA, solo long, spot.
# Sweep dei minimi -> rottura della struttura (MSS) -> ritorno nel gap (FVG).
# Le regole stanno in ict_logic.py (funzioni pure, provate a parte).
# Paper per impostazione predefinita; reale solo se "ict" e' in live_strategies
# (si accende dalla pagina Trading reale) e passa tutti i controlli di real_guard.
# NON e' una strategia dimostrata: e' una prova dal vivo.
# ============================================================================
try:
    import ict_logic as ICT
except ImportError:  # pragma: no cover
    from backend import ict_logic as ICT

ICT_WALLET_ID = "ict_wallet_singleton"
ICT_SETTING_KEYS = ("ict_enabled", "ict_symbols", "ict_timeframe", "ict_rr", "ict_kill_zones",
                    "ict_alloc_pct", "ict_max_open", "ict_max_hold_h")
ICT_TIMEFRAMES = ("5m", "15m", "30m", "1h")
_ict_candle_cache: dict[str, tuple[float, list[list[float]]]] = {}
_ict_seen: set[str] = set()
_ict_log_at: dict[str, float] = {}
_ict_status: dict[str, Any] = {"at": None, "watching": {}}


async def get_ict_wallet(cfg: Optional[Config] = None) -> dict[str, Any]:
    doc = await db.ict_wallet.find_one({"_id": ICT_WALLET_ID}, {"_id": 0})
    if not doc:
        cfg = cfg or await get_config()
        doc = {"cash": float(cfg.ict_capital), "start_capital": float(cfg.ict_capital)}
        await db.ict_wallet.update_one({"_id": ICT_WALLET_ID}, {"$set": doc}, upsert=True)
    return doc


async def _ict_log(symbol: str, tf: str, reason: str) -> None:
    key = f"{symbol}|{tf}|{reason}"
    now = time.time()
    if now - _ict_log_at.get(key, 0.0) < 300:
        return
    _ict_log_at[key] = now
    await log_reject(symbol, tf, "ict", reason)


async def _ict_candles(symbol: str, tf: str) -> list[list[float]]:
    now = time.time()
    cached = _ict_candle_cache.get(f"{symbol}|{tf}")
    if cached and cached[1] and now - cached[0] < 30:
        return cached[1]
    candles = await exchange.get_klines(symbol, tf)
    if candles:
        _ict_candle_cache[f"{symbol}|{tf}"] = (now, candles)
        return candles
    if cached and cached[1] and now - cached[0] < 300:
        return cached[1]
    return []


async def run_ict_scan() -> None:
    cfg = await get_config()
    if not cfg.ict_enabled:
        return
    tf = cfg.ict_timeframe
    watching: dict[str, Any] = {}
    for symbol in cfg.ict_symbols:
        candles = await _ict_candles(symbol, tf)
        if len(candles) < 60:
            await _ict_log(symbol, tf, "dati insufficienti")
            continue
        live = price_feed.get(symbol) or await price_feed.price_or_rest(symbol)
        if not live or live <= 0:
            await _ict_log(symbol, tf, "prezzo live non disponibile")
            continue
        setups = ICT.find_setups(candles)
        watching[symbol] = {"setups": len(setups), "price": live,
                            "fvg_top": setups[-1]["fvg_top"] if setups else None,
                            "stop": setups[-1]["stop"] if setups else None}
        if not setups:
            continue
        if cfg.ict_kill_zones and not ICT.in_kill_zone(datetime.now(timezone.utc).hour):
            await _ict_log(symbol, tf, "setup pronto ma fuori dagli orari (kill zone)")
            continue
        if await db.ict_positions.find_one({"symbol": symbol, "status": "open"}):
            continue
        open_n = await db.ict_positions.count_documents({"status": "open"})
        if open_n >= cfg.ict_max_open:
            await _ict_log(symbol, tf, "numero massimo di posizioni aperte raggiunto")
            continue
        for st in reversed(setups):
            sid = f"{symbol}|{tf}|{st['id']}"
            if sid in _ict_seen or await db.ict_seen.find_one({"_id": sid}, {"_id": 1}):
                continue
            plan = ICT.entry_plan(st, live, cfg.ict_rr)
            if not plan:
                continue
            _ict_seen.add(sid)
            await db.ict_seen.update_one({"_id": sid}, {"$set": {"at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
            await open_ict_position(symbol, tf, st, plan, cfg)
            break
    _ict_status["at"] = datetime.now(timezone.utc).isoformat()
    _ict_status["watching"] = watching


async def open_ict_position(symbol: str, tf: str, st: dict[str, Any], plan: dict[str, Any], cfg: Config) -> None:
    wallet = await get_ict_wallet(cfg)
    cash = float(wallet.get("cash", 0.0))
    open_rows = await db.ict_positions.find({"status": "open"}, {"notional": 1, "_id": 0}).to_list(50)
    equity = cash + sum(r.get("notional", 0.0) for r in open_rows)
    notional = min(equity * cfg.ict_alloc_pct / 100.0, cash)
    if notional < 1.0:
        await _ict_log(symbol, tf, "capitale ICT esaurito")
        return
    mode, why = await real_guard(cfg, "ict")
    if mode == "blocked":
        await log_reject(symbol, tf, "ict", f"reale bloccato: {why}")
        return
    real_info = None
    if mode == "real":
        rb = await real_open_buy(symbol, notional, cfg, "ict", cash_cap=cash)
        if not rb:
            await log_reject(symbol, tf, "ict", "ordine reale non eseguito (vedi registro reale)")
            return
        real_info = rb
        notional = rb["value"]
        fill_price = rb["avg_price"]
        fill_info = {"fill_model": "reale", "cost_pct": 0.0, "spread_pct": None}
        quantity = rb["net_qty"]
    else:
        fill_price, fill_info = await simulate_fill(symbol, "buy", plan["entry"], cfg, quote_budget=notional)
        quantity = notional / fill_price
    stop = plan["stop"]
    if fill_price <= stop:
        # il prezzo di riempimento e' gia' sotto lo stop: non apro (in paper); nel reale chiudero' subito allo stop
        if not real_info:
            await log_reject(symbol, tf, "ict", "prezzo di riempimento sotto lo stop: non apro")
            return
    target = fill_price + cfg.ict_rr * (fill_price - stop)
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()), "symbol": symbol, "timeframe": tf, "side": "long",
        "entry": plan["entry"], "fill_price": fill_price, "fill_model": fill_info["fill_model"],
        "entry_cost_pct": fill_info["cost_pct"], "spread_pct": fill_info.get("spread_pct"),
        "stop": stop, "target": target, "risk_pct": round((fill_price - stop) / fill_price * 100.0, 3), "rr": cfg.ict_rr,
        "setup_id": st["id"], "sweep_low": st["sweep_low"], "fvg_top": st["fvg_top"], "fvg_bottom": st["fvg_bottom"],
        "quantity": quantity, "notional": notional, "status": "open",
        "opened_at": now.isoformat(), "opened_ts": now.timestamp(),
    }
    if real_info:
        doc["real"] = {k: real_info[k] for k in ("order_id", "link_id", "net_qty", "fee_base", "fee_quote", "qty")}
    await db.ict_positions.insert_one(dict(doc))
    await db.ict_wallet.update_one({"_id": ICT_WALLET_ID}, {"$inc": {"cash": -notional}}, upsert=True)


async def monitor_ict_positions() -> None:
    cfg = await get_config()
    open_positions = await db.ict_positions.find({"status": "open"}, {"_id": 0}).to_list(20)
    for p in open_positions:
        cur = price_feed.get(p["symbol"]) or await price_feed.price_or_rest(p["symbol"])
        if not cur or cur <= 0:
            continue
        await record_track("ict", db.ict_positions, p, cur)
        hit = ICT.exit_check(p, cur, time.time(), cfg.ict_max_hold_h)
        if not hit:
            continue
        if p.get("real"):
            if not real_sell_due(p["id"]):
                continue
            sell = await real_close_position(p, cfg, "ictx")
            if not sell:
                await rlog(f"Vendita reale ICT di {p['symbol']} non riuscita, riprovo tra 30 s", "errore")
                continue
            sell_price = sell["avg_price"]
            pnl = sell["net_value"] + sell.get("dust_value", 0.0) - p["notional"]
            await db.ict_wallet.update_one({"_id": ICT_WALLET_ID}, {"$inc": {"cash": p["notional"] + pnl}}, upsert=True)
            await db.ict_positions.update_one({"id": p["id"]}, {"$set": {
                "status": "closed", "close_price": sell_price, "close_reason": hit, "close_last_price": cur,
                "exit_fill_model": "reale", "fee_pct": cfg.sim_fee_pct, "pnl_usdt": round(pnl, 4),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "real_exit": {k: v for k, v in sell.items() if k != "dust"},
            }})
            await close_track(p["id"], cur)
            continue
        if cfg.sim_realistic_fills and not book_recheck_due(p["id"]):
            continue
        sell_price, sell_info = await simulate_fill(p["symbol"], "sell", cur, cfg, base_qty=p["quantity"])
        gross = (sell_price - p["fill_price"]) * p["quantity"]
        exit_notional = sell_price * p["quantity"]
        fees = (p["notional"] + exit_notional) * (cfg.sim_fee_pct / 100.0)
        pnl = gross - fees
        await db.ict_wallet.update_one({"_id": ICT_WALLET_ID}, {"$inc": {"cash": p["notional"] + pnl}}, upsert=True)
        await db.ict_positions.update_one({"id": p["id"]}, {"$set": {
            "status": "closed", "close_price": sell_price, "close_reason": hit, "close_last_price": cur,
            "exit_fill_model": sell_info["fill_model"], "exit_cost_pct": sell_info["cost_pct"],
            "exit_spread_pct": sell_info.get("spread_pct"), "fee_pct": cfg.sim_fee_pct,
            "pnl_usdt": round(pnl, 4), "closed_at": datetime.now(timezone.utc).isoformat(),
        }})
        await close_track(p["id"], cur)


async def ict_monitor_loop() -> None:
    """Uscite ogni 3 s sul prezzo live; ricerca ingressi nello stesso ciclo (un solo chiamante: niente doppie aperture)."""
    await asyncio.sleep(12)
    while True:
        try:
            await monitor_ict_positions()
        except Exception as e:  # noqa: BLE001
            logger.exception("ICT monitor error: %s", e)
        try:
            await run_ict_scan()
        except Exception as e:  # noqa: BLE001
            logger.exception("ICT scan error: %s", e)
        await asyncio.sleep(3)


async def _ict_close_all_real() -> tuple[int, int]:
    cfg = await get_config()
    rows = await db.ict_positions.find({"status": "open", "real": {"$exists": True}}, {"_id": 0}).to_list(50)
    done, failed = 0, 0
    for p in rows:
        sell = await real_close_position(p, cfg, "chiudi")
        if not sell:
            failed += 1
            continue
        cur = sell["avg_price"]
        pnl = sell["net_value"] + sell.get("dust_value", 0.0) - p["notional"]
        await db.ict_wallet.update_one({"_id": ICT_WALLET_ID}, {"$inc": {"cash": p["notional"] + pnl}}, upsert=True)
        await db.ict_positions.update_one({"id": p["id"]}, {"$set": {
            "status": "closed", "close_price": cur, "close_reason": "chiusura_manuale_reale", "close_last_price": cur,
            "exit_fill_model": "reale", "fee_pct": cfg.sim_fee_pct, "pnl_usdt": round(pnl, 4),
            "closed_at": datetime.now(timezone.utc).isoformat(), "real_exit": {k: v for k, v in sell.items() if k != "dust"},
        }})
        await close_track(p["id"], cur)
        done += 1
    return done, failed


@api.get("/ict/portfolio")
async def ict_portfolio() -> dict[str, Any]:
    cfg = await get_config()
    wallet = await get_ict_wallet(cfg)
    open_docs = await db.ict_positions.find({"status": "open"}, {"_id": 0}).to_list(50)
    closed_docs = await db.ict_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(300)
    open_out, open_value, unreal = [], 0.0, 0.0
    for p in open_docs:
        cur = price_feed.get(p["symbol"]) or await price_feed.price_or_rest(p["symbol"]) or p["entry"]
        val = cur * p["quantity"]
        fl = val - p["notional"] - (p["notional"] + val) * (cfg.sim_fee_pct / 100.0)
        open_value += val
        unreal += fl
        open_out.append({**p, "current_price": cur, "floating_pnl": round(fl, 4), "is_real": bool(p.get("real"))})
    closed_out = [{**c, "is_real": bool(c.get("real"))} for c in closed_docs]
    wins = [c for c in closed_docs if c.get("pnl_usdt", 0.0) > 0]
    realized = sum(c.get("pnl_usdt", 0.0) for c in closed_docs)
    return {
        "enabled": cfg.ict_enabled,
        "mode": "real" if "ict" in cfg.live_strategies else "paper",
        "real_switch_on": cfg.real_trading_enabled and "ict" in cfg.live_strategies,
        "settings": {k: getattr(cfg, k) for k in ICT_SETTING_KEYS},
        "cash": round(float(wallet.get("cash", 0.0)), 4),
        "start_capital": float(wallet.get("start_capital", cfg.ict_capital)),
        "equity": round(float(wallet.get("cash", 0.0)) + open_value, 4),
        "unrealized": round(unreal, 4),
        "realized": round(realized, 4),
        "trades": len(closed_docs),
        "win_rate": round(100.0 * len(wins) / len(closed_docs), 1) if closed_docs else None,
        "open_positions": open_out,
        "closed_positions": closed_out[:100],
        "watching": _ict_status.get("watching", {}),
        "last_scan": _ict_status.get("at"),
        "disclaimer": "Strategia sperimentale, non dimostrata dai test.",
    }


@api.put("/ict/settings")
async def ict_put_settings(body: dict[str, Any]) -> dict[str, Any]:
    cfg = await get_config()
    data = cfg.model_dump()
    for k, v in body.items():
        if k not in ICT_SETTING_KEYS:
            raise HTTPException(status_code=400, detail=f"Impostazione sconosciuta: {k}")
        data[k] = v
    try:
        new_cfg = Config(**data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Valore non valido: {e}")
    if new_cfg.ict_timeframe not in ICT_TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Timeframe ammessi: " + ", ".join(ICT_TIMEFRAMES))
    if not (1.0 <= new_cfg.ict_rr <= 5.0):
        raise HTTPException(status_code=400, detail="RR tra 1 e 5")
    if not (5.0 <= new_cfg.ict_alloc_pct <= 100.0):
        raise HTTPException(status_code=400, detail="Quota per operazione tra 5% e 100%")
    if not (1 <= new_cfg.ict_max_open <= 4):
        raise HTTPException(status_code=400, detail="Posizioni aperte massime tra 1 e 4")
    if not (1.0 <= new_cfg.ict_max_hold_h <= 168.0):
        raise HTTPException(status_code=400, detail="Durata massima tra 1 e 168 ore")
    syms = [s.strip().upper() for s in new_cfg.ict_symbols]
    if not (1 <= len(syms) <= 8) or any((not s.endswith("USDC")) or (not s.isalnum()) or len(s) > 14 for s in syms):
        raise HTTPException(status_code=400, detail="Da 1 a 8 coppie USDC (es. BTCUSDC)")
    new_cfg.ict_symbols = syms
    body = dict(body)
    if "ict_symbols" in body:
        body["ict_symbols"] = syms
    await db.config.update_one({"_id": CONFIG_ID}, {"$set": {k: getattr(new_cfg, k) for k in body}}, upsert=True)
    return {k: getattr(new_cfg, k) for k in ICT_SETTING_KEYS}


@api.post("/ict/reset")
async def ict_reset() -> dict[str, Any]:
    if await db.ict_positions.find_one({"status": "open", "real": {"$exists": True}}):
        raise HTTPException(status_code=400, detail="Ci sono posizioni ICT reali aperte: chiudile prima (pagina Trading reale).")
    cfg = await get_config()
    await db.ict_positions.delete_many({})
    await db.ict_seen.delete_many({})
    _ict_seen.clear()
    await db.ict_wallet.update_one(
        {"_id": ICT_WALLET_ID}, {"$set": {"cash": float(cfg.ict_capital), "start_capital": float(cfg.ict_capital)}}, upsert=True)
    return {"ok": True}


app.include_router(api)
