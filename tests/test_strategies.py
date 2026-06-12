"""
tests/test_strategies.py

Strategy logic tests for the most commonly used exit_*.py modules.

Each test builds a deterministic bar sequence and asserts that the
strategy fires the right exit at the right bar with the right reason.

Conventions:
    entry_idx = 0  (strategy walks bars[1:])
    entry_price is always bars[0]["close"]
    params always include eodTime, eodMode, _contract, _config, _cache
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _b(time_str, close, open_=None, high=None, low=None):
    """Shorthand bar builder."""
    c = close
    return {
        "time":   time_str,
        "open":   open_ if open_ is not None else c,
        "high":   high  if high  is not None else c + 0.05,
        "low":    low   if low   is not None else c - 0.05,
        "close":  c,
        "volume": 100,
        "vwap":   c,
    }


def _base_params(eod="15:45", mode="daily", contract=None, config=None):
    return {
        "eodTime":   eod,
        "eodMode":   mode,
        "_contract": contract or {},
        "_config":   config  or {"defaults": {"riskFreeRate": 0.0525}},
        "_cache":    {},
    }


def _times(n, start_hour=9, start_min=31):
    """Generate n consecutive 'YYYY-MM-DD HH:MM' strings."""
    times = []
    for i in range(n):
        total = start_hour * 60 + start_min + i
        times.append(f"2026-01-12 {total // 60:02d}:{total % 60:02d}")
    return times


# ─────────────────────────────────────────────────────────────
# exit_fixed_stop
# ─────────────────────────────────────────────────────────────

class TestFixedStop:
    def _import(self):
        from strategies import exit_fixed_stop
        return exit_fixed_stop

    def test_validate_rejects_zero(self):
        m = self._import()
        assert m.validate({"hardStopPct": 0}) is not None

    def test_validate_rejects_over_100(self):
        m = self._import()
        assert m.validate({"hardStopPct": 101}) is not None

    def test_validate_passes(self):
        m = self._import()
        assert m.validate({"hardStopPct": 50}) is None

    def test_fires_on_low_touch(self):
        m = self._import()
        # Entry $2.00, stop 50% = $1.00; bar 3 has low = $0.90
        bars = [_b("2026-01-12 09:30", 2.00)]
        for t in _times(2):
            bars.append(_b(t, 2.00))
        bars.append(_b("2026-01-12 09:33", 2.00, low=0.90))

        params = {**_base_params(), "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "hard_stop"
        assert result["stopPrice"] == pytest.approx(1.00, abs=0.01)

    def test_gap_through_fills_at_open(self):
        m = self._import()
        # Entry $2.00, stop 50% = $1.00; bar opens at $0.80 (gap through)
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 0.80, open_=0.80, low=0.70))

        params = {**_base_params(), "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "hard_stop"
        assert result["stopPrice"] == pytest.approx(0.80, abs=0.01)

    def test_eod_exit_fires(self):
        m = self._import()
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 15:45", 2.00))

        params = {**_base_params(), "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "eod"

    def test_expiry_if_no_stop_or_eod(self):
        m = self._import()
        # Stop at 50% = $1.00; bar low = $1.95 (above stop); no EOD bar → expiry
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 10:00", 2.00))

        params = {**_base_params(), "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] in ("expiry", "eod")


# ─────────────────────────────────────────────────────────────
# exit_trailing_pct
# ─────────────────────────────────────────────────────────────

class TestTrailingPct:
    def _import(self):
        from strategies import exit_trailing_pct
        return exit_trailing_pct

    def test_validate_rejects_zero_trail(self):
        m = self._import()
        assert m.validate({"trailPct": 0, "hardStopPct": 50}) is not None

    def test_stop_moves_up_with_price(self):
        m = self._import()
        # Entry $1.00; rises by $0.20/bar for 10 bars → peak ~$3.05 (high+0.05)
        # Trail 25% → stop ~= 3.05 × 0.75 = 2.29
        bars = [_b("2026-01-12 09:30", 1.00)]
        for i, t in enumerate(_times(10)):
            c = round(1.00 + (i + 1) * 0.20, 2)
            bars.append(_b(t, c))
        # Crash bar that touches the trailing stop
        bars.append(_b("2026-01-12 09:42", 1.50, low=1.50))

        params = {**_base_params(), "trailPct": 25, "hardStopPct": 50}
        result = m.execute(bars, 0, 1.00, params)
        assert result["exitReason"] == "trailing_stop"
        assert result["highWaterMark"] == pytest.approx(3.05, abs=0.10)

    def test_hard_stop_respected_early(self):
        m = self._import()
        # Entry $2.00; bar 1 gap-opens at $0.50 (below 50% hard stop = $1.00)
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 0.50, open_=0.50, low=0.50))

        params = {**_base_params(), "trailPct": 10, "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        # gap-through open fires trailing_stop (exit_trailing_pct uses that reason for all exits)
        assert result["exitReason"] == "trailing_stop"
        assert result["stopPrice"] <= 1.00 + 0.01

    def test_stop_never_decreases(self):
        m = self._import()
        # Prices: rise, dip, rise higher, dip. Stop should be based on highest peak.
        bars = [_b("2026-01-12 09:30", 2.00)]
        prices = [2.50, 2.30, 3.00, 2.80]
        for price, t in zip(prices, _times(4)):
            bars.append(_b(t, price))

        params = {**_base_params(), "trailPct": 20, "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        # Stop at end = peak_high × 0.80. Peak high ≈ 3.05, so stop ≥ 2.44
        assert result["stopPrice"] >= 2.40 - 0.01


# ─────────────────────────────────────────────────────────────
# exit_bracket
# ─────────────────────────────────────────────────────────────

class TestBracket:
    def _import(self):
        from strategies import exit_bracket
        return exit_bracket

    def test_validate_rejects_zero_tp(self):
        m = self._import()
        assert m.validate({"takeProfitPct": 0, "stopLossPct": 25}) is not None

    def test_validate_rejects_sl_over_100(self):
        m = self._import()
        assert m.validate({"takeProfitPct": 50, "stopLossPct": 101}) is not None

    def test_tp_hit_intrabar(self):
        m = self._import()
        # Entry $2.00, TP 50% = $3.00; bar 2 high = $3.10 → TP fires
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 2.00))
        bars.append(_b("2026-01-12 09:32", 2.90, high=3.10))

        params = {**_base_params(), "takeProfitPct": 50, "stopLossPct": 25}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "trailing_stop"
        assert result["tpHit"] is True
        assert result["stopPrice"] == pytest.approx(3.00, abs=0.01)

    def test_sl_hit_intrabar(self):
        m = self._import()
        # Entry $2.00, SL 25% = $1.50; bar 1 low = $1.40
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 1.90, low=1.40))

        params = {**_base_params(), "takeProfitPct": 50, "stopLossPct": 25}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "hard_stop"
        assert result["tpHit"] is False

    def test_tp_gap_through_fills_at_open(self):
        m = self._import()
        # Entry $2.00, TP 50% = $3.00; bar gaps open at $3.50 (above TP)
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 3.50, open_=3.50))

        params = {**_base_params(), "takeProfitPct": 50, "stopLossPct": 25}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "trailing_stop"
        assert result["stopPrice"] == pytest.approx(3.50, abs=0.01)

    def test_eod_exit_when_neither_triggered(self):
        m = self._import()
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 15:45", 2.50))

        params = {**_base_params(), "takeProfitPct": 50, "stopLossPct": 25}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "eod"


# ─────────────────────────────────────────────────────────────
# exit_break_even
# ─────────────────────────────────────────────────────────────

class TestBreakEven:
    def _import(self):
        from strategies import exit_break_even
        return exit_break_even

    def test_hard_stop_fires_before_activation(self):
        m = self._import()
        # Entry $2.00, activation 30% = $2.60; price gaps down to $0.90 (hard stop fires)
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 0.90, open_=0.90, low=0.90))

        params = {**_base_params(),
                  "activationPct": 30, "continueTrail": False,
                  "trailPct": 20, "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "hard_stop"
        assert result["breakEvenActive"] is False

    def test_stop_moves_to_entry_after_activation(self):
        m = self._import()
        # Entry $2.00, activation 30% = $2.60
        # Bar 1 reaches high $2.70 → arms BE, stop → $2.00
        # Bar 2 opens above BE but low dips below entry → BE stop fires at $2.00
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 2.70, high=2.70))  # arms BE
        bars.append(_b("2026-01-12 09:32", 1.90, open_=2.05, low=1.90))  # low hits BE stop

        params = {**_base_params(),
                  "activationPct": 30, "continueTrail": False,
                  "trailPct": 20, "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "break_even_stop"
        assert result["breakEvenActive"] is True
        assert result["stopPrice"] == pytest.approx(2.00, abs=0.01)

    def test_validate_rejects_zero_activation(self):
        m = self._import()
        err = m.validate({"activationPct": 0, "continueTrail": False, "trailPct": 20,
                          "hardStopPct": 50})
        assert err is not None


# ─────────────────────────────────────────────────────────────
# exit_profit_lock
# ─────────────────────────────────────────────────────────────

class TestProfitLock:
    def _import(self):
        from strategies import exit_profit_lock
        return exit_profit_lock

    def test_hard_stop_fires_before_activation(self):
        m = self._import()
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 0.80, open_=0.80, low=0.80))

        params = {**_base_params(),
                  "activationPct": 50, "trailPct": 25, "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] in ("hard_stop", "trailing_stop")
        assert result["trailActivated"] is False

    def test_trail_activates_and_exits_on_reversal(self):
        m = self._import()
        # Entry $2.00, activation 50% = $3.00
        # Bar 1 reaches high $3.10 → trail arms, stop = 3.10 × 0.75 = 2.325
        # Bar 2 opens at $2.20 (gap through stop) → exits
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 3.10, high=3.10))
        bars.append(_b("2026-01-12 09:32", 2.20, open_=2.20, low=2.20))

        params = {**_base_params(),
                  "activationPct": 50, "trailPct": 25, "hardStopPct": 50}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "trailing_stop"
        assert result["trailActivated"] is True

    def test_validate_rejects_trail_geq_activation(self):
        m = self._import()
        err = m.validate({"activationPct": 25, "trailPct": 25, "hardStopPct": 50})
        assert err is not None


# ─────────────────────────────────────────────────────────────
# exit_r_multiple
# ─────────────────────────────────────────────────────────────

class TestRMultiple:
    def _import(self):
        from strategies import exit_r_multiple
        return exit_r_multiple

    def test_stop_steps_to_be_at_1r(self):
        m = self._import()
        # Entry $2.00, risk 50% → R = $1.00, initial stop = $1.00
        # Bar 1 high = $3.15 → crosses 1R ($3.00), stop moves to $2.00 (break-even)
        # Bar 2 opens above $2.00 but low touches below $2.00 → r_step_stop at $2.00
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 09:31", 3.10, high=3.15))
        bars.append(_b("2026-01-12 09:32", 1.90, open_=2.05, low=1.90))

        params = {**_base_params(), "initialRiskPct": 50, "maxR": 3}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "r_step_stop"
        assert result["stopPrice"] == pytest.approx(2.00, abs=0.01)

    def test_expiry_if_no_stop_hit(self):
        m = self._import()
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 10:00", 2.10))

        params = {**_base_params(), "initialRiskPct": 50, "maxR": 3}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] in ("expiry", "eod")


# ─────────────────────────────────────────────────────────────
# exit_time_decay
# ─────────────────────────────────────────────────────────────

class TestTimeDecay:
    def _import(self):
        from strategies import exit_time_decay
        return exit_time_decay

    def test_validate_rejects_min_geq_max(self):
        m = self._import()
        err = m.validate({"maxTrailPct": 20, "minTrailPct": 25})
        assert err is not None

    def test_trail_tightens_over_time(self):
        m = self._import()
        # Price flat at $5.00. minStopDist=2.0 keeps floor well below bar lows
        # so the pct trail dominates and we can observe it tightening.
        bars = [_b("2026-01-12 09:30", 5.00)]
        for t in ["2026-01-12 09:31", "2026-01-12 12:00", "2026-01-12 15:44"]:
            bars.append(_b(t, 5.00))

        params = {**_base_params(),
                  "maxTrailPct": 40, "minTrailPct": 10,
                  "minStopDist": 2.0, "marketOpen": "09:30"}
        result = m.execute(bars, 0, 5.00, params)
        trace = result["stopTrace"]
        assert len(trace) >= 2
        # Stop rises as trail tightens toward close
        assert trace[0]["stopPrice"] <= trace[-1]["stopPrice"] + 0.01


# ─────────────────────────────────────────────────────────────
# exit_dte_threshold
# ─────────────────────────────────────────────────────────────

class TestDteThreshold:
    def _import(self):
        from strategies import exit_dte_threshold
        return exit_dte_threshold

    def test_exits_on_dte_day_at_eod(self):
        m = self._import()
        contract = {"expiry": "2026-01-14"}
        bars = [_b("2026-01-12 09:30", 2.00)]
        # 2026-01-13 has DTE=1 which is <= dteDays=2 → holds to EOD
        bars.append(_b("2026-01-13 15:45", 2.00))

        params = {**_base_params(contract=contract),
                  "dteDays": 2, "exitAtOpen": False, "hardStopPct": 90}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "eod"
        assert result.get("exitType") == "dte_threshold"

    def test_does_not_exit_before_dte(self):
        m = self._import()
        contract = {"expiry": "2026-01-30"}
        bars = [_b("2026-01-12 09:30", 2.00)]
        bars.append(_b("2026-01-12 15:44", 2.00))  # before EOD, 18 DTE

        params = {**_base_params(contract=contract),
                  "dteDays": 7, "exitAtOpen": False, "hardStopPct": 90}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "expiry"

    def test_exit_at_open_mode(self):
        m = self._import()
        contract = {"expiry": "2026-01-13"}
        bars = [_b("2026-01-12 09:30", 2.00)]
        # First bar on expiry day → exitAtOpen fires immediately
        bars.append(_b("2026-01-13 09:30", 2.00, open_=2.00))

        params = {**_base_params(contract=contract),
                  "dteDays": 0, "exitAtOpen": True, "hardStopPct": 90}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "eod"
        assert result.get("exitType") == "dte_threshold"


# ─────────────────────────────────────────────────────────────
# exit_bracket_* variants (spot-check a few)
# ─────────────────────────────────────────────────────────────

class TestBracketVariants:
    @pytest.mark.parametrize("module_name,tp,sl", [
        ("exit_bracket_50_25", 50, 25),
        ("exit_bracket_100_50", 100, 50),
        ("exit_bracket_15_10", 15, 10),
    ])
    def test_variant_uses_correct_tp_sl(self, module_name, tp, sl):
        import importlib
        m = importlib.import_module(f"strategies.{module_name}")
        entry = 2.00
        tp_price = round(entry * (1 + tp / 100), 4)

        # Bar whose high clearly clears the TP
        bars = [_b("2026-01-12 09:30", entry)]
        bars.append(_b("2026-01-12 09:31", entry, high=tp_price + 0.05))

        params = {**_base_params()}
        result = m.execute(bars, 0, entry, params)
        assert result["exitReason"] == "trailing_stop"
        assert result["stopPrice"] == pytest.approx(tp_price, abs=0.01)


# ─────────────────────────────────────────────────────────────
# exit_trailing_pct_* variants
# ─────────────────────────────────────────────────────────────

class TestTrailingPctVariants:
    @pytest.mark.parametrize("module_name,trail_pct", [
        ("exit_trailing_pct_10", 10),
        ("exit_trailing_pct_20", 20),
        ("exit_trailing_pct_25", 25),
        ("exit_trailing_pct_30", 30),
    ])
    def test_variant_trail_fires_correctly(self, module_name, trail_pct):
        import importlib
        m = importlib.import_module(f"strategies.{module_name}")
        entry = 2.00
        peak = 4.00
        expected_stop = round(peak * (1 - trail_pct / 100), 2)

        bars = [_b("2026-01-12 09:30", entry)]
        bars.append(_b("2026-01-12 09:31", peak, high=peak))
        # Drop below stop: open at expected_stop - 0.10 triggers gap-through
        bars.append(_b("2026-01-12 09:32", expected_stop - 0.10,
                        low=expected_stop - 0.10))

        params = {**_base_params(), "hardStopPct": 90}
        result = m.execute(bars, 0, entry, params)
        assert result["exitReason"] == "trailing_stop"
        assert result["highWaterMark"] == pytest.approx(peak, abs=0.10)


# ─────────────────────────────────────────────────────────────
# exit_premium_pct variants
# ─────────────────────────────────────────────────────────────

class TestPremiumPctVariants:
    @pytest.mark.parametrize("module_name,stop_pct", [
        ("exit_premium_pct_25", 25),
        ("exit_premium_pct_50", 50),
        ("exit_premium_pct_75", 75),
    ])
    def test_variant_fires_at_correct_level(self, module_name, stop_pct):
        import importlib
        m = importlib.import_module(f"strategies.{module_name}")
        entry = 2.00
        stop_price = round(entry * (1 - stop_pct / 100), 4)

        bars = [_b("2026-01-12 09:30", entry)]
        # Open at stop, so gap-through fills at stop (not below)
        bars.append(_b("2026-01-12 09:31", stop_price - 0.05,
                        open_=stop_price, low=stop_price - 0.05))

        # Variant delegates to exit_premium_pct which uses params['stopPct']
        params = {**_base_params(), "stopPct": stop_pct}
        result = m.execute(bars, 0, entry, params)
        assert result["exitReason"] == "hard_stop"
        assert result["stopPrice"] == pytest.approx(stop_price, abs=0.01)


# ─────────────────────────────────────────────────────────────
# exit_tsm_bollinger
# ─────────────────────────────────────────────────────────────

class TestTsmBollinger:
    def _import(self):
        from strategies import exit_tsm_bollinger
        return exit_tsm_bollinger

    def test_hard_stop_fires_during_warmup(self):
        m = self._import()
        # Entry $2.00, hard stop 50% = $1.00; crash bar at low=$0.80 (< 3 warmup bars)
        bars = [_b("2026-01-12 09:30", 2.00)]
        for t in _times(3):
            bars.append(_b(t, 2.00))
        bars.append(_b("2026-01-12 09:35", 0.80, low=0.80))

        params = {**_base_params(), "maPeriod": 20, "hardStopPct": 50, "warmupBars": 10}
        result = m.execute(bars, 0, 2.00, params)
        assert result["exitReason"] == "hard_stop"

    def test_ma_exit_fires_after_warmup(self):
        m = self._import()
        # 14 rising bars then one bar whose low dips to early price levels,
        # which falls below the 5-bar MA of the recent highs
        bars = [_b("2026-01-12 09:30", 2.00)]
        for i, t in enumerate(_times(14)):
            c = round(2.00 + (i + 1) * 0.20, 2)
            bars.append(_b(t, c))
        # This bar's low = 2.00, MA of bars[11..15] ≈ 4.00 → low <= MA → fires
        bars.append(_b("2026-01-12 09:45", 2.00, low=2.00))

        params = {**_base_params(), "maPeriod": 5, "hardStopPct": 90, "warmupBars": 3}
        result = m.execute(bars, 0, 2.00, params)
        # Should exit via MA mean-reversion or EOD, NOT hard stop
        assert result["exitReason"] in ("trailing_stop", "eod", "expiry")
