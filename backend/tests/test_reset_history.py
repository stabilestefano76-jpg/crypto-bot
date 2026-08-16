"""Backend tests for PART 3: Reset Signal History feature.

Verifies DELETE /api/signals clears all signals and does not touch other
collections (Portfolio, config).
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


class TestSignalReset:
    def test_delete_signals_returns_ok_with_count(self, api_client):
        r = api_client.delete(f"{BASE_URL}/api/signals")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert isinstance(body.get("deleted"), int)
        assert body["deleted"] >= 0

    def test_signals_all_count_is_zero_after_delete(self, api_client):
        api_client.delete(f"{BASE_URL}/api/signals")
        r = api_client.get(f"{BASE_URL}/api/signals", params={"status": "all"})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["signals"] == []

    def test_history_stats_zero_after_delete(self, api_client):
        api_client.delete(f"{BASE_URL}/api/signals")
        r = api_client.get(f"{BASE_URL}/api/history/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["active"] == 0
        assert body["wins"] == 0
        assert body["losses"] == 0

    def test_delete_is_idempotent(self, api_client):
        api_client.delete(f"{BASE_URL}/api/signals")
        r = api_client.delete(f"{BASE_URL}/api/signals")
        assert r.status_code == 200
        assert r.json()["deleted"] == 0


class TestRegressionUnaffected:
    def test_portfolio_endpoint_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/paper/portfolio")
        assert r.status_code == 200
        body = r.json()
        # Portfolio structure should still be intact
        for k in ("initial_capital", "cash", "equity", "positions"):
            assert k in body

    def test_config_endpoint_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/config")
        assert r.status_code == 200
        # A few well-known keys must exist
        cfg = r.json()
        for k in ("scan_interval_minutes", "timeframes", "min_score_threshold"):
            assert k in cfg

    def test_paper_config_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/paper/config")
        assert r.status_code == 200
        for k in ("initial_capital", "risk_per_trade_pct", "trading_mode"):
            assert k in r.json()

    def test_status_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/status")
        assert r.status_code == 200
        assert "is_scanning" in r.json()

    def test_setup_debug_endpoint_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/setup-debug/log")
        assert r.status_code == 200

    def test_slippage_log_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/slippage/log")
        assert r.status_code == 200

    def test_feed_status_still_ok(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/feed/status")
        assert r.status_code == 200
        assert "ws_connected" in r.json()
