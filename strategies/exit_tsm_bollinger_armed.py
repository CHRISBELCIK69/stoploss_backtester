# ============================================================
# strategies/exit_tsm_bollinger_armed.py
# Bollinger mean-revert exit — armed variant.
#
# Solves the "touchy trigger that exits before the move starts"
# problem of the basic tsm_bollinger by adding two filters:
#
#   FIX 1 — Profit-arm threshold
#     The MA exit doesn't arm until price has touched
#     `minArmProfitPct` above entry. Until then, only the hard
#     stop is active. This guarantees the trade has demonstrated
#     directional edge before mean-reversion logic engages.
#
#   FIX 2 — Deliberate cross, not a wick
#     Instead of `bar_low <= ma` (any wick triggers exit), the
#     trigger is `prev_close > prev_ma AND bar_close < ma`. This
#     requires the price to have been above the MA on the last
#     bar AND to close below the MA on the current bar — a real,
#     sustained cross. Filters out:
#       - single-bar wicks from spread widening
#       - quote-glitch noise
#       - overshoots that immediately recover
#
#   Hard stop floor is active throughout, same as the base version.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'tsm_bollinger_armed',
    'name':        'TSM Bollinger armed (profit + cross)',
    'description': 'Mean-revert exit gated by profit threshold + deliberate '
                   'close-cross of the MA. Avoids exiting before the move starts.',
    'params': [
        {'key': 'maPeriod',         'label': 'MA period (bars)',         'default': 20, 'min': 5,   'max': 100, 'step': 5,
         'hint': 'Bollinger midline lookback'},
        {'key': 'minArmProfitPct',  'label': 'Arm profit threshold (%)', 'default': 15, 'min': 1,   'max': 200, 'step': 1,
         'hint': 'MA exit cannot fire until price reaches entry × (1 + this %)'},
        {'key': 'hardStopPct',      'label': 'Hard stop (%)',            'default': 25, 'min': 5,   'max': 100, 'step': 5},
        {'key': 'warmupBars',       'label': 'Warmup bars',              'default': 10, 'min': 0,   'max': 50,  'step': 5,
         'hint': 'Bars after entry before MA exit can fire (even if armed)'},
        {'key': 'eodTime',          'label': 'EOD exit (CST)',           'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['maPeriod'] < 3:
        return 'MA period must be at least 3'
    if params['minArmProfitPct'] <= 0:
        return 'Arm profit threshold must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    ma_period          = int(params['maPeriod'])
    min_arm_profit_pct = float(params['minArmProfitPct'])
    hard_stop_pct      = params['hardStopPct']
    warmup             = int(params['warmupBars'])

    hard_stop  = entry_price * (1 - hard_stop_pct / 100)
    arm_target = entry_price * (1 + min_arm_profit_pct / 100)

    ma_exit_armed   = False
    high_water_mark = entry_price
    prev_close      = entry_price
    prev_ma         = entry_price
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])
        bars_since_entry = i - entry_idx

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        # ── Compute MA from close prices ──
        window_start = max(0, i - ma_period + 1)
        window = [float(bars[j]['close']) for j in range(window_start, i + 1)]
        ma = sum(window) / len(window) if window else entry_price

        # ── Arm the MA exit once price reaches the profit threshold ──
        if not ma_exit_armed and bar_high >= arm_target:
            ma_exit_armed = True

        # Primary trace is the MA throughout — the exit-driving level once
        # armed. Drawn continuously (no jumps) so the chart reads cleanly.
        # The hard stop and arm target become extra context lines.
        trace.append({'time': bar['time'], 'stopPrice': ma})
        append_trace(extras, 'Hard stop',  bar, hard_stop)
        append_trace(extras, 'Arm target', bar, arm_target)

        # ── Hard stop always active ──
        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'highWaterMark': high_water_mark, 'maExitArmed': ma_exit_armed,
                    'maAtExit': round(ma, 4), 'stopTrace': trace, 'extraTraces': extras}

        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'highWaterMark': high_water_mark, 'maExitArmed': ma_exit_armed,
                    'maAtExit': round(ma, 4), 'stopTrace': trace, 'extraTraces': extras}

        # ── MA exit only fires if armed AND past warmup AND deliberate cross ──
        # Deliberate cross: prev bar's close was ABOVE its MA, this bar closes BELOW current MA.
        # Both conditions must be true to filter noise.
        if (ma_exit_armed
            and bars_since_entry > warmup
            and len(window) >= ma_period
            and prev_close > prev_ma
            and bar_close < ma):

            # Fill at bar close — deliberate cross means close is the realistic fill
            return {'exitBar': bar, 'exitReason': 'trailing_stop',
                    'stopPrice': bar_close, 'highWaterMark': high_water_mark,
                    'maExitArmed': True, 'maAtExit': round(ma, 4),
                    'exitType': 'deliberate_cross', 'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            # For EOD, the engine uses bar close as fill price anyway —
            # stopPrice here is just metadata. Use ma for consistency.
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': ma,
                    'highWaterMark': high_water_mark, 'maExitArmed': ma_exit_armed,
                    'maAtExit': round(ma, 4), 'stopTrace': trace, 'extraTraces': extras}

        # Update prev_ values for next bar's cross check
        prev_close = bar_close
        prev_ma    = ma

    last_ma = ma if 'ma' in dir() else entry_price
    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': last_ma,
            'highWaterMark': high_water_mark, 'maExitArmed': ma_exit_armed,
            'maAtExit': round(last_ma, 4), 'stopTrace': trace, 'extraTraces': extras}
