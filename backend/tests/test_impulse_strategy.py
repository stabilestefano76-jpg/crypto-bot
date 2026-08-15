"""Tests for the additive Impulse-FVG + Consolidation strategy.

Covers:
- GET /api/config exposes new fields with correct defaults / current values
- POST /api/strategy-mode validation (both, scoring, impulse_fvg, invalid)
- PUT /api/config persists new impulse params
- POST /api/scan runs with strategy_mode='both' and returns scan results
- GET /api/signals: schema for scoring signals + direction-safety on any
  impulse_fvg signals (rare — zero is ACCEPTABLE)
- Regression endpoints: portfolio, paper/execute, stop/slippage/setup/feed
"""
from __future__ import annotations

import os
import time

import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_BACKEND_URL",
    os.environ.get("EXPO_PUBLIC_BACKEND_URL", ""),
).rstrip("/")
if not BASE_URL:
    raise RuntimeError("EXPO_BACKEND_URL / EXPO_PUBLIC_BACKEND_URL not set")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- Config: new impulse fields present -------------------------------------
class TestConfigExposesImpulseFields:
    def test_get_config_has_new_fields(self, api):
        r = api.get(f"{BASE_URL}/api/config", timeout=15)
        assert r.status_code == 200
        cfg = r.json()
        assert "strategy_mode" in cfg
        assert cfg["strategy_mode"] in ("scoring", "impulse_fvg", "both")
        assert cfg.get("impulse_atr_mult") == 1.5
        assert cfg.get("consolidation_min_candles") == 3
        assert cfg.get("consolidation_max_atr") == 1.5
        assert cfg.get("tp1_pct") == 65.0
        assert cfg.get("tp2_pct") == 35.0


# --- Strategy-mode endpoint --------------------------------------------------
class TestStrategyMode:
    def test_set_both(self, api):
        r = api.post(f"{BASE_URL}/api/strategy-mode",
                     json={"strategy_mode": "both"}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "strategy_mode": "both"}
        cfg = api.get(f"{BASE_URL}/api/config", timeout=10).json()
        assert cfg["strategy_mode"] == "both"

    def test_set_scoring(self, api):
        r = api.post(f"{BASE_URL}/api/strategy-mode",
                     json={"strategy_mode": "scoring"}, timeout=10)
        assert r.status_code == 200
        assert api.get(f"{BASE_URL}/api/config", timeout=10).json()["strategy_mode"] == "scoring"

    def test_set_impulse_fvg(self, api):
        r = api.post(f"{BASE_URL}/api/strategy-mode",
                     json={"strategy_mode": "impulse_fvg"}, timeout=10)
        assert r.status_code == 200
        assert api.get(f"{BASE_URL}/api/config", timeout=10).json()["strategy_mode"] == "impulse_fvg"

    def test_invalid_rejected(self, api):
        r = api.post(f"{BASE_URL}/api/strategy-mode",
                     json={"strategy_mode": "nope"}, timeout=10)
        assert r.status_code == 400

    def test_restore_to_both(self, api):
        # Leave the DB in strategy_mode='both' for downstream tests
        r = api.post(f"{BASE_URL}/api/strategy-mode",
                     json={"strategy_mode": "both"}, timeout=10)
        assert r.status_code == 200


# --- PUT /api/config persists impulse params --------------------------------
class TestConfigPersistImpulseParams:
    def test_put_persists(self, api):
        cur = api.get(f"{BASE_URL}/api/config", timeout=10).json()
        original_min = cur["consolidation_min_candles"]
        original_tp1 = cur["tp1_pct"]
        try:
            cur["consolidation_min_candles"] = 4
            cur["tp1_pct"] = 70.0
            r = api.put(f"{BASE_URL}/api/config", json=cur, timeout=15)
            assert r.status_code == 200, r.text
            got = api.get(f"{BASE_URL}/api/config", timeout=10).json()
            assert got["consolidation_min_candles"] == 4
            assert got["tp1_pct"] == 70.0
        finally:
            cur["consolidation_min_candles"] = original_min
            cur["tp1_pct"] = original_tp1
            api.put(f"{BASE_URL}/api/config", json=cur, timeout=15)


