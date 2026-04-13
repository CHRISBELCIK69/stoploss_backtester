# ============================================================
# strategies/exit_trailing_dollar.py
# Fixed dollar trailing stop.
#
# HOW IT WORKS:
#   Same concept as percentage trailing, but the distance is a
#   fixed dollar amount instead of a percentage.
#   Stop = high water mark - $X.
#   Better than % trailing when the option is very cheap (sub-$1)
#   because a 25% trail on a $0.20 option is only $0.05 — noise.
#   A $0.50 trail gives consistent room regardless of price.
# ============================================================

from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'trailing_dollar',
    'name':        'Dollar trailing stop',
    'description': 'Stop trails a fixed $ amount below the high water mark. '
                   'Better than % trailing for cheap options where small %s = noise.',
    'params': [
        {'key': 'trailAmount', 'label': 'Trail amount ($)',        'default': 0.50, 'min': 0.01, 'max': 50,  'step': 0.05,
         'hint': 'e.g. 0.50 = stop is always $0.50 below the running high'},
        {'key': 'hardStopPct', 'label': 'Initial hard stop (%)',   'default': 50,   'min': 1,    'max': 100, 'step': 1,
         'hint': 'Fixed % stop until the trailing stop overtakes it'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',          'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['trailAmount'] <= 0:
        return 'Trail amount must be greater than $0'
    return None


def execute(bars, entry_idx, entry_price, params):
    trail_amount  = params['trailAmount']
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
            new_stop = high_water_mark - trail_amount
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
