"""Tests for the NEW 'Reversal Pre-FVG' (counter_trend) strategy.

Replaces the previous counter-trend tests. Covers:
- analyze_pair_counter: LONG breakout (bearish FVG above the box)
- analyze_pair_counter: SHORT mirror (bullish FVG below the box)
- R:R gate rejects |tp2-entry|/risk < min_rr_ratio
- Direction invariant: no signal if last close doesn't break either box edge
- manage_counter_position state machine (before TP1, at TP1, +0.5% advance,
  <0.5% advance, TP2)
- Regression HTTP endpoints under strategy_mode=counter_trend
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

import pytest
import requests

sys.path.insert(0, "/app/backend")
import server  # noqa: E402


def _read_base_url() -> str:
    v = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
    if v:
        return v
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    return ""


BASE_URL = _read_base_url()
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL is not set"


# ---------------------------------------------------------------------------
# Deterministic candle builders. Candle format: [t, open, close, high, low, vol]
# ---------------------------------------------------------------------------
def _filler(n: int, start: float = 100.5) -> list[list[float]]:
    out, t, p = [], 0, start
    for k in range(n):
        o = p
        c = p + (0.1 if k % 2 else -0.1)
        h = max(o, c) + 0.2
        low = min(o, c) - 0.2
        out.append([t, o, c, h, low, 100.0])
        t += 1
        p = c
    return out


def build_long_scenario() -> list[list[float]]:
    """LONG: bearish FVG above ~98..102, tight box ~97.5..98.2, bullish
    engulfing inside, breakout close > box_high."""
    candles = _filler(60)
    t = candles[-1][0] + 1
    candles.append([t, 103.0, 102.2, 103.2, 102.0, 120.0]); t += 1
    candles.append([t, 101.0, 98.6, 101.5, 98.5, 130.0]); t += 1
    candles.append([t, 98.3, 97.8, 98.0, 97.7, 140.0]); t += 1  # bearish FVG 98..102
    candles.append([t, 98.0, 97.6, 98.1, 97.5, 90.0]); t += 1
    candles.append([t, 97.7, 97.6, 97.8, 97.5, 90.0]); t += 1
    candles.append([t, 97.55, 98.15, 98.2, 97.5, 95.0]); t += 1  # bullish engulfing
    candles.append([t, 98.2, 98.5, 98.6, 98.15, 10000.0]); t += 1  # breakout
    return candles


def build_short_scenario() -> list[list[float]]:
    """SHORT: bullish FVG below ~98..102, tight box ~101.8..102.5, bearish
    engulfing, breakout close < box_low."""
    candles = _filler(60, start=99.5)
    t = candles[-1][0] + 1
    candles.append([t, 97.0, 97.8, 98.0, 96.8, 120.0]); t += 1  # i-2 high=98.0
    candles.append([t, 98.5, 101.5, 101.8, 98.4, 130.0]); t += 1
    candles.append([t, 101.7, 102.2, 102.3, 102.0, 140.0]); t += 1  # bullish FVG 98..102
    candles.append([t, 102.0, 102.4, 102.5, 101.9, 90.0]); t += 1
    candles.append([t, 102.4, 102.4, 102.5, 101.9, 90.0]); t += 1
    candles.append([t, 102.45, 101.85, 102.5, 101.8, 95.0]); t += 1  # bearish engulfing
    candles.append([t, 101.8, 101.5, 101.85, 101.4, 10000.0]); t += 1  # breakout
    return candles


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def cfg():
    return server.Config()


@pytest.fixture
def patch_rsi_filters(monkeypatch):
    """Force RSI-based gates to pass. Values keyed off the last candle direction:
    - last close rising  -> LONG scenario  -> RSI 15 (HTF oversold), bullish divergence
    - last close falling -> SHORT scenario -> RSI 85 (HTF overbought), bearish divergence
    """

    def _rsi(closes, period=14):
        v = 15.0 if closes[-1] >= closes[-2] else 85.0
        return [v] * len(closes)

    monkeypatch.setattr(server, "rsi_wilder", _rsi)
    monkeypatch.setattr(server, "_rsi_momentum_turn", lambda rsis, side, ob, os_: True)

    def _div(closes, rsis, w):
        return "bullish" if closes[-1] >= closes[-2] else "bearish"

    monkeypatch.setattr(server, "detect_rsi_divergence", _div)


@pytest.fixture
def patch_klines(monkeypatch):
    holder = {"candles": []}

    async def _fk(symbol, tf):
        return list(holder["candles"])

    monkeypatch.setattr(server.kucoin, "get_klines", _fk)
    return holder


# ---------------------------------------------------------------------------
# analyze_pair_counter entry tests
# ---------------------------------------------------------------------------
class TestAnalyzePairCounter:
    def test_long_entry_signal(self, cfg, patch_rsi_filters, patch_klines):
        patch_klines["candles"] = build_long_scenario()
        sig = asyncio.run(server.analyze_pair_counter("BTC-USDT", "1h", cfg))
        assert sig is not None, "expected LONG signal"
        assert sig.side == "long"
        assert abs(sig.entry - 98.2) < 1e-6
        assert sig.stop_loss < 97.5
        assert abs(sig.tp2 - 102.0) < 1e-6  # far edge of bearish FVG (top)
        assert sig.entry < sig.tp1 < sig.tp2
        assert abs(sig.fvg_top - 102.0) < 1e-6
        assert abs(sig.fvg_bottom - 98.0) < 1e-6
        assert sig.strategy == "counter_trend"
        assert "Consolidation Breakout" in sig.confirmations
        assert any("Engulfing" in c or "Star" in c for c in sig.confirmations)

    def test_short_entry_signal(self, cfg, patch_rsi_filters, patch_klines):
        patch_klines["candles"] = build_short_scenario()
        sig = asyncio.run(server.analyze_pair_counter("BTC-USDT", "1h", cfg))
        assert sig is not None, "expected SHORT signal"
        assert sig.side == "short"
        assert abs(sig.entry - 101.8) < 1e-6
        assert sig.stop_loss > 102.5
        assert abs(sig.tp2 - 98.0) < 1e-6  # far edge of bullish FVG (bottom)
        assert sig.tp2 < sig.tp1 < sig.entry
        assert abs(sig.fvg_top - 102.0) < 1e-6
        assert abs(sig.fvg_bottom - 98.0) < 1e-6
        assert sig.strategy == "counter_trend"
        assert "Consolidation Breakout" in sig.confirmations
        assert any("Engulfing" in c or "Star" in c for c in sig.confirmations)

    def test_no_breakout_returns_none(self, cfg, patch_rsi_filters, patch_klines):
        candles = build_long_scenario()
        # Replace breakout candle: closes inside the box, no breakout
        candles[-1] = [candles[-1][0], 97.9, 97.95, 98.1, 97.7, 500.0]
        patch_klines["candles"] = candles
        sig = asyncio.run(server.analyze_pair_counter("BTC-USDT", "1h", cfg))
        assert sig is None

    def test_rr_gate_rejects_low_rr(self, cfg, patch_rsi_filters, patch_klines):
        patch_klines["candles"] = build_long_scenario()
        cfg.min_rr_ratio = 999.0
        sig = asyncio.run(server.analyze_pair_counter("BTC-USDT", "1h", cfg))
        assert sig is None


# ---------------------------------------------------------------------------
# manage_counter_position state machine
# ---------------------------------------------------------------------------
class _FakeCollection:
    def __init__(self, store):
        self.store = store

    async def update_one(self, q, upd):
        self.store.setdefault("updates", []).append(copy.deepcopy(upd["$set"]))
        self.store.update(upd["$set"])


@pytest.fixture
def state_env(monkeypatch, cfg):
    store: dict = {}
    monkeypatch.setattr(server.db, "paper_positions", _FakeCollection(store))

    closed: dict = {}

    async def fake_close(pos, price, outcome):
        closed["price"] = price
        closed["outcome"] = outcome
        return None

    monkeypatch.setattr(server, "close_paper_position", fake_close)

    frac_calls: list = []

    async def fake_close_fraction(pos, price, frac, outcome, set_partial=True):
        frac_calls.append((price, frac, outcome))
        return float(pos["quantity"]) * (1 - frac)

    monkeypatch.setattr(server, "_close_fraction", fake_close_fraction)

    async def fake_fees(symbol, cfg_):
        return (0.001, 0.001)

    monkeypatch.setattr(server, "get_trade_fees", fake_fees)

    last_close = {"v": 0.0}

    async def fake_last_closed(symbol, tf):
        return last_close["v"]

    monkeypatch.setattr(server, "_last_closed_close", fake_last_closed)

    return {"store": store, "closed": closed, "frac_calls": frac_calls,
            "last_close": last_close, "cfg": cfg}


def _long_pos():
    return {
        "id": "p1", "symbol": "BTC-USDT", "timeframe": "1h", "side": "long",
        "entry": 98.2, "fill_price": 98.2, "stop_loss": 97.0, "current_stop": 97.0,
        "quantity": 10.0, "tp1": 99.0, "tp2": 100.0, "partial_closed": False,
        "breakeven_active": False, "opened_at": "2026-01-01T00:00:00+00:00",
    }


class TestManageCounterPosition:
    def test_a_before_tp1_no_partial(self, state_env):
        state_env["last_close"]["v"] = 98.5
        p = asyncio.run(server.manage_counter_position(_long_pos(), 98.5, state_env["cfg"]))
        assert not p.get("partial_closed")
        assert state_env["frac_calls"] == []

    def test_b_hit_tp1_partial_65pct_stop_stays_atr(self, state_env):
        state_env["last_close"]["v"] = 99.0
        p = asyncio.run(server.manage_counter_position(_long_pos(), 99.0, state_env["cfg"]))
        assert state_env["frac_calls"], "expected partial close call at TP1"
        _, frac, outcome = state_env["frac_calls"][-1]
        assert abs(frac - 0.65) < 1e-9
        assert outcome == "tp1"
        assert p.get("partial_closed") is True
        assert state_env["store"].get("breakeven_active") is None

    def test_c_post_tp1_advance_moves_stop_to_tp1_plus_fees(self, state_env):
        pos2 = {**_long_pos(), "partial_closed": True, "quantity": 3.5,
                "breakeven_active": False}
        state_env["last_close"]["v"] = 99.4
        # 99.6 > 99.0 * (1 + 0.5%) = 99.495
        asyncio.run(server.manage_counter_position(pos2, 99.6, state_env["cfg"]))
        assert state_env["store"].get("breakeven_active") is True
        expected = 99.0 + 99.0 * (0.001 + 0.001)
        assert abs(state_env["store"]["current_stop"] - expected) < 1e-6

    def test_d_advance_below_threshold_stop_stays(self, state_env):
        pos3 = {**_long_pos(), "partial_closed": True, "quantity": 3.5,
                "breakeven_active": False}
        state_env["last_close"]["v"] = 99.1
        asyncio.run(server.manage_counter_position(pos3, 99.1, state_env["cfg"]))
        assert state_env["store"].get("breakeven_active") is None

    def test_e_hit_tp2_closes_remainder_as_win(self, state_env):
        pos4 = {**_long_pos(), "partial_closed": True, "quantity": 3.5,
                "breakeven_active": True}
        state_env["last_close"]["v"] = 100.0
        asyncio.run(server.manage_counter_position(pos4, 100.0, state_env["cfg"]))
        assert state_env["closed"].get("price") == 100.0
        assert state_env["closed"].get("outcome") == "win"


# ---------------------------------------------------------------------------
# Regression HTTP endpoints
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def http():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


class TestRegressionEndpoints:
    def test_put_config_strategy_counter_trend(self, http):
        r0 = http.get(f"{BASE_URL}/api/config", timeout=30)
        assert r0.status_code == 200
        current = r0.json()
        payload = {**current, "strategy_mode": "counter_trend"}
        r = http.put(f"{BASE_URL}/api/config", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("strategy_mode") == "counter_trend"
        r2 = http.get(f"{BASE_URL}/api/config", timeout=30)
        assert r2.status_code == 200
        assert r2.json().get("strategy_mode") == "counter_trend"

    def test_scan_endpoint_counter_trend(self, http):
        r = http.post(f"{BASE_URL}/api/scan", timeout=90)
        assert r.status_code == 200, r.text

    def test_signals_endpoint_shape(self, http):
        r = http.get(f"{BASE_URL}/api/signals", timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert "signals" in body and isinstance(body["signals"], list)
        assert "count" in body and isinstance(body["count"], int)

    def test_scoring_strategy_still_ok(self, http):
        r0 = http.get(f"{BASE_URL}/api/config", timeout=30)
        c = r0.json()
        http.put(f"{BASE_URL}/api/config",
                 json={**c, "strategy_mode": "scoring"}, timeout=30)
        r = http.post(f"{BASE_URL}/api/scan", timeout=90)
        assert r.status_code == 200

    def test_impulse_strategy_still_ok(self, http):
        r0 = http.get(f"{BASE_URL}/api/config", timeout=30)
        c = r0.json()
        http.put(f"{BASE_URL}/api/config",
                 json={**c, "strategy_mode": "impulse_fvg"}, timeout=30)
        r = http.post(f"{BASE_URL}/api/scan", timeout=90)
        assert r.status_code == 200
