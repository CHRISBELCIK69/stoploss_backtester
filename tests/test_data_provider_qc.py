"""
tests/test_data_provider_qc.py

Tests for data_provider_qc.py covering:
    - OCC parsing / building
    - parse_contracts
    - _pad_minute_gaps
    - _matches_occ
    - _lean_ms_to_eastern
    - fetch_bars (mocked — no real QC calls)
    - Integration tests guarded by --integration flag
"""

import io
import csv
import zipfile
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_provider_qc import (
    parse_occ,
    build_occ,
    parse_contracts,
    _pad_minute_gaps,
    _matches_occ,
    _lean_ms_to_eastern,
)


# ─────────────────────────────────────────────────────────────
# OCC parsing
# ─────────────────────────────────────────────────────────────

class TestParseOcc:
    def test_call(self):
        parts = parse_occ("SPY260112C00690000")
        assert parts["symbol"] == "SPY"
        assert parts["expiry"] == "2026-01-12"
        assert parts["type"] == "C"
        assert parts["strike"] == pytest.approx(690.0)

    def test_put(self):
        parts = parse_occ("SPY260112P00680000")
        assert parts["type"] == "P"
        assert parts["strike"] == pytest.approx(680.0)

    def test_fractional_strike(self):
        parts = parse_occ("SPY260112C00467500")
        assert parts["strike"] == pytest.approx(467.5)

    def test_multi_char_symbol(self):
        parts = parse_occ("AMZN260306C00220000")
        assert parts["symbol"] == "AMZN"
        assert parts["expiry"] == "2026-03-06"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_occ("NOT_AN_OCC")

    def test_roundtrip(self):
        occ = "SPY260112C00690000"
        parts = parse_occ(occ)
        rebuilt = build_occ(parts["symbol"], parts["strike"],
                            parts["type"], parts["expiry"])
        assert rebuilt == occ


class TestBuildOcc:
    def test_basic_call(self):
        assert build_occ("SPY", 690, "C", "2026-01-12") == "SPY260112C00690000"

    def test_fractional_strike(self):
        assert build_occ("SPY", 467.5, "C", "2026-05-20") == "SPY260520C00467500"

    def test_put(self):
        assert build_occ("QQQ", 450, "P", "2026-03-21") == "QQQ260321P00450000"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            build_occ("SPY", 690, "X", "2026-01-12")

    def test_yymmdd_expiry_format(self):
        # Should accept both YYYY-MM-DD and YYMMDD
        assert build_occ("SPY", 690, "C", "260112") == "SPY260112C00690000"


# ─────────────────────────────────────────────────────────────
# parse_contracts
# ─────────────────────────────────────────────────────────────

class TestParseContracts:
    def test_0dte_format(self):
        result = parse_contracts("SPY260112C00690000, 09:30")
        assert len(result["contracts"]) == 1
        c = result["contracts"][0]
        assert c["occ"] == "SPY260112C00690000"
        assert c["entryTime"] == "09:30"
        assert c["entryDate"] == "2026-01-12"  # defaults to expiry

    def test_swing_format_with_entry_date(self):
        result = parse_contracts("AMZN260306C00220000, 08:59, 2026-02-25")
        c = result["contracts"][0]
        assert c["entryDate"] == "2026-02-25"
        assert c["entryTime"] == "08:59"

    def test_comments_ignored(self):
        raw = "# This is a comment\nSPY260112C00690000, 09:30"
        result = parse_contracts(raw)
        assert len(result["contracts"]) == 1

    def test_blank_lines_ignored(self):
        raw = "\nSPY260112C00690000, 09:30\n\n"
        result = parse_contracts(raw)
        assert len(result["contracts"]) == 1

    def test_multiple_contracts(self):
        raw = "SPY260112C00690000, 09:30\nQQQ260112C00480000, 10:15"
        result = parse_contracts(raw)
        assert len(result["contracts"]) == 2

    def test_invalid_occ_captured_in_errors(self):
        result = parse_contracts("BADINPUT, 09:30")
        assert len(result["errors"]) > 0
        assert len(result["contracts"]) == 0

    def test_default_entry_time(self):
        """OCC only, no time — should default to 09:30."""
        result = parse_contracts("SPY260112C00690000")
        assert result["contracts"][0]["entryTime"] == "09:30"


# ─────────────────────────────────────────────────────────────
# _lean_ms_to_eastern
# ─────────────────────────────────────────────────────────────

class TestLeanMsToEastern:
    def test_market_open(self):
        # 9:30 ET = 9.5 hours × 3600 × 1000 ms from midnight
        ms = int(9.5 * 3600 * 1000)
        result = _lean_ms_to_eastern("20260112", ms)
        assert result == "2026-01-12 09:30"

    def test_noon(self):
        ms = int(12 * 3600 * 1000)
        result = _lean_ms_to_eastern("20260112", ms)
        assert result == "2026-01-12 12:00"

    def test_eod(self):
        ms = int((15 * 3600 + 45 * 60) * 1000)
        result = _lean_ms_to_eastern("20260112", ms)
        assert result == "2026-01-12 15:45"


