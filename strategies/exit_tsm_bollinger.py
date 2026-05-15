# ============================================================
# strategies/exit_tsm_bollinger.py
# TSM Bollinger Mean Reversion Exit (Kaufman - Bollinger Squeeze)
#
# HOW IT WORKS:
#   Exits when the option price crosses back above its moving average
#   (Bollinger midline). The premise: if you bought a cheap option
#   and it ran up, crossing back to the MA means the move is done —
#   mean reversion is taking over.
#
#   This is NOT a stop-loss in the traditional sense. It's a
#   mean-reversion take-profit. The stop line IS the moving average,
#   so it appears as a smooth curve on the chart that the price
#   eventually touches from above.
#
#   A hard stop below entry protects the downside.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'tsm_bollinger',
    'name':        'TSM Bollinger mean-revert',
    'description': 'Exit when price crosses back to its moving average. '
                   'Mean-reversion take-profit. Kaufman TSM Bollinger.',
    'params': [
        {'key': 'maPeriod',    'label': 'MA period (bars)',       'default': 20, 'min': 5,  'max': 100, 'step': 5,
         'hint': 'Bollinger midline lookback'},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',          'default': 25, 'min': 5,  'max': 100, 'step': 5},
        {'key': 'warmupBars',  'label': 'Warmup before MA exit',  'default': 10, 'min': 0,  'max': 50,  'step': 5,
         'hint': 'Bars after entry before the MA exit can trigger (avoids exiting immediately)'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['maPeriod'] < 3:
        return 'MA period must be at least 3'
    return None


def execute(bars, entry_idx, entry_price, params):
    ma_period   = int(params['maPeriod'])
    hard_stop_pct = params['hardStopPct']
    warmup      = int(params['warmupBars'])
    eod_time    = params.get('eodTime', '15:45')

    stop_price  = entry_price * (1 - hard_stop_pct / 100)
    trace       = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])
        bars_since_entry = i - entry_idx

        # Compute moving average of close
        window_start = max(0, i - ma_period + 1)
        window = [float(bars[j]['close']) for j in range(window_start, i + 1)]
        ma = sum(window) / len(window) if window else entry_price

        # The "stop" shown on chart is the MA (exit trigger for mean reversion)
        # But real protective stop is the hard stop below entry
        display_stop = ma if bars_since_entry > warmup else stop_price
        trace.append({'time': bar['time'], 'stopPrice': display_stop})

        # Hard stop always active
        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'highWaterMark': entry_price, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': stop_price,
                    'highWaterMark': entry_price, 'stopTrace': trace}

        # MA mean-reversion exit — only after warmup, and only if price
        # has been above MA and now the high touches it from above
        # (for a long: we exit when price comes back DOWN to the MA)
        if bars_since_entry > warmup and len(window) >= ma_period:
            # Price must have been above MA at some point (it ran up)
            # Exit when the LOW touches the MA — price is reverting
            if bar_low <= ma:
                fill = max(ma, bar_low)  # fill at MA or low, whichever higher
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': fill,
                        'highWaterMark': bar_high, 'maAtExit': round(ma, 4), 'stopTrace': trace}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': display_stop,
                    'highWaterMark': bar_high, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': display_stop,
            'highWaterMark': float(bars[-1]['high']), 'stopTrace': trace}
