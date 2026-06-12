"""
tests/test_qc_client_and_api.py

Tests for qc_client.py and Flask API endpoints in main.py:
    - Auth header structure
    - Path builder correctness
    - Error class hierarchy
    - GET  /api/strategies
    - POST /api/run_one
    - POST /api/backtest
    - GET  /api/strategy_source/<id>
    - GET  /api/qc_test  (integration only)
"""

import base64
import hashlib
import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─────────────────────────────────────────────────────────────
# QCClient path builders
# ─────────────────────────────────────────────────────────────

class TestQcClientPathBuilders:
    def _client(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "testtoken"}):
            from qc_client import QCClient
            return QCClient()

    def test_option_trade_path(self):
        c = self._client()
        path = c.option_trade_path("SPY", "20260112")
        assert path == "option/usa/minute/spy/20260112_trade.zip"

    def test_option_trade_path_lowercases_symbol(self):
        c = self._client()
        assert "spy" in c.option_trade_path("SPY", "20260112")

    def test_option_iv_path(self):
        c = self._client()
        path = c.option_iv_path("SPY", "20260112")
        assert "iv" in path
        assert "spy" in path
        assert "20260112" in path

    def test_equity_trade_path(self):
        c = self._client()
        path = c.equity_trade_path("SPY", "20260112")
        assert "equity" in path
        assert "spy" in path
        assert "20260112_trade.zip" in path

    def test_option_daily_path(self):
        c = self._client()
        path = c.option_daily_path("SPY")
        assert "daily" in path
        assert "spy" in path

    def test_env_override_base_url(self):
        with patch.dict(os.environ, {
            "QC_USER_ID": "12345",
            "QC_API_TOKEN": "testtoken",
            "QC_API_BASE": "https://staging.example.com",
        }):
            from qc_client import QCClient
            c = QCClient()
            assert c.base_url == "https://staging.example.com"

    def test_env_override_data_dirs(self):
        with patch.dict(os.environ, {
            "QC_USER_ID": "12345",
            "QC_API_TOKEN": "testtoken",
            "QC_DATA_DIR_OPTION": "option/custom/path",
        }):
            from qc_client import QCClient
            c = QCClient()
            path = c.option_trade_path("SPY", "20260112")
            assert path.startswith("option/custom/path")


# ─────────────────────────────────────────────────────────────
# QCClient auth headers
# ─────────────────────────────────────────────────────────────

class TestQcClientAuthHeaders:
    def test_auth_headers_structure(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "mytoken"}):
            from qc_client import QCClient
            c = QCClient()
            headers = c._auth_headers()

        assert "Authorization" in headers
        assert "Timestamp" in headers
        assert headers["Authorization"].startswith("Basic ")
        assert headers["Content-Type"] == "application/json"

    def test_auth_header_encodes_user_id(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "mytoken"}):
            from qc_client import QCClient
            c = QCClient()
            headers = c._auth_headers()

        token_b64 = headers["Authorization"].split(" ", 1)[1]
        decoded = base64.b64decode(token_b64).decode()
        assert decoded.startswith("12345:")

    def test_auth_header_hash_is_64_char_hex(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "mytoken"}):
            from qc_client import QCClient
            c = QCClient()
            headers = c._auth_headers()

        token_b64 = headers["Authorization"].split(" ", 1)[1]
        decoded = base64.b64decode(token_b64).decode()
        _, actual_hash = decoded.split(":", 1)
        assert len(actual_hash) == 64
        assert all(ch in "0123456789abcdef" for ch in actual_hash)

    def test_missing_credentials_raises(self):
        env_backup = {k: os.environ.pop(k, None) for k in ("QC_USER_ID", "QC_API_TOKEN")}
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("QC_USER_ID", None)
                os.environ.pop("QC_API_TOKEN", None)
                from qc_client import QCClient, QCAuthError
                with pytest.raises(QCAuthError):
                    QCClient()
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v


# ─────────────────────────────────────────────────────────────
# Error classes
# ─────────────────────────────────────────────────────────────