# ─────────────────────────────────────────────────────────────
# _matches_occ
# ─────────────────────────────────────────────────────────────

class TestMatchesOcc:
    def _parts(self, strike=690.0, typ="C", expiry="2026-01-12"):
        return {
            "symbol": "SPY",
            "strike": strike,
            "type":   typ,
            "expiry": expiry,
        }

    def _lean_filename(self, strike=690.0, typ="C", expiry="20260112"):
        # Lean scale: strike × 10000
        strike_lean = str(int(round(strike * 10000)))
        type_word = "call" if typ == "C" else "put"
        return f"20260112_spy_minute_trade_{type_word}_{strike_lean}_{expiry}.csv"

    def test_matching_call(self):
        parts = self._parts()
        fname = self._lean_filename()
        assert _matches_occ(fname, parts) is True

    def test_matching_put(self):
        parts = self._parts(typ="P")
        fname = self._lean_filename(typ="P")
        assert _matches_occ(fname, parts) is True

    def test_wrong_strike(self):
        parts = self._parts(strike=695.0)
        fname = self._lean_filename(strike=690.0)
        assert _matches_occ(fname, parts) is False

    def test_wrong_type(self):
        parts = self._parts(typ="P")
        fname = self._lean_filename(typ="C")
        assert _matches_occ(fname, parts) is False

    def test_wrong_expiry(self):
        parts = self._parts(expiry="2026-02-20")
        fname = self._lean_filename(expiry="20260112")
        assert _matches_occ(fname, parts) is False

    def test_case_insensitive(self):
        parts = self._parts()
        fname = self._lean_filename().upper()
        assert _matches_occ(fname, parts) is True


# ─────────────────────────────────────────────────────────────
# _pad_minute_gaps
# ─────────────────────────────────────────────────────────────

class TestPadMinuteGaps:
    def _bar(self, time_str, close=2.00, synthetic=False):
        return {
            "time": time_str, "open": close, "high": close + 0.05,
            "low": close - 0.05, "close": close, "volume": 10,
            "vwap": close, "synthetic": synthetic,
        }

    def test_no_gaps_unchanged(self):
        bars = [self._bar("2026-01-12 09:30"),
                self._bar("2026-01-12 09:31"),
                self._bar("2026-01-12 09:32")]
        result = _pad_minute_gaps(bars)
        assert len(result) == 3
        assert all(not b["synthetic"] for b in result)

    def test_single_gap_filled(self):
        bars = [self._bar("2026-01-12 09:30", close=2.00),
                self._bar("2026-01-12 09:32", close=2.50)]
        result = _pad_minute_gaps(bars)
        assert len(result) == 3
        synthetic = result[1]
        assert synthetic["time"] == "2026-01-12 09:31"
        assert synthetic["synthetic"] is True
        assert synthetic["close"] == pytest.approx(2.00)  # carries forward close

    def test_large_gap_not_filled(self):
        """Gaps > 30 minutes should NOT be padded."""
        bars = [self._bar("2026-01-12 09:30"),
                self._bar("2026-01-12 11:00")]  # 90-minute gap
        result = _pad_minute_gaps(bars)
        assert len(result) == 2

    def test_overnight_gap_not_filled(self):
        bars = [self._bar("2026-01-12 16:00"),
                self._bar("2026-01-13 09:30")]
        result = _pad_minute_gaps(bars)
        assert len(result) == 2

    def test_empty_input(self):
        assert _pad_minute_gaps([]) == []

    def test_single_bar(self):
        bars = [self._bar("2026-01-12 09:30")]
        assert _pad_minute_gaps(bars) == bars

    def test_greeks_carry_forward_in_synthetic(self):
        bar = self._bar("2026-01-12 09:30", close=2.00)
        bar["iv"] = 0.35
        bar["delta"] = 0.45
        bars = [bar, self._bar("2026-01-12 09:32", close=2.50)]
        result = _pad_minute_gaps(bars)
        synthetic = result[1]
        assert synthetic["iv"] == pytest.approx(0.35)
        assert synthetic["delta"] == pytest.approx(0.45)

    def test_multiple_gaps(self):
        # gap 09:30→09:33 = 3 min → fills 09:31, 09:32 (2 synthetic)
        # gap 09:33→09:37 = 4 min → fills 09:34, 09:35, 09:36 (3 synthetic)
        # total: 3 real + 2 + 3 = 8 bars
        bars = [self._bar("2026-01-12 09:30"),
                self._bar("2026-01-12 09:33"),
                self._bar("2026-01-12 09:37")]
        result = _pad_minute_gaps(bars)
        assert len(result) == 8


