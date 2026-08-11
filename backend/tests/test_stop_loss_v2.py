"""KuSignal - stop-loss overhaul (ATR-based, FVG-beyond, R:R gate, debug log) tests."""
import os
import time
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


# --- Config: new fields present & persistable ---
NEW_FIELDS = {
    "atr_period": 14,
    "atr_sl_multiplier": 1.5,
    "min_rr_ratio": 1.5,
    "premature_lookahead": 20,
    "sl_padding_pct": 0.3,
}


def test_config_has_new_fields(s):
    r = s.get(f"{API}/config", timeout=15)
    assert r.status_code == 200
    cfg = r.json()
    for k, default in NEW_FIELDS.items():
        assert k in cfg, f"missing {k}"
    # Fields present with sane numeric values (defaults may drift due to PUT persistence)
    assert isinstance(cfg["atr_period"], int) and cfg["atr_period"] > 0
    assert float(cfg["atr_sl_multiplier"]) >= 1.0
    assert float(cfg["min_rr_ratio"]) >= 1.0
    assert isinstance(cfg["premature_lookahead"], int) and cfg["premature_lookahead"] > 0
    assert float(cfg["sl_padding_pct"]) >= 0.0


def test_config_put_persists_new_fields(s):
    r = s.get(f"{API}/config", timeout=15).json()
    original = {k: r[k] for k in NEW_FIELDS}
    r["atr_period"] = 10
    r["atr_sl_multiplier"] = 2.0
    r["min_rr_ratio"] = 1.8
    r["premature_lookahead"] = 25
    r["sl_padding_pct"] = 0.5
    rr = s.put(f"{API}/config", json=r, timeout=15)
    assert rr.status_code == 200
    out = rr.json()
    assert out["atr_period"] == 10
    assert out["atr_sl_multiplier"] == 2.0
    assert out["min_rr_ratio"] == 1.8
    assert out["premature_lookahead"] == 25
    assert float(out["sl_padding_pct"]) == 0.5
    # verify persistence via re-GET
    check = s.get(f"{API}/config", timeout=15).json()
    assert check["atr_period"] == 10
    assert check["min_rr_ratio"] == 1.8
    # restore
    for k, v in original.items():
        r[k] = v
    s.put(f"{API}/config", json=r, timeout=15)


# --- Signals: ATR fields, stop distance, beyond FVG edge, R:R gate ---
def _fetch_signals(s):
    r = s.get(f"{API}/signals?status=active&limit=100", timeout=25)
    assert r.status_code == 200
    return r.json().get("signals", [])


def test_signals_have_atr_fields(s):
    sigs = _fetch_signals(s)
    if not sigs:
        # try triggering a scan and wait
        s.post(f"{API}/scan", timeout=15)
        time.sleep(30)
        sigs = _fetch_signals(s)
    if not sigs:
        pytest.skip("No active signals to validate")
    # Filter to signals produced by this iteration (have atr field). Old
    # active signals from previous iterations may pre-date the ATR overhaul.
    new_sigs = [x for x in sigs if "atr" in x and x.get("atr")]
    assert new_sigs, (
        f"No signal has atr field. Total active={len(sigs)}. "
        f"Legacy signals without atr: {[x['id'] for x in sigs if 'atr' not in x][:5]}"
    )
    for sig in new_sigs:
        assert sig["atr"] > 0, f"atr must be > 0: {sig}"
        assert sig["atr_multiplier"] >= 1.5


def test_signals_stop_distance_ge_1p5_atr(s):
    sigs = [x for x in _fetch_signals(s) if x.get("atr")]
    if not sigs:
        pytest.skip("No new-style signals with atr")
    for sig in sigs:
        dist = abs(sig["entry"] - sig["stop_loss"])
        ratio = dist / sig["atr"]
        assert ratio >= 1.5 - 1e-6, (
            f"{sig['symbol']} {sig['side']} stop distance {ratio:.3f}xATR < 1.5"
        )


