# ============================================================
# strategies/exit_fixed_stop.py
# Simple fixed percentage stop loss. No trailing.
# The stop price is set at entry and never moves.
#
# HOW IT WORKS:
#   A single stop price is calculated at entry: entry × (1 - stop%).
#   If the bar low touches that price at any point, you're out.
#   The stop NEVER moves — it's a flat line on the chart.
#   Good baseline, but gives back all gains on a runner.
# ============================================================

from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'fixed_stop',
    'name':        'Fixed stop loss',
    'description': 'Exit when price drops a fixed % below entry. Stop never moves. '
                   'Simple downside protection but gives back all gains on a winning trade.',
    'params': [
        {'key': 'hardStopPct', 'label': 'Stop loss (%)',    'default': 50,      'min': 1,   'max': 100, 'step': 1,
         'hint': 'e.g. 50 = exit when price falls 50% below entry price'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',   'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['hardStopPct'] <= 0 or params['hardStopPct'] > 100:
        return 'Stop loss must be between 1% and 100%'
    return None


def execute(bars, entry_idx, entry_price, params):
    hard_stop_pct = params['hardStopPct']
    eod_time      = params.get('eodTime', '15:45')
    stop_price    = entry_price * (1 - hard_stop_pct / 100)

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Gap-through: open already below stop → fill at open
        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open, 'highWaterMark': entry_price, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': stop_price, 'highWaterMark': entry_price, 'stopTrace': trace}

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': entry_price, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price, 'highWaterMark': entry_price, 'stopTrace': trace}
