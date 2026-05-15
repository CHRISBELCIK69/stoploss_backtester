# ============================================================
# strategies/exit_trailing_pct.py
# Percentage trailing stop.
#
# HOW IT WORKS:
#   Tracks the highest price since entry (high water mark).
#   Stop sits X% below that high — and only moves UP, never down.
#   So as price climbs, the stop climbs with it, locking in gains.
#   If price reverses X% from its peak, you exit.
#   Best for: catching big runners while protecting against reversals.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'trailing_pct',
    'name':        'Percentage trailing stop',
    'description': 'Stop trails X% below the highest price seen since entry. '
                   'Locks in gains as the trade runs — exits on reversal from peak.',
    'params': [
        {'key': 'trailPct',    'label': 'Trail distance (%)',      'default': 25, 'min': 1, 'max': 99,  'step': 1,
         'hint': 'e.g. 25 = stop sits 25% below the running high'},
        {'key': 'hardStopPct', 'label': 'Initial hard stop (%)',   'default': 50, 'min': 1, 'max': 100, 'step': 1,
         'hint': 'Fixed stop before trail moves above entry'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',          'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['trailPct'] <= 0:
        return 'Trail % must be greater than 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    trail_pct     = params['trailPct']
    hard_stop_pct = params['hardStopPct']
    eod_time      = params.get('eodTime', '15:45')

    high_water_mark = entry_price
    stop_price      = entry_price * (1 - hard_stop_pct / 100)

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high
            new_stop = high_water_mark * (1 - trail_pct / 100)
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_open, 'highWaterMark': high_water_mark, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}