# ─────────────────────────────────────────────────────────────
# fetch_bars — mocked QC client
# ─────────────────────────────────────────────────────────────

class TestFetchBarsMocked:
    """Tests for fetch_bars using a mocked _fetch_zip — no real API calls."""

    def _make_zip(self, symbol, date_str, strike, expiry_yyyymmdd,
                  option_type="C", num_rows=5):
        scale = 10000
        rows = []
        for i in range(num_rows):
            ms = (9 * 3600 + (30 + i) * 60) * 1000
            price = int(2.00 * scale) + i * 100
            rows.append([ms, price, price + 500, price - 500, price, 10])
        strike_lean = str(int(round(strike * 10000)))
        typ = "call" if option_type == "C" else "put"
        csv_name = (f"{date_str}_{symbol.lower()}_minute_trade_{typ}_"
                    f"{strike_lean}_{expiry_yyyymmdd}.csv")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            s = io.StringIO()
            csv.writer(s).writerows(rows)
            zf.writestr(csv_name, s.getvalue())
        return buf.getvalue()

    def _mock_client(self):
        """Return a mock QCClient with a path-builder that works without real credentials."""
        mock = MagicMock()
        mock.option_trade_path.side_effect = lambda sym, date: f"option/usa/minute/{sym.lower()}/{date}_trade.zip"
        mock.option_iv_path.side_effect = lambda sym, date: f"option/usa/iv/minute/{sym.lower()}/{date}_iv.zip"
        return mock

    def test_fetch_bars_returns_correct_count(self):
        occ = "SPY260112C00690000"
        zip_bytes = self._make_zip("SPY", "20260112", 690.0, "20260112", num_rows=5)

        with patch("data_provider_qc._fetch_zip", return_value=zip_bytes), \
             patch("data_provider_qc._client", return_value=self._mock_client()):
            from data_provider_qc import fetch_bars
            bars = fetch_bars(occ, "2026-01-12", "2026-01-12")

        assert len(bars) >= 5

    def test_fetch_bars_scales_prices(self):
        """Prices should be divided by 10000 (Lean scale)."""
        occ = "SPY260112C00690000"
        zip_bytes = self._make_zip("SPY", "20260112", 690.0, "20260112", num_rows=3)

        with patch("data_provider_qc._fetch_zip", return_value=zip_bytes), \
             patch("data_provider_qc._client", return_value=self._mock_client()):
            from data_provider_qc import fetch_bars
            bars = fetch_bars(occ, "2026-01-12", "2026-01-12")

        assert bars[0]["close"] == pytest.approx(2.00, abs=0.01)

    def test_fetch_bars_returns_empty_on_missing_zip(self):
        with patch("data_provider_qc._fetch_zip", return_value=None), \
             patch("data_provider_qc._client", return_value=self._mock_client()):
            from data_provider_qc import fetch_bars
            bars = fetch_bars("SPY260112C00690000", "2026-01-12", "2026-01-12")
        assert bars == []

    def test_fetch_bars_timestamps_are_eastern(self):
        occ = "SPY260112C00690000"
        zip_bytes = self._make_zip("SPY", "20260112", 690.0, "20260112", num_rows=1)

        with patch("data_provider_qc._fetch_zip", return_value=zip_bytes), \
             patch("data_provider_qc._client", return_value=self._mock_client()):
            from data_provider_qc import fetch_bars
            bars = fetch_bars(occ, "2026-01-12", "2026-01-12")

        assert bars[0]["time"].startswith("2026-01-12 09:30")


# ─────────────────────────────────────────────────────────────
# Integration tests — real QC API
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestQcIntegration:
    """
    Real QC API calls — requires credentials + --integration flag.
    Run with: QC_USER_ID=xxx QC_API_TOKEN=xxx pytest tests/ --integration
    """

    def test_auth_succeeds(self):
        from qc_client import QCClient
        client = QCClient()
        assert client.user_id
        assert client.api_token
        resp = client._post_raw("/api/v2/authenticate", {})
        assert resp.status_code in (200, 201)

    def test_option_trade_path_format(self):
        from qc_client import QCClient
        client = QCClient()
        path = client.option_trade_path("spy", "20260112")
        assert "option/usa/minute/spy/20260112_trade.zip" in path

    def test_fetch_bars_returns_data_for_known_contract(self):
        """
        Fetch real bars for a historical SPY 0DTE.
        Requires active QC subscription with US Equity Options — Minute.
        """
        from data_provider_qc import fetch_bars
        occ = "SPY260112C00690000"
        bars = fetch_bars(occ, "2026-01-12", "2026-01-12")

        assert len(bars) > 0, "Expected bars but got none — check subscription"
        assert "time" in bars[0]
        assert "close" in bars[0]
        assert bars[0]["close"] > 0
        for bar in bars:
            assert bar["time"].startswith("2026-01-12")
