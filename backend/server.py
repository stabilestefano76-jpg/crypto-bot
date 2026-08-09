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
    sl_padding_pct: float = 1.0  # % beyond FVG edge — extra drawdown margin
    max_pairs_per_scan: int = 200  # cap for MVP performance
    enabled_pairs: list[str] = Field(default_factory=list)  # empty = all matching filter
    excluded_pairs: list[str] = Field(default_factory=list)


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
    strength: int  # number of confirmations
    rsi_value: float
    volume_ratio: float
    created_at: str  # ISO string
    fvg_top: float
    fvg_bottom: float
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


class PaperPosition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    symbol: str
    timeframe: str
    side: str  # long | short
    entry: float
    stop_loss: float
    take_profit: float
    quantity: float
    risk_usdt: float
    opened_at: str


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
    cash = await get_paper_cash()
    # Equity approximation for sizing = cash (positions are marked-to-market only for display)
    risk_usdt = max(1.0, cash * pcfg.risk_per_trade_pct / 100)
    risk_per_unit = abs(signal["entry"] - signal["stop_loss"])
    if risk_per_unit <= 0:
        return None
    qty = risk_usdt / risk_per_unit
    pos = PaperPosition(
        signal_id=signal["id"],
        symbol=signal["symbol"],
        timeframe=signal["timeframe"],
        side=signal["side"],
        entry=signal["entry"],
        stop_loss=signal["stop_loss"],
        take_profit=signal["take_profit"],
        quantity=round(qty, 8),
        risk_usdt=round(risk_usdt, 2),
        opened_at=datetime.now(timezone.utc).isoformat(),
    )
    await db.paper_positions.insert_one(pos.model_dump())
    logger.info(
        "Opened paper position %s %s qty=%.6f risk=%.2f",
        pos.symbol, pos.side, pos.quantity, pos.risk_usdt,
    )
    return pos


async def close_paper_position(pos: dict[str, Any], exit_price: float, outcome: str) -> PaperTrade:
    entry = float(pos["entry"])
    qty = float(pos["quantity"])
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
    await set_paper_cash(cash + pnl)
    # Update signal outcome
    await db.signals.update_one(
        {"id": pos["signal_id"]},
        {"$set": {"outcome": outcome, "status": "closed"}},
    )
    logger.info(
        "Closed paper %s %s pnl=%.2f (%s)",
        pos["symbol"], pos["side"], pnl, outcome,
    )
    return trade


