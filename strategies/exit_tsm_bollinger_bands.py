# ============================================================
# strategies/exit_tsm_bollinger_bands.py
# True Bollinger Bands exit — uses all three lines (midline + ±N σ)
# instead of just the SMA midline.
#
# HOW IT WORKS:
#
#   Three lines computed every bar:
#     midline = SMA(close, period)
#     upper   = midline + numStdDev × σ
#     lower   = midline − numStdDev × σ
#   where σ is the rolling stddev of close prices over `period` bars.
#
#   Two independent exit triggers (whichever fires first):
#
#     1) UPPER BAND TAKE-PROFIT
#        bar_high reaches the upper band → exit at upper band price.
#        This catches the peak of a run — the option is "stretched"
#        relative to its own recent average.
#
#     2) MIDLINE MEAN-REVERSION
#        Price was above the midline at some point, then a bar closes
#        BELOW the midline → exit at bar close. The momentum is gone.
#
#   Plus a hard stop floor below entry as catastrophic safety.
#
#   On the chart you'll see the midline drawn as the "stop" line.
#   The upper/lower band levels at exit are reported as extras.
# ============================================================

import math
from backtest_engine import to_minutes, append_trace, should_eod_exit


META = {
    'enabled':     True,
    'id':          'tsm_bollinger_bands',
    'name':        'TSM Bollinger Bands (full)',
    'description': 'True Bollinger Bands — midline + upper/lower σ bands. '
                   'Take-profit on upper band touch, mean-revert on midline cross.',
    'params': [
        {'key': 'period',      'label': 'Period (bars)',          'default': 20, 'min': 5,   'max': 100, 'step': 5,
         'hint': 'Lookback for SMA midline and stddev'},
        {'key': 'numStdDev',   'label': 'Bands σ multiplier',     'default': 2.0, 'min': 0.5, 'max': 4.0, 'step': 0.25,
         'hint': 'Distance of upper/lower bands from midline (typical = 2.0)'},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',          'default': 50, 'min': 5,   'max': 100, 'step': 5,
         'hint': 'Catastrophic stop below entry'},
        {'key': 'warmupBars',  'label': 'Warmup bars',            'default': 10, 'min': 0,   'max': 50,  'step': 5,
         'hint': 'Bars after entry before band exits can trigger'},
        {'key': 'requireUpperTouch', 'label': 'Require upper-band touch first', 'default': True, 'type': 'boolean',
         'hint': 'Mean-revert only exits if price ALREADY hit upper band — filters chop'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['period'] < 3:
        return 'Period must be at least 3'
    if params['numStdDev'] <= 0:
        return 'σ multiplier must be greater than 0'
    return None


def _bands(closes, period, num_std):
    """
    Compute (midline, upper, lower, sigma) from a list of closes.
    Returns (None, None, None, None) if not enough data.
    """
    n = len(closes)
    if n < 2:
        return None, None, None, None
    window = closes[-period:] if n >= period else closes
    midline = sum(window) / len(window)
    var = sum((x - midline) ** 2 for x in window) / max(1, len(window) - 1)
    sigma = math.sqrt(var)
    upper = midline + num_std * sigma
    lower = midline - num_std * sigma
    return midline, upper, lower, sigma


def execute(bars, entry_idx, entry_price, params):
    period         = int(params['period'])
    num_std        = float(params['numStdDev'])
    hard_stop_pct  = params['hardStopPct']
    warmup         = int(params['warmupBars'])
    require_touch  = bool(params['requireUpperTouch'])
    eod_time       = params.get('eodTime', '15:45')

    hard_stop      = entry_price * (1 - hard_stop_pct / 100)
    high_water_mark = entry_price
    upper_touched  = False
    above_midline  = False
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])
        bar_mins  = to_minutes(bar['time'][11:16])
        bars_since = i - entry_idx

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        # ── Compute bands from closes up to this bar ──
        closes = [float(bars[j]['close']) for j in range(max(0, i - period + 1), i + 1)]
        midline, upper, lower, sigma = _bands(closes, period, num_std)

        # Track if price was above midline at any point
        if midline is not None and bar_high > midline:
            above_midline = True

        # Track if price has touched the upper band
        if upper is not None and bar_high >= upper:
            upper_touched = True

        # Display trace = midline (the "stop line" the chart draws)
        # Falls back to hard stop during warmup so chart isn't empty
        display_stop = midline if (bars_since > warmup and midline is not None) else hard_stop
        trace.append({'time': bar['time'], 'stopPrice': display_stop})
        # Extras: the actual Bollinger bands + hard stop floor
        append_trace(extras, 'Upper band', bar, upper)
        append_trace(extras, 'Lower band', bar, lower)
        append_trace(extras, 'Hard stop',  bar, hard_stop)

        # ── Hard stop always active ──
        if bar_open <= hard_stop:
            return {
                'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                'highWaterMark': high_water_mark,
                'midline': round(midline, 4) if midline else None,
                'upperBand': round(upper, 4) if upper else None,
                'lowerBand': round(lower, 4) if lower else None,
                'sigma': round(sigma, 4) if sigma else None,
                'upperTouched': upper_touched,
                'stopTrace': trace, 'extraTraces': extras,
            }
        if bar_low <= hard_stop:
            return {
                'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                'highWaterMark': high_water_mark,
                'midline': round(midline, 4) if midline else None,
                'upperBand': round(upper, 4) if upper else None,
                'lowerBand': round(lower, 4) if lower else None,
                'sigma': round(sigma, 4) if sigma else None,
                'upperTouched': upper_touched,
                'stopTrace': trace, 'extraTraces': extras,
            }

        # ── Band exits only after warmup ──
        if bars_since > warmup and midline is not None:

            # 1) UPPER BAND TAKE-PROFIT — bar_high reached upper band
            if upper is not None and bar_high >= upper:
                # Fill at upper band level (take profit at the band)
                fill = min(upper, bar_high)
                return {
                    'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': fill,
                    'highWaterMark': high_water_mark,
                    'midline': round(midline, 4),
                    'upperBand': round(upper, 4),
                    'lowerBand': round(lower, 4),
                    'sigma': round(sigma, 4),
                    'exitType': 'upper_band_touch',
                    'upperTouched': True,
                    'stopTrace': trace, 'extraTraces': extras,
                }

            # 2) MIDLINE MEAN-REVERSION — price was above, now closes below
            mean_revert_eligible = above_midline and (not require_touch or upper_touched)
            if mean_revert_eligible and bar_close < midline:
                # Fill at the better of close or midline (already past it on close)
                fill = max(bar_close, midline)
                # If gapped through, fill at open
                if bar_open < midline:
                    fill = bar_open
                return {
                    'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': fill,
                    'highWaterMark': high_water_mark,
                    'midline': round(midline, 4),
                    'upperBand': round(upper, 4),
                    'lowerBand': round(lower, 4),
                    'sigma': round(sigma, 4),
                    'exitType': 'midline_revert',
                    'upperTouched': upper_touched,
                    'stopTrace': trace, 'extraTraces': extras,
                }

        # ── EOD ──
        if should_eod_exit(bar, params):
            return {
                'exitBar': bar, 'exitReason': 'eod', 'stopPrice': display_stop,
                'highWaterMark': high_water_mark,
                'midline': round(midline, 4) if midline else None,
                'upperBand': round(upper, 4) if upper else None,
                'lowerBand': round(lower, 4) if lower else None,
                'sigma': round(sigma, 4) if sigma else None,
                'upperTouched': upper_touched,
                'stopTrace': trace, 'extraTraces': extras,
            }

    # Held to last bar
    last = bars[-1]
    closes = [float(bars[j]['close']) for j in range(max(0, len(bars) - period), len(bars))]
    midline, upper, lower, sigma = _bands(closes, period, num_std)
    return {
        'exitBar': last, 'exitReason': 'expiry', 'stopPrice': midline if midline else hard_stop,
        'highWaterMark': high_water_mark,
        'midline': round(midline, 4) if midline else None,
        'upperBand': round(upper, 4) if upper else None,
        'lowerBand': round(lower, 4) if lower else None,
        'sigma': round(sigma, 4) if sigma else None,
        'upperTouched': upper_touched,
        'stopTrace': trace, 'extraTraces': extras,
    }
