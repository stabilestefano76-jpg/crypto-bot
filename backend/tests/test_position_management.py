"""Backend tests for the additive position-management modules.

Covers:
- GET/PUT /api/config new fields with defaults
- Regression endpoints (signals, setup-debug, stop-debug, slippage, feed/status)
- Paper mode set to spot, reset, execute a signal, portfolio schema
"""
from __future__ import annotations

import os
import time

import pytest
import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
if not BASE:
    BASE = "https://divergence-trader-1.preview.emergentagent.com"

API = f"{BASE}/api"

NEW_CONFIG_DEFAULTS = {
    "timeout_15m": 14,
    "timeout_1h": 7,
    "timeout_4h": 5,
    "timeout_1d": 3,
    "timeout_min_r": 0.3,
    "breakeven_safety_pct": 0.05,
    "default_fee_rate": 0.001,
    "trailing_activation_r": 1.0,
    "trailing_atr_mult": 1.2,
    "partial_close_enabled": True,
    "partial_close_r": 1.0,
    "partial_close_pct": 35.0,
    "liq_min_distance_pct": 25.0,
}


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


# ---------- Config ----------
class TestConfigNewFields:
    def test_get_config_has_new_defaults(self, s):
        r = s.get(f"{API}/config", timeout=20)
        assert r.status_code == 200
        cfg = r.json()
        for k, v in NEW_CONFIG_DEFAULTS.items():
            assert k in cfg, f"missing config key: {k}"
            if isinstance(v, float):
                assert abs(float(cfg[k]) - v) < 1e-6, f"{k}={cfg[k]} expected {v}"
            else:
                assert cfg[k] == v, f"{k}={cfg[k]} expected {v}"

    def test_put_config_persists_new_field(self, s):
        r = s.get(f"{API}/config", timeout=20)
        assert r.status_code == 200
        cfg = r.json()
        original = cfg["timeout_1h"]
        cfg["timeout_1h"] = 6
        r2 = s.put(f"{API}/config", json=cfg, timeout=20)
        assert r2.status_code == 200, r2.text
        assert r2.json()["timeout_1h"] == 6
        # GET-verify persistence
        r3 = s.get(f"{API}/config", timeout=20)
        assert r3.status_code == 200
        assert r3.json()["timeout_1h"] == 6
        # Restore
        cfg["timeout_1h"] = original
        s.put(f"{API}/config", json=cfg, timeout=20)


# ---------- Regression endpoints ----------
class TestRegressionEndpoints:
    def test_setup_debug_log(self, s):
        r = s.get(f"{API}/setup-debug/log", timeout=20)
        assert r.status_code == 200

    def test_stop_debug_log(self, s):
        r = s.get(f"{API}/stop-debug/log", timeout=20)
        assert r.status_code == 200

    def test_slippage_log(self, s):
        r = s.get(f"{API}/slippage/log", timeout=20)
        assert r.status_code == 200

    def test_feed_status(self, s):
        r = s.get(f"{API}/feed/status", timeout=20)
        assert r.status_code == 200

    def test_signals_schema_unchanged(self, s):
        r = s.get(f"{API}/signals", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "signals" in data
        if data["signals"]:
            sig = data["signals"][0]
            for k in ("score", "max_score", "atr", "reversal_signals"):
                assert k in sig, f"missing signal field {k}"
            assert float(sig["max_score"]) == 8.0
            assert float(sig["atr"]) > 0


# ---------- Paper flow ----------
class TestPaperFlowManagementSchema:
    def _find_long_1h_signal(self, s):
        r = s.get(f"{API}/signals?timeframe=1h&side=long&status=active", timeout=30)
        assert r.status_code == 200
        return r.json()["signals"]

    def test_set_spot_reset_and_open(self, s):
        # Ensure we can reset first (removes open positions -> allows switching mode)
        r0 = s.post(f"{API}/paper/reset", timeout=20)
        assert r0.status_code == 200
        # Set spot mode
        r = s.post(f"{API}/paper/trading-mode", json={"trading_mode": "spot"}, timeout=20)
        assert r.status_code == 200, r.text
        assert r.json()["trading_mode"] == "spot"
        # Reset (again for cleanliness)
        r2 = s.post(f"{API}/paper/reset", timeout=20)
        assert r2.status_code == 200

        # Try to find a 1h long signal, else trigger a scan and retry
        signals = self._find_long_1h_signal(s)
        if not signals:
            s.post(f"{API}/scan", timeout=20)
            time.sleep(25)
            signals = self._find_long_1h_signal(s)
        if not signals:
            pytest.skip("No 1h long signals available to execute in this window")

        sig = signals[0]
        r3 = s.post(f"{API}/paper/execute/{sig['id']}", timeout=30)
        assert r3.status_code == 200, r3.text
        pos = r3.json()["position"]
        # New management fields present with correct defaults
        assert "current_stop" in pos and pos["current_stop"] > 0
        assert abs(pos["current_stop"] - pos["stop_loss"]) < 1e-6, \
            f"current_stop {pos['current_stop']} != stop_loss {pos['stop_loss']}"
        assert "initial_risk" in pos and pos["initial_risk"] > 0
        expected_ir = abs(pos["fill_price"] - pos["stop_loss"])
        assert abs(pos["initial_risk"] - expected_ir) < max(1e-6, expected_ir * 1e-4)
        assert pos.get("breakeven_active") is False
        assert pos.get("trailing_active") is False
        assert pos.get("partial_closed") is False

        # Portfolio endpoint returns the same fields
        r4 = s.get(f"{API}/paper/portfolio", timeout=20)
        assert r4.status_code == 200
        portf = r4.json()
        positions = portf.get("positions", [])
        assert positions, "expected at least one open position"
        p0 = next((p for p in positions if p["id"] == pos["id"]), positions[0])
        for k in ("current_stop", "breakeven_active", "trailing_active", "partial_closed"):
            assert k in p0, f"portfolio position missing field {k}"
        assert abs(p0["current_stop"] - p0["stop_loss"]) < 1e-6

        # Cleanup - close/reset
        s.post(f"{API}/paper/reset", timeout=20)