def test_signals_stop_beyond_opposite_fvg_edge(s):
    sigs = [x for x in _fetch_signals(s) if x.get("atr")]
    if not sigs:
        pytest.skip("No new-style signals")
    for sig in sigs:
        if sig["side"] == "long":
            assert sig["stop_loss"] < sig["fvg_bottom"], (
                f"LONG {sig['symbol']}: SL {sig['stop_loss']} not below fvg_bottom {sig['fvg_bottom']}"
            )
        else:
            assert sig["stop_loss"] > sig["fvg_top"], (
                f"SHORT {sig['symbol']}: SL {sig['stop_loss']} not above fvg_top {sig['fvg_top']}"
            )


def test_signals_rr_gate(s):
    sigs = [x for x in _fetch_signals(s) if x.get("atr")]
    if not sigs:
        pytest.skip("No new-style signals")
    for sig in sigs:
        risk = abs(sig["entry"] - sig["stop_loss"])
        reward = abs(sig["take_profit"] - sig["entry"])
        assert risk > 0
        rr = reward / risk
        assert rr >= 1.5 - 1e-6, f"{sig['symbol']} R:R={rr:.3f} < 1.5"


# --- Scan endpoint still works (non-blocking) ---
def test_trigger_scan(s):
    r = s.post(f"{API}/scan", timeout=15)
    assert r.status_code == 200
    assert r.json().get("started") is True


# --- Stop-debug log endpoint ---
def test_stop_debug_log_schema(s):
    r = s.get(f"{API}/stop-debug/log", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ["logs", "count", "premature", "valid", "pending",
              "premature_rate", "avg_stop_distance_atr"]:
        assert k in d, f"missing {k}"
    assert isinstance(d["logs"], list)
    assert isinstance(d["count"], int)
    # counts consistent
    assert d["count"] == len(d["logs"])
    assert d["premature"] + d["valid"] + d["pending"] <= d["count"]


# --- Regression: slippage log and feed status ---
def test_slippage_log(s):
    r = s.get(f"{API}/slippage/log", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ["logs", "count", "total_abs_slippage_usdt", "avg_slippage_pct"]:
        assert k in d


def test_feed_status(s):
    r = s.get(f"{API}/feed/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ["ws_connected", "subscribed", "cached_symbols"]:
        assert k in d


# --- Paper execute flow: opens a position; verify schema (SL close may not trigger during test window) ---
def test_paper_execute_and_stop_debug_pending(s):
    sigs = _fetch_signals(s)
    if not sigs:
        pytest.skip("No active signals available")
    # Reset to ensure clean state (also avoids one-position-per-pair blocking)
    s.post(f"{API}/paper/reset", timeout=15)
    sig = sigs[0]
    # For spot mode, skip shorts
    if sig["side"] == "short":
        long_sigs = [x for x in sigs if x["side"] == "long"]
        if not long_sigs:
            pytest.skip("Only shorts available; spot mode blocks them")
        sig = long_sigs[0]
    r = s.post(f"{API}/paper/execute/{sig['id']}", timeout=25)
    if r.status_code == 400:
        pytest.skip(f"Cannot open position: {r.json()}")
    assert r.status_code == 200
    pos = r.json()["position"]
    assert pos["symbol"] == sig["symbol"]
    assert pos["stop_loss"] == sig["stop_loss"]
    # Verify portfolio reflects the open position
    p = s.get(f"{API}/paper/portfolio", timeout=15).json()
    assert p["open_positions_count"] >= 1
    # Cleanup: manual close (will produce a trade regardless of price direction)
    close = s.post(f"{API}/paper/positions/{pos['id']}/close", timeout=25)
    assert close.status_code == 200
    # Manual close won't create a stop_debug_log entry (only true SL hits do);
    # confirm endpoint still responds sanely
    log = s.get(f"{API}/stop-debug/log", timeout=15).json()
    assert isinstance(log["logs"], list)