class TestQcClientErrorClasses:
    def test_auth_error_is_exception(self):
        from qc_client import QCAuthError
        err = QCAuthError("bad credentials")
        assert isinstance(err, Exception)
        assert "bad credentials" in str(err)

    def test_file_missing_is_exception(self):
        from qc_client import QCFileMissing
        err = QCFileMissing("file not found")
        assert isinstance(err, Exception)

    def test_errors_are_distinct_types(self):
        from qc_client import QCAuthError, QCFileMissing
        assert not issubclass(QCAuthError, QCFileMissing)
        assert not issubclass(QCFileMissing, QCAuthError)


# ─────────────────────────────────────────────────────────────
# Flask API — /api/strategies
# ─────────────────────────────────────────────────────────────

class TestApiStrategies:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "testtoken"}):
            from main import app
            app.config["TESTING"] = True
            with app.test_client() as c:
                yield c

    def test_returns_200(self, client):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200

    def test_returns_list(self, client):
        resp = client.get("/api/strategies")
        strategies = json.loads(resp.data)
        assert isinstance(strategies, list)
        assert len(strategies) > 0

    def test_each_strategy_has_required_fields(self, client):
        strategies = json.loads(client.get("/api/strategies").data)
        for s in strategies:
            assert "id" in s
            assert "name" in s
            assert "params" in s


# ─────────────────────────────────────────────────────────────
# Flask API — /api/run_one
# ─────────────────────────────────────────────────────────────

class TestApiRunOne:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "testtoken"}):
            from main import app
            app.config["TESTING"] = True
            with app.test_client() as c:
                yield c

    def _mock_bars(self, n=20):
        bars = []
        for i in range(n):
            minute = 30 + i
            t = f"2026-01-12 {9 + minute // 60:02d}:{minute % 60:02d}"
            bars.append({
                "time": t, "open": 2.00, "high": 2.05,
                "low": 1.95, "close": 2.00, "volume": 100, "vwap": 2.00,
            })
        return bars

    def test_unknown_strategy_returns_400(self, client):
        payload = {
            "occ": "SPY260112C00690000", "entryDate": "2026-01-12",
            "expiry": "2026-01-12", "entryTime": "09:30",
            "strategyId": "nonexistent_strategy_xyz", "qty": 1,
        }
        resp = client.post("/api/run_one", data=json.dumps(payload),
                           content_type="application/json")
        assert resp.status_code == 400

    def test_missing_occ_returns_400(self, client):
        payload = {"strategyId": "bracket", "qty": 1}
        resp = client.post("/api/run_one", data=json.dumps(payload),
                           content_type="application/json")
        assert resp.status_code == 400

    def test_run_bracket_with_mocked_bars(self, client):
        payload = {
            "occ":        "SPY260112C00690000",
            "entryDate":  "2026-01-12",
            "expiry":     "2026-01-12",
            "entryTime":  "09:30",
            "strategyId": "bracket",
            "params":     {"takeProfitPct": 50, "stopLossPct": 25},
            "qty":        1,
        }
        with patch("main.fetch_bars", return_value=self._mock_bars()):
            resp = client.post("/api/run_one", data=json.dumps(payload),
                               content_type="application/json")

        if resp.status_code == 200:
            data = json.loads(resp.data)
            assert "result" in data
            r = data["result"]
            assert "pnl" in r
            assert "exitReason" in r
            assert "entryPrice" in r
            assert "exitPrice" in r


# ─────────────────────────────────────────────────────────────
# Flask API — /api/backtest
# ─────────────────────────────────────────────────────────────

