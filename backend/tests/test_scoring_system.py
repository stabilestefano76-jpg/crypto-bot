"""Tests for the SCORING SYSTEM (replaces rigid AND logic)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://divergence-trader-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


# --- Config: scoring fields ---
def test_config_has_scoring_fields(s):
    cfg = s.get(f"{API}/config", timeout=15).json()
    for k, expected in [
        ("score_fvg", 2.0),
        ("score_rsi_divergence", 2.0),
        ("score_volume", 1.0),
        ("score_ma_cross", 1.0),
        ("min_score_threshold", 4.0),
        ("signal_validity_candles", 5),
        ("fvg_lookback", 40),
    ]:
        assert k in cfg, f"missing {k}"
        # numeric compare (default should match)
        assert float(cfg[k]) == float(expected), f"{k} expected {expected} got {cfg[k]}"


def test_config_put_persists_scoring(s):
    cfg = s.get(f"{API}/config", timeout=15).json()
    original = cfg["min_score_threshold"]
    cfg["min_score_threshold"] = 3.0
    r = s.put(f"{API}/config", json=cfg, timeout=15)
    assert r.status_code == 200
    assert float(r.json()["min_score_threshold"]) == 3.0
    got = s.get(f"{API}/config", timeout=15).json()
    assert float(got["min_score_threshold"]) == 3.0
    # restore
    cfg["min_score_threshold"] = original
    s.put(f"{API}/config", json=cfg, timeout=15)


# --- Signals score/max_score ---
def test_signals_have_score_and_max_score(s):
    cfg = s.get(f"{API}/config", timeout=15).json()
    threshold = float(cfg["min_score_threshold"])
    expected_max = (
        float(cfg["score_fvg"]) + float(cfg["score_rsi_divergence"]) +
        float(cfg["score_volume"]) + float(cfg["score_ma_cross"])
    )
    r = s.get(f"{API}/signals?status=active&limit=100", timeout=20)
    assert r.status_code == 200
    sigs = r.json()["signals"]
    if not sigs:
        pytest.skip("no active signals to validate")
    for sig in sigs:
        assert "score" in sig and "max_score" in sig, f"missing score fields: {sig.keys()}"
        assert float(sig["score"]) >= threshold, f"score {sig['score']} < threshold {threshold}"
        assert float(sig["score"]) <= float(sig["max_score"])
        assert float(sig["max_score"]) == expected_max


def test_signals_varied_confirmations_not_all_four(s):
    """Prove AND requirement is gone: at least one active signal with <4 confirmations,
    OR a mix of different confirmation combos."""
    r = s.get(f"{API}/signals?status=active&limit=100", timeout=20)
    sigs = r.json()["signals"]
    if not sigs:
        pytest.skip("no signals")
    combos = {tuple(sorted(sig.get("confirmations", []))) for sig in sigs}
    # ATR-based structure guarantees FVG present. If AND-logic was still active,
    # every signal would carry all 4 confirmations -> only 1 combo of length 4.
    all_four = all(len(sig.get("confirmations", [])) == 4 for sig in sigs)
    assert not all_four or len(combos) > 1, (
        "All signals require all 4 confirmations - AND logic may still be present"
    )


# --- Scan + setup-debug ---
def test_scan_and_setup_debug_log(s):
    # clear log first
    s.delete(f"{API}/setup-debug/log", timeout=15)
    # trigger scan
    r = s.post(f"{API}/scan", timeout=15)
    assert r.status_code == 200
    # wait for scan to produce some setup-debug entries
    for _ in range(20):
        time.sleep(3)
        d = s.get(f"{API}/setup-debug/log", timeout=15).json()
        if d.get("count", 0) > 0:
            break
    d = s.get(f"{API}/setup-debug/log", timeout=15).json()
    for k in ["logs", "count", "fail_counts", "pass_counts", "bottleneck"]:
        assert k in d, f"missing {k}"
    if d["count"] > 0:
        entry = d["logs"][0]
        for k in ["passed", "failed", "score", "max_score", "threshold"]:
            assert k in entry
        assert isinstance(entry["passed"], list)
        assert isinstance(entry["failed"], list)
        # bottleneck must be a filter name present in fail_counts
        assert d["bottleneck"] in d["fail_counts"]


def test_setup_debug_log_delete(s):
    r = s.delete(f"{API}/setup-debug/log", timeout=15)
    assert r.status_code == 200
    d = s.get(f"{API}/setup-debug/log", timeout=15).json()
    assert d["count"] == 0
    assert d["bottleneck"] is None


# --- Regression: ATR-based SL ---
def test_signals_atr_based_stop(s):
    r = s.get(f"{API}/signals?status=active&limit=50", timeout=20).json()
    for sig in r["signals"]:
        # only new signals have atr populated
        atr = float(sig.get("atr") or 0)
        if atr <= 0:
            continue
        # stop beyond opposite FVG edge
        if sig["side"] == "long":
            assert float(sig["stop_loss"]) < float(sig["fvg_bottom"])
        else:
            assert float(sig["stop_loss"]) > float(sig["fvg_top"])
        # R:R >= 1.5
        risk = abs(float(sig["entry"]) - float(sig["stop_loss"]))
        reward = abs(float(sig["take_profit"]) - float(sig["entry"]))
        assert reward / risk >= 1.5 - 1e-6


def test_regression_endpoints(s):
    for path in ["/stop-debug/log", "/slippage/log", "/feed/status"]:
        r = s.get(f"{API}{path}", timeout=15)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


# --- Paper execute ---
def test_paper_execute(s):
    sigs = s.get(f"{API}/signals?status=active&limit=20", timeout=20).json()["signals"]
    if not sigs:
        pytest.skip("no signals")
    # pick a long signal to work regardless of spot/leverage
    cand = next((x for x in sigs if x["side"] == "long"), None) or sigs[0]
    r = s.post(f"{API}/paper/execute/{cand['id']}", timeout=20)
    # can be 200 (opened) or 400 (dup / cap)
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        pos = r.json()["position"]
        assert pos["signal_id"] == cand["id"]
        # clean up
        s.post(f"{API}/paper/positions/{pos['id']}/close", timeout=20)
