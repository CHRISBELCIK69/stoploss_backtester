# ============================================================
# strategies/exit_time_decay.py
# Time-decay accelerating trailing stop — 0DTE specific.
#
# HOW IT WORKS:
#   The trail % tightens linearly as the day progresses.
#   At open: wide trail (e.g. 40%) — room to breathe.
#   At close: tight trail (e.g. 10%) — protecting against theta.
#
#   This reflects 0DTE reality: a $2 option at 10am might be
#   $0.10 by 3pm even if the underlying hasn't moved (theta).
#   The stop automatically tightens to match this acceleration.
#
#   On the chart the stop line curves up toward the price line
#   as the day goes on, creating a narrowing channel.
# ============================================================

from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'time_decay',
    'name':        'Time-decay accelerating stop',
    'description': 'Trail tightens throughout the day — loose at open, tight near close. '
                   'Matches theta acceleration on 0DTE options.',
    'params': [
        {'key': 'maxTrailPct', 'label': 'Trail at open (%)',  'default': 40,      'min': 5, 'max': 99, 'step': 1,
         'hint': 'Widest trail — applies at market open'},
        {'key': 'minTrailPct', 'label': 'Trail at close (%)', 'default': 10,      'min': 1, 'max': 99, 'step': 1,
         'hint': 'Tightest trail — applies at EOD exit time'},
        {'key': 'minStopDist', 'label': 'Min stop distance ($)', 'default': 0.10, 'min': 0.01, 'max': 5.0, 'step': 0.01,
         'hint': 'Floor — trail never tighter than this $ amount (prevents penny stops on cheap options)'},
        {'key': 'marketOpen',  'label': 'Market open (CST)',  'default': '09:30', 'type': 'time'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',     'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['minTrailPct'] >= params['maxTrailPct']:
        return 'Trail at close must be less than trail at open'
    return None


def execute(bars, entry_idx, entry_price, params):
    max_trail_pct  = params['maxTrailPct']
    min_trail_pct  = params['minTrailPct']
    min_stop_dist  = params.get('minStopDist', 0.10)
    market_open    = params.get('marketOpen', '09:30')
    eod_time       = params.get('eodTime', '15:45')

    open_mins  = to_minutes(market_open)
    eod_mins   = to_minutes(eod_time)
    total_mins = eod_mins - open_mins

    high_water_mark = entry_price

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        day_progress      = min(1.0, max(0.0, (bar_mins - open_mins) / total_mins))
        current_trail_pct = max_trail_pct - day_progress * (max_trail_pct - min_trail_pct)

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        # Use the tighter of % trail or min dollar distance floor
        pct_stop   = high_water_mark * (1 - current_trail_pct / 100)
        floor_stop = high_water_mark - min_stop_dist
        stop_price = max(pct_stop, floor_stop)

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        if bar_open <= stop_price:
            return {
                'exitBar': bar, 'exitReason': 'trailing_stop',
                'stopPrice': bar_open, 'highWaterMark': high_water_mark,
                'trailPctAtExit': round(current_trail_pct, 1), 'dayProgressPct': round(day_progress * 100), 'stopTrace': trace,
            }

        if bar_low <= stop_price:
            return {
                'exitBar': bar, 'exitReason': 'trailing_stop',
                'stopPrice': stop_price, 'highWaterMark': high_water_mark,
                'trailPctAtExit': round(current_trail_pct, 1), 'dayProgressPct': round(day_progress * 100), 'stopTrace': trace,
            }

        if bar_mins >= eod_mins:
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': None, 'highWaterMark': high_water_mark, 'stopTrace': trace}