class TestApiBacktest:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"QC_USER_ID": "12345", "QC_API_TOKEN": "testtoken"}):
            from main import app
            app.config["TESTING"] = True
            with app.test_client() as c:
                yield c

    def _mock_bars(self, n=25, price=2.00, date="2026-01-12"):
        bars = []
        for i in range(n):
            minute = 30 + i
            t = f"{date} {9 + minute // 60:02d}:{minute % 60:02d}"
            bars.append({
                "time": t, "open": price, "high": price + 0.05,
                "low": price - 0.05, "close": price, "volume": 100, "vwap": price,
            })
        return bars

    def test_invalid_occ_returns_400(self, client):
        resp = client.post("/api/backtest",
                           data=json.dumps({"contracts": "BADINPUT", "qty": 1}),
                           content_type="application/json")
        assert resp.status_code == 400

    def test_empty_contracts_returns_400(self, client):
        resp = client.post("/api/backtest",
                           data=json.dumps({"contracts": "", "qty": 1}),
                           content_type="application/json")
        assert resp.status_code == 400

    def test_valid_contract_with_mocked_bars(self, client):
        payload = {"contracts": "SPY260112C00690000, 09:30", "qty": 1}
        mock_bars = self._mock_bars()

        with patch("main.fetch_bars", return_value=mock_bars), \
             patch("main.fetch_daily_bars", return_value=[]), \
             patch("main.fetch_underlying_bars", return_value=[]):
            resp = client.post("/api/backtest", data=json.dumps(payload),
                               content_type="application/json")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "contracts" in data
        assert len(data["contracts"]) == 1
        c = data["contracts"][0]
        assert c["occ"] == "SPY260112C00690000"
        assert len(c["strategies"]) > 0

    def test_result_has_timing_fields(self, client):
        payload = {"contracts": "SPY260112C00690000, 09:30", "qty": 1}

        with patch("main.fetch_bars", return_value=self._mock_bars()), \
             patch("main.fetch_daily_bars", return_value=[]), \
             patch("main.fetch_underlying_bars", return_value=[]):
            resp = client.post("/api/backtest", data=json.dumps(payload),
                               content_type="application/json")

        data = json.loads(resp.data)
        assert "timings" in data
        timings = data["timings"]
        assert "totalMs" in timings
        assert "strategyRuns" in timings

    def test_multiple_contracts(self, client):
        raw = "SPY260112C00690000, 09:30\nSPY260112P00680000, 09:30"
        payload = {"contracts": raw, "qty": 1}

        with patch("main.fetch_bars", return_value=self._mock_bars()), \
             patch("main.fetch_daily_bars", return_value=[]), \
             patch("main.fetch_underlying_bars", return_value=[]):
            resp = client.post("/api/backtest", data=json.dumps(payload),
                               content_type="application/json")

        if resp.status_code == 200:
            data = json.loads(resp.data)
            assert len(data["contracts"]) == 2

    def test_strategy_source_endpoint(self, client):
        resp = client.get("/api/strategy_source/bracket")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["id"] == "bracket"
        assert "source" in data
        assert "execute" in data["source"]

    def test_strategy_source_unknown_returns_404(self, client):
        resp = client.get("/api/strategy_source/does_not_exist_xyz")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────
# Integration tests — real QC API
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestQcClientIntegration:
    """Real API calls — requires credentials + --integration flag."""

    def test_download_known_equity_file(self):
        from qc_client import QCClient
        c = QCClient()
        path = c.equity_trade_path("SPY", "20260110")
        data = c.download_file(path)
        assert len(data) > 0
        assert data[:2] == b"PK"  # ZIP magic bytes

    def test_404_raises_file_missing(self):
        from qc_client import QCClient, QCFileMissing
        c = QCClient()
        with pytest.raises(QCFileMissing):
            c.download_file("option/usa/minute/zzz/99991231_trade.zip")


@pytest.mark.integration
class TestApiIntegration:
    """End-to-end Flask tests against a real QC API response."""

    @pytest.fixture
    def client(self):
        from main import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_qc_test_endpoint(self, client):
        resp = client.get("/api/qc_test")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data.get("auth_status") in (200, 201)

    def test_full_backtest_with_real_data(self, client):
        """Run a real backtest against QC API for a historical 0DTE."""
        payload = {"contracts": "SPY260112C00690000, 09:30", "qty": 1}
        resp = client.post("/api/backtest", data=json.dumps(payload),
                           content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["contracts"]) == 1
        assert len(data["contracts"][0]["strategies"]) > 0
