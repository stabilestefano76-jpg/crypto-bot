"""KuSignal Bot backend integration tests."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://divergence-trader-1.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


# --- basic ---
def test_root(s):
    r = s.get(f"{API}/", timeout=15)
    assert r.status_code == 200
    assert r.json().get("service") == "kusignal-bot"


def test_status(s):
    r = s.get(f"{API}/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ["last_scan_at", "last_scanned_pairs", "last_signals_found", "is_scanning"]:
        assert k in d


# --- config ---
def test_config_get_and_put(s):
    r = s.get(f"{API}/config", timeout=15)
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["quote_filter"] == "USDT"
    original = cfg["max_pairs_per_scan"]
    cfg["max_pairs_per_scan"] = 150
    r = s.put(f"{API}/config", json=cfg, timeout=15)
    assert r.status_code == 200
    assert r.json()["max_pairs_per_scan"] == 150
    # verify persistence
    r = s.get(f"{API}/config", timeout=15)
    assert r.json()["max_pairs_per_scan"] == 150
    # restore
    cfg["max_pairs_per_scan"] = original
    s.put(f"{API}/config", json=cfg, timeout=15)


# --- signals ---
def test_signals_active(s):
    r = s.get(f"{API}/signals?status=active", timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert "signals" in d and "count" in d
    assert isinstance(d["signals"], list)
    if d["signals"]:
        sig = d["signals"][0]
        for k in ["id", "symbol", "timeframe", "side", "entry", "stop_loss", "take_profit", "confirmations"]:
            assert k in sig
        assert sig["side"] in ("long", "short")
        # RSI Divergence + FVG confluence expected
        confs = " ".join(sig["confirmations"])
        assert "RSI" in confs and "FVG" in confs


def test_signal_by_id_and_404(s):
    r = s.get(f"{API}/signals?status=active", timeout=20).json()
    if r["signals"]:
        sid = r["signals"][0]["id"]
        rr = s.get(f"{API}/signals/{sid}", timeout=15)
        assert rr.status_code == 200
        assert rr.json()["id"] == sid
    rr = s.get(f"{API}/signals/does-not-exist-xyz", timeout=15)
    assert rr.status_code == 404


def test_signals_side_filter(s):
    r = s.get(f"{API}/signals?side=long&status=all", timeout=20)
    assert r.status_code == 200
    for sig in r.json()["signals"]:
        assert sig["side"] == "long"


# --- candles ---
def test_candles_btc(s):
    r = s.get(f"{API}/candles/BTC-USDT?timeframe=1h", timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["symbol"] == "BTC-USDT"
    assert d["timeframe"] == "1h"
    assert isinstance(d["candles"], list)
    assert len(d["candles"]) > 50
    c = d["candles"][0]
    for k in ["t", "o", "c", "h", "l", "v"]:
        assert k in c
    assert isinstance(d["rsi"], list)
    assert len(d["rsi"]) == len(d["candles"])


# --- scan ---
def test_trigger_scan(s):
    r = s.post(f"{API}/scan", timeout=15)
    assert r.status_code == 200
    assert r.json().get("started") is True


# --- history ---
def test_history_stats(s):
    r = s.get(f"{API}/history/stats", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ["total", "active", "wins", "losses", "win_rate"]:
        assert k in d
    assert d["total"] >= d["active"]


# --- pairs ---
def test_pairs(s):
    r = s.get(f"{API}/pairs?limit=20", timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert "pairs" in d
    assert len(d["pairs"]) > 0
    # sorted by volume desc
    vols = [p["volume_24h_usdt"] for p in d["pairs"]]
    assert vols == sorted(vols, reverse=True)
    # all USDT
    for p in d["pairs"]:
        assert p["symbol"].endswith("-USDT")
