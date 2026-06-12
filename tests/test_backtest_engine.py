"""
tests/test_backtest_engine.py

Unit tests for backtest_engine.py:
    - to_minutes
    - fill_price
    - find_entry_bar
    - should_eod_exit
    - append_trace / append_diag
    - process_contract  (uses a minimal stub strategy)
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest_engine import (
    to_minutes,
    fill_price,
    find_entry_bar,
    should_eod_exit,
    process_contract,
    append_trace,
    append_diag,
)


# ─────────────────────────────────────────────────────────────
# to_minutes
# ─────────────────────────────────────────────────────────────

class TestToMinutes:
    def test_market_open(self):
        assert to_minutes("09:30") == 9 * 60 + 30

    def test_noon(self):
        assert to_minutes("12:00") == 720

    def test_eod(self):
        assert to_minutes("15:45") == 15 * 60 + 45

    def test_midnight(self):
        assert to_minutes("00:00") == 0

    def test_end_of_day(self):
        assert to_minutes("23:59") == 23 * 60 + 59


# ─────────────────────────────────────────────────────────────
# fill_price
# ─────────────────────────────────────────────────────────────

class TestFillPrice:
    def test_uses_close(self):
        bar = {"close": 2.50, "last": 1.00}
        assert fill_price(bar) == 2.50

    def test_falls_back_to_last(self):
        bar = {"close": None, "last": 1.75}
        assert fill_price(bar) == 1.75

    def test_returns_zero_when_both_missing(self):
        assert fill_price({}) == 0

    def test_handles_string_zero(self):
        # "0" is a non-empty string (truthy), so float("0") = 0.0
        bar = {"close": "0", "last": None}
        assert fill_price(bar) == 0


# ─────────────────────────────────────────────────────────────
# find_entry_bar
# ─────────────────────────────────────────────────────────────

class TestFindEntryBar:
    def _make_bars(self, times):
        return [{"time": t, "close": 2.00, "open": 1.98, "high": 2.05, "low": 1.95}
                for t in times]

    def test_exact_match(self):
        bars = self._make_bars([
            "2026-01-12 09:30",
            "2026-01-12 09:31",
            "2026-01-12 09:32",
        ])
        bar = find_entry_bar(bars, "2026-01-12", to_minutes("09:31"))
        assert bar["time"] == "2026-01-12 09:31"

    def test_first_at_or_after(self):
        """If there is no bar at exactly 09:31, return 09:32."""
        bars = self._make_bars([
            "2026-01-12 09:30",
            "2026-01-12 09:32",
            "2026-01-12 09:34",
        ])
        bar = find_entry_bar(bars, "2026-01-12", to_minutes("09:31"))
        assert bar["time"] == "2026-01-12 09:32"

    def test_returns_none_if_date_not_in_bars(self):
        bars = self._make_bars(["2026-01-11 09:30"])
        assert find_entry_bar(bars, "2026-01-12", to_minutes("09:30")) is None

    def test_returns_none_if_time_after_last_bar(self):
        bars = self._make_bars(["2026-01-12 09:30"])
        assert find_entry_bar(bars, "2026-01-12", to_minutes("16:00")) is None

    def test_returns_first_bar_of_day_when_entry_before_open(self):
        bars = self._make_bars([
            "2026-01-12 09:30",
            "2026-01-12 09:31",
        ])
        bar = find_entry_bar(bars, "2026-01-12", to_minutes("09:00"))
        assert bar["time"] == "2026-01-12 09:30"


# ─────────────────────────────────────────────────────────────
# should_eod_exit
# ─────────────────────────────────────────────────────────────

class TestShouldEodExit:
    def _bar(self, time_str):
        return {"time": time_str, "close": 2.00}

    def test_fires_at_eod_time(self):
        params = {"eodTime": "15:45", "eodMode": "daily"}
        assert should_eod_exit(self._bar("2026-01-12 15:45"), params) is True

    def test_fires_after_eod_time(self):
        params = {"eodTime": "15:45", "eodMode": "daily"}
        assert should_eod_exit(self._bar("2026-01-12 15:50"), params) is True

    def test_does_not_fire_before_eod(self):
        params = {"eodTime": "15:45", "eodMode": "daily"}
        assert should_eod_exit(self._bar("2026-01-12 15:44"), params) is False

    def test_daily_mode_fires_every_day(self):
        params = {"eodTime": "15:45", "eodMode": "daily"}
        assert should_eod_exit(self._bar("2026-01-13 15:45"), params) is True

    def test_expiry_mode_only_fires_on_expiry_day(self):
        contract = {"expiry": "2026-01-15"}
        params = {"eodTime": "15:45", "eodMode": "expiry", "_contract": contract}
        assert should_eod_exit(self._bar("2026-01-14 15:45"), params) is False
        assert should_eod_exit(self._bar("2026-01-15 15:45"), params) is True

    def test_default_mode_is_daily(self):
        params = {"eodTime": "15:45"}
        assert should_eod_exit(self._bar("2026-01-12 15:45"), params) is True


# ─────────────────────────────────────────────────────────────
# append_trace / append_diag
# ─────────────────────────────────────────────────────────────

class TestAppendTrace:
    def test_adds_entry(self):
        d = {}
        bar = {"time": "2026-01-12 09:31"}
        append_trace(d, "Hard stop", bar, 1.50)
        assert d == {"Hard stop": [{"time": "2026-01-12 09:31", "stopPrice": 1.50}]}

    def test_appends_to_existing_key(self):
        d = {"Hard stop": [{"time": "2026-01-12 09:30", "stopPrice": 1.50}]}
        bar = {"time": "2026-01-12 09:31"}
        append_trace(d, "Hard stop", bar, 1.55)
        assert len(d["Hard stop"]) == 2

    def test_skips_none_price(self):
        d = {}
        append_trace(d, "Hard stop", {"time": "09:30"}, None)
        assert d == {}


class TestAppendDiag:
    def test_creates_entry_with_metadata(self):
        d = {}
        bar = {"time": "2026-01-12 09:31"}
        append_diag(d, "proxy_vix", bar, 22.5,
                    label="Proxy VIX", unit="", scaleHint="volatility")
        assert "proxy_vix" in d
        entry = d["proxy_vix"]
        assert entry["label"] == "Proxy VIX"
        assert entry["scaleHint"] == "volatility"
        assert entry["series"] == [{"time": "2026-01-12 09:31", "value": 22.5}]

    def test_skips_none_value(self):
        d = {}
        append_diag(d, "proxy_vix", {"time": "09:31"}, None)
        assert d == {}


# ─────────────────────────────────────────────────────────────
# process_contract — uses a minimal inline strategy stub
# ─────────────────────────────────────────────────────────────

def _make_strategy(exit_at_bar_idx=5, exit_reason="hard_stop", stop_price=1.50):
    """Build a stub strategy module that exits at a predetermined bar index."""
    from types import SimpleNamespace

    def validate(params):
        return None

    def execute(bars, entry_idx, entry_price, params):
        target_idx = min(entry_idx + exit_at_bar_idx, len(bars) - 1)
        return {
            "exitBar":    bars[target_idx],
            "exitReason": exit_reason,
            "stopPrice":  stop_price,
            "stopTrace":  [{"time": bars[i]["time"], "stopPrice": stop_price}
                           for i in range(entry_idx + 1, target_idx + 1)],
        }

    return SimpleNamespace(
        META={
            "id":     "stub_strategy",
            "name":   "Stub Strategy",
            "params": [],
        },
        validate=validate,
        execute=execute,
    )


class TestProcessContract:
    def _bars_for_date(self, date="2026-01-12", n=20, price=2.00):
        bars = []
        for i in range(n):
            minute = 30 + i
            t = f"{date} {9 + minute // 60:02d}:{minute % 60:02d}"
            bars.append({
                "time":   t,
                "open":   price,
                "high":   price + 0.05,
                "low":    price - 0.05,
                "close":  price,
                "volume": 100,
                "vwap":   price,
            })
        return bars

    def _contract(self):
        return {
            "occ":       "SPY260112C00690000",
            "symbol":    "SPY",
            "strike":    690.0,
            "type":      "C",
            "expiry":    "2026-01-12",
            "entryDate": "2026-01-12",
            "entryTime": "09:30",
        }

    def test_basic_result_shape(self):
        bars = self._bars_for_date()
        contract = self._contract()
        strategy = _make_strategy(exit_at_bar_idx=5, stop_price=1.50)
        params = {"eodTime": "15:45", "eodMode": "daily",
                  "_contract": contract, "_config": {}}
        result = process_contract(contract, bars, strategy, params, qty=1)

        assert result is not None
        assert result["occ"] == "SPY260112C00690000"
        assert result["exitReason"] == "hard_stop"
        assert result["qty"] == 1
        assert "pnl" in result
        assert "retPct" in result
        assert "entryPrice" in result
        assert "exitPrice" in result

    def test_pnl_calculated_correctly(self):
        bars = self._bars_for_date(price=2.00)
        contract = self._contract()
        # Strategy exits with stopPrice=1.50 → loss of $0.50 × 100 × 1 = -$50
        strategy = _make_strategy(exit_at_bar_idx=3, exit_reason="hard_stop", stop_price=1.50)
        params = {"eodTime": "15:45", "eodMode": "daily",
                  "_contract": contract, "_config": {}}
        result = process_contract(contract, bars, strategy, params, qty=1)

        assert result["entryPrice"] == pytest.approx(2.00, abs=0.01)
        assert result["pnl"] == pytest.approx(-50.00, abs=1.0)

    def test_qty_multiplies_pnl(self):
        bars = self._bars_for_date(price=2.00)
        contract = self._contract()
        strategy = _make_strategy(exit_at_bar_idx=3, exit_reason="hard_stop", stop_price=1.50)
        params = {"eodTime": "15:45", "eodMode": "daily",
                  "_contract": contract, "_config": {}}
        result_1 = process_contract(contract, bars, strategy, params, qty=1)
        result_3 = process_contract(contract, bars, strategy, params, qty=3)

        assert result_3["pnl"] == pytest.approx(result_1["pnl"] * 3, abs=0.01)

    def test_returns_none_when_no_entry_bar(self):
        """Entry date not in bars — should return None, not crash."""
        bars = self._bars_for_date(date="2026-01-11")
        contract = self._contract()  # entryDate = 2026-01-12
        strategy = _make_strategy()
        params = {"eodTime": "15:45", "eodMode": "daily",
                  "_contract": contract, "_config": {}}
        result = process_contract(contract, bars, strategy, params, qty=1)
        assert result is None

    def test_returns_none_when_no_bars(self):
        contract = self._contract()
        strategy = _make_strategy()
        params = {"eodTime": "15:45", "_contract": contract, "_config": {}}
        result = process_contract(contract, [], strategy, params, qty=1)
        assert result is None

    def test_bars_held_is_correct(self):
        bars = self._bars_for_date(n=20)
        contract = self._contract()
        strategy = _make_strategy(exit_at_bar_idx=5)
        params = {"eodTime": "15:45", "eodMode": "daily",
                  "_contract": contract, "_config": {}}
        result = process_contract(contract, bars, strategy, params, qty=1)
        # entry is bar 0 (09:30), exit is bar 5 (09:35) → 5 bars held
        assert result["barsHeld"] == 5

    def test_strategy_id_and_name_in_result(self):
        bars = self._bars_for_date()
        contract = self._contract()
        strategy = _make_strategy()
        params = {"eodTime": "15:45", "eodMode": "daily",
                  "_contract": contract, "_config": {}}
        result = process_contract(contract, bars, strategy, params, qty=1)
        assert result["strategyId"] == "stub_strategy"
        assert result["strategyName"] == "Stub Strategy"
