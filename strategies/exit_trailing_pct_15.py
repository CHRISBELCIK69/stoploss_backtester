# ============================================================
# strategies/exit_trailing_pct_2.py
# Percentage trailing stop variant #2 — 15% trail
# ============================================================

from backtest_engine import to_minutes

TRAIL_PCT = 15

META = {
    'enabled':     True,
    'id':          'trailing_pct_15',
    'name':        '#2 Trailing 15%',
    'description': f'Percentage trailing stop — {TRAIL_PCT}% trail distance below running high.',
    'params': [
        {'key': 'hardStopPct', 'label': 'Initial hard stop (%)', 'default': 50, 'min': 1, 'max': 100, 'step': 1},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',        'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
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
            new_stop = high_water_mark * (1 - TRAIL_PCT / 100)
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_open, 'highWaterMark': high_water_mark, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}
