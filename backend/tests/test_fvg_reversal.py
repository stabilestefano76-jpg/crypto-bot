"""Backend tests for FVG Reversal extension (Jan 2026)."""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://divergence-trader-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


# --- Config: new fields exist with correct defaults ---
def test_config_has_reversal_fields(s):
    r = s.get(f"{API}/config", timeout=20)
    assert r.status_code == 200
    c = r.json()
    assert "score_fvg_reversal" in c
    assert "reversal_min_signals" in c
    assert "reversal_rejection_wick_ratio" in c
    assert "fvg_fill_mode" in c
    assert c["fvg_fill_mode"] in ("opposite_edge", "midpoint")


def test_config_put_persists_new_fields(s):
    orig = s.get(f"{API}/config").json()
    patched = dict(orig)
    patched["fvg_fill_mode"] = "midpoint"
    patched["reversal_min_signals"] = 1
    r = s.put(f"{API}/config", json=patched, timeout=20)
    assert r.status_code == 200
    back = s.get(f"{API}/config").json()
    assert back["fvg_fill_mode"] == "midpoint"
    assert back["reversal_min_signals"] == 1

    # restore
    r2 = s.put(f"{API}/config", json=orig, timeout=20)
    assert r2.status_code == 200


# --- Signals: max_score = sum(5 weights), reversal_signals field present ---
def test_signals_have_max_score_including_reversal(s):
    cfg = s.get(f"{API}/config").json()
    expected_max = (
        cfg["score_fvg"]
        + cfg["score_rsi_divergence"]
        + cfg["score_volume"]
        + cfg["score_ma_cross"]
        + cfg["score_fvg_reversal"]
    )
    data = s.get(f"{API}/signals?status=active&limit=50").json()
    sigs = data["signals"]
    if not sigs:
        pytest.skip("no active signals to inspect")
    fresh = [s for s in sigs if s.get("max_score", 0) >= expected_max - 1e-6]
    assert fresh, f"no fresh signals with max_score={expected_max}; all stale: {sigs}"
    for sig in fresh:
        assert "reversal_signals" in sig and isinstance(sig["reversal_signals"], list)
        assert abs(sig["max_score"] - expected_max) < 1e-6, sig
        # Regression: score gate
        assert sig["score"] >= cfg["min_score_threshold"] - 1e-9
        # If FVG Reversal is a confirmation, reversal_signals must satisfy min
        if "FVG Reversal" in sig["confirmations"]:
            assert len(sig["reversal_signals"]) >= cfg["reversal_min_signals"]


# --- Regression: ATR & structural stop unchanged for non-reversal signals ---
def test_regression_atr_and_rr(s):
    cfg = s.get(f"{API}/config").json()
    sigs = s.get(f"{API}/signals?status=active&limit=100").json()["signals"]
    if not sigs:
        pytest.skip("no active signals")
    for sig in sigs:
        assert sig["atr"] > 0
        risk = abs(sig["entry"] - sig["stop_loss"])
        reward = abs(sig["take_profit"] - sig["entry"])
        assert risk > 0
        est_rr = reward / risk
        assert est_rr >= cfg["min_rr_ratio"] - 1e-6, (sig["symbol"], est_rr)
        if "FVG Reversal" not in sig["confirmations"]:
            if sig["side"] == "long":
                assert sig["stop_loss"] < sig["fvg_bottom"], sig
            else:
                assert sig["stop_loss"] > sig["fvg_top"], sig


# --- Scan repopulates setup_debug_log; reversal_signals field on logs ---
def test_scan_repopulates_debug_log_with_reversal_signals(s):
    r = s.post(f"{API}/scan", timeout=20)
    assert r.status_code == 200
    # wait for scan to complete
    for _ in range(40):
        st = s.get(f"{API}/status").json()
        if not st.get("is_scanning"):
            break
        time.sleep(2)
    dbg = s.get(f"{API}/setup-debug/log?limit=200").json()
    assert "fail_counts" in dbg
    assert "pass_counts" in dbg
    # FVG Reversal must appear in fail_counts (it's rarely confirmed => usually fails)
    assert "FVG Reversal" in dbg["fail_counts"], dbg["fail_counts"]
    # Every log entry has reversal_signals field
    for l in dbg["logs"]:
        assert "reversal_signals" in l
        assert isinstance(l["reversal_signals"], list)


# --- Regression: other logs / endpoints still work ---
def test_regression_other_endpoints(s):
    for path in ("/stop-debug/log", "/slippage/log", "/feed/status"):
        r = s.get(f"{API}{path}", timeout=15)
        assert r.status_code == 200, path


def test_regression_paper_execute(s):
    sigs = s.get(f"{API}/signals?status=active&limit=5").json()["signals"]
    if not sigs:
        pytest.skip("no signals to execute")
    sig_id = sigs[0]["id"]
    r = s.post(f"{API}/paper/execute/{sig_id}", timeout=20)
    # Expected: 200 (opened), or 400 (max reached / duplicate / spot-short)
    assert r.status_code in (200, 400), r.text