async def monitor_paper_positions() -> None:
    """Fetch current prices and close positions if SL/TP hit."""
    positions = await db.paper_positions.find({}, {"_id": 0}).to_list(1000)
    if not positions:
        return
    tickers = await kucoin.get_tickers()
    price_map: dict[str, float] = {}
    for t in tickers:
        try:
            price_map[t["symbol"]] = float(t.get("last") or 0)
        except (TypeError, ValueError):
            continue
    for pos in positions:
        price = price_map.get(pos["symbol"], 0.0)
        if price <= 0:
            continue
        if pos["side"] == "long":
            if price <= pos["stop_loss"]:
                await close_paper_position(pos, pos["stop_loss"], "loss")
            elif price >= pos["take_profit"]:
                await close_paper_position(pos, pos["take_profit"], "win")
        else:
            if price >= pos["stop_loss"]:
                await close_paper_position(pos, pos["stop_loss"], "loss")
            elif price <= pos["take_profit"]:
                await close_paper_position(pos, pos["take_profit"], "win")


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

    rsis = rsi_wilder(closes, cfg.rsi_period)
    divergence = detect_rsi_divergence(closes, rsis, cfg.pivot_window)
    if divergence is None:
        return None

    fvg = detect_fvg(highs, lows, lookback=40)
    if fvg is None:
        return None

    # Confluence: divergence direction must align with FVG direction
    side = "long" if divergence == "bullish" else "short"
    fvg_kind = fvg["kind"]
    if (side == "long" and fvg_kind != "bullish") or (
        side == "short" and fvg_kind != "bearish"
    ):
        return None

    price = closes[-1]
    # Entry at nearest FVG edge; SL beyond zone with padding
    if side == "long":
        entry = fvg["top"]  # top of bullish FVG is the low of candle[i] = upper edge above bottom
        # Ensure price is at/above FVG zone but hasn't broken far above
        if price < fvg["bottom"] or price > entry * 1.05:
            return None
        stop_loss = fvg["bottom"] * (1 - cfg.sl_padding_pct / 100)
        risk = entry - stop_loss
        if risk <= 0:
            return None
        take_profit = entry + risk * cfg.rr_ratio
    else:
        entry = fvg["bottom"]
        if price > fvg["top"] or price < entry * 0.95:
            return None
        stop_loss = fvg["top"] * (1 + cfg.sl_padding_pct / 100)
        risk = stop_loss - entry
        if risk <= 0:
            return None
        take_profit = entry - risk * cfg.rr_ratio

    confirmations = ["RSI Divergence", "FVG Zone"]
    strength = 2

    # Volume confirmation
    vol_ratio = volume_spike_ratio(vols, cfg.volume_ma_period)
    if vol_ratio >= cfg.volume_spike_multiplier:
        confirmations.append("Volume Spike")
        strength += 1
    elif cfg.require_volume_confirmation:
        return None

    # EMA alignment
    ema_f = ema(closes, cfg.ema_fast)
    ema_s = ema(closes, cfg.ema_slow)
    if ema_f[-1] is not None and ema_s[-1] is not None:
        if side == "long" and ema_f[-1] > ema_s[-1]:
            confirmations.append("EMA Trend Up")
            strength += 1
        elif side == "short" and ema_f[-1] < ema_s[-1]:
            confirmations.append("EMA Trend Down")
            strength += 1
        elif cfg.require_ma_alignment:
            return None

    return Signal(
        symbol=symbol,
        timeframe=tf,
        side=side,
        entry=round(entry, 8),
        stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8),
        rr_ratio=cfg.rr_ratio,
        confirmations=confirmations,
        strength=strength,
        rsi_value=round(rsis[-1] or 0, 2),
        volume_ratio=round(vol_ratio, 2),
        created_at=datetime.now(timezone.utc).isoformat(),
        fvg_top=round(fvg["top"], 8),
        fvg_bottom=round(fvg["bottom"], 8),
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

        async def process(sym: str) -> None:
            for tf in cfg.timeframes:
                try:
                    sig = await analyze_pair(sym, tf, cfg)
                    if sig:
                        signals_found.append(sig)
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
    """Poll prices every 60s to detect SL/TP hits on open paper positions."""
    await asyncio.sleep(10)
    while True:
        try:
            await monitor_paper_positions()
        except Exception as e:  # noqa: BLE001
            logger.exception("Paper monitor error: %s", e)
        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# FastAPI app & routes
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    scan_task = asyncio.create_task(scheduler_loop())
    monitor_task = asyncio.create_task(paper_monitor_loop())
    yield
    scan_task.cancel()
    monitor_task.cancel()
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
    enriched: list[dict[str, Any]] = []
    for p in positions:
        cur = price_map.get(p["symbol"], p["entry"])
        if p["side"] == "long":
            pnl = (cur - p["entry"]) * p["quantity"]
        else:
            pnl = (p["entry"] - cur) * p["quantity"]
        pnl_pct = (pnl / (p["entry"] * p["quantity"])) * 100 if p["entry"] * p["quantity"] > 0 else 0
        unrealized += pnl
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
        "positions": enriched,
    }


@api.get("/paper/trades")
async def paper_trades(limit: int = 100) -> dict[str, Any]:
    cursor = db.paper_trades.find({}, {"_id": 0}).sort("closed_at", -1).limit(limit)
    trades = await cursor.to_list(length=limit)
    return {"trades": trades, "count": len(trades)}


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