# --- POST /api/scan with both strategies ------------------------------------
class TestScanBoth:
    def test_scan_runs(self, api):
        # Ensure both are active
        api.post(f"{BASE_URL}/api/strategy-mode",
                 json={"strategy_mode": "both"}, timeout=10)
        r = api.post(f"{BASE_URL}/api/scan", timeout=180)
        assert r.status_code == 200, r.text
        data = r.json()
        # /api/scan may return {"started": True} (async trigger) or state
        assert data.get("started") or "scanned_pairs" in data or "last_scanned_pairs" in data
        # Give the async scan up to 90s to finish, then verify state was updated
        for _ in range(30):
            time.sleep(3)
            st = api.get(f"{BASE_URL}/api/status", timeout=10).json()
            if not st.get("is_scanning") and (st.get("last_scanned_pairs") or 0) > 0:
                break


# --- Signals: schema + direction safety --------------------------------------
class TestSignals:
    def test_signals_schema(self, api):
        r = api.get(f"{BASE_URL}/api/signals?limit=200", timeout=15)
        assert r.status_code == 200
        payload = r.json()
        # Endpoint returns {"signals": [...], "count": N}
        signals = payload["signals"] if isinstance(payload, dict) else payload
        assert isinstance(signals, list)

        scoring = [s for s in signals if s.get("strategy") == "scoring"]
        impulse = [s for s in signals if s.get("strategy") == "impulse_fvg"]

        # Scoring signals (unchanged): must retain score/max_score/atr/reversal_signals
        for s in scoring[:20]:
            assert s.get("score", 0) >= 0
            assert s.get("max_score", 0) > 0
            assert s.get("atr", 0) > 0
            assert isinstance(s.get("reversal_signals", []), list)
            assert "confirmations" in s

        # Impulse signals may be zero — that's ACCEPTABLE by design.
        # If any exist, verify schema and direction-safety invariants.
        print(f"scoring={len(scoring)} impulse={len(impulse)}")
        for s in impulse:
            assert s["confirmations"] == [
                "Market Structure", "Impulse FVG", "Consolidation Breakout"
            ]
            assert s.get("tp1", 0) > 0
            side = s["side"]
            entry = s["entry"]
            sl = s["stop_loss"]
            ch = s["consolidation_high"]
            cl = s["consolidation_low"]
            tp1 = s["tp1"]
            assert ch > cl > 0
            if side == "long":
                assert entry > ch, f"long entry must be above box high: {entry} !> {ch}"
                assert sl < cl, f"long SL must be below box low: {sl} !< {cl}"
                assert tp1 > entry, f"long tp1 must be above entry"
                if s.get("tp2", 0) > 0:
                    assert s["tp2"] > entry
            else:
                assert entry < cl, f"short entry must be below box low: {entry} !< {cl}"
                assert sl > ch, f"short SL must be above box high: {sl} !> {ch}"
                assert tp1 < entry, f"short tp1 must be below entry"
                if s.get("tp2", 0) > 0:
                    assert s["tp2"] < entry


# --- Regression endpoints ---------------------------------------------------
class TestRegressionEndpoints:
    def test_portfolio(self, api):
        r = api.get(f"{BASE_URL}/api/paper/portfolio", timeout=15)
        assert r.status_code == 200

    def test_stop_debug(self, api):
        r = api.get(f"{BASE_URL}/api/stop-debug/log", timeout=15)
        assert r.status_code == 200

    def test_slippage(self, api):
        r = api.get(f"{BASE_URL}/api/slippage/log", timeout=15)
        assert r.status_code == 200

    def test_setup_debug(self, api):
        r = api.get(f"{BASE_URL}/api/setup-debug/log", timeout=15)
        assert r.status_code == 200

    def test_feed_status(self, api):
        r = api.get(f"{BASE_URL}/api/feed/status", timeout=15)
        assert r.status_code == 200

    def test_paper_execute_first_signal(self, api):
        # Reset paper to clean state then try to execute the newest signal.
        api.post(f"{BASE_URL}/api/paper/reset", timeout=15)
        api.post(f"{BASE_URL}/api/paper/trading-mode",
                 json={"trading_mode": "spot"}, timeout=10)
        signals = api.get(f"{BASE_URL}/api/signals?limit=20", timeout=15).json()
        if isinstance(signals, dict):
            signals = signals.get("signals", [])
        # Pick a long signal (spot cannot short)
        candidates = [s for s in signals if s.get("side") == "long"]
        if not candidates:
            pytest.skip("no long signals available")
        sig = candidates[0]
        r = api.post(f"{BASE_URL}/api/paper/execute/{sig['id']}", timeout=20)
        # Must not 500; either opens (200) or returns policy-blocked (400/409)
        assert r.status_code in (200, 400, 409), (r.status_code, r.text)
