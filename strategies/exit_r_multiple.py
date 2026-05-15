# ============================================================
# strategies/exit_r_multiple.py
# R-Multiple exit strategy.
#
# HOW IT WORKS:
#   "R" = your initial risk in dollars (entry × risk%).
#   All targets are expressed as multiples of R.
#
#   Example: entry=$2.00, risk=50% → R=$1.00, stop=$1.00
#     At 1R ($3.00): stop moves to $2.00 (break-even)
#     At 2R ($4.00): stop moves to $3.00 (locks 1R profit)
#     At 3R ($5.00): stop moves to $4.00 (locks 2R profit)
#
#   On the chart the stop looks like a staircase — flat then
#   jumping up at each R milestone. Classic systematic approach.
# ============================================================

import math
from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'r_multiple',
    'name':        'R-Multiple step stop',
    'description': 'Stop steps up at each R-multiple of profit. Staircase pattern — '
                   'locks in gains at 1R, 2R, 3R milestones systematically.',
    'params': [
        {'key': 'initialRiskPct', 'label': 'Initial risk / R (% of entry)', 'default': 50,      'min': 1, 'max': 100, 'step': 1,
         'hint': 'e.g. 50 = risk $0.50 per $1.00 of entry price (1R = 50% of entry)'},
        {'key': 'maxR',           'label': 'Max R to target before EOD exit', 'default': 3,     'min': 1, 'max': 20,  'step': 0.5,
         'hint': 'Stop trailing at this multiple — hold for EOD otherwise'},
        {'key': 'eodTime',        'label': 'EOD exit (CST)',                  'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['initialRiskPct'] <= 0 or params['initialRiskPct'] >= 100:
        return 'Initial risk must be between 1% and 99%'
    if params['maxR'] < 1:
        return 'Max R must be at least 1'
    return None


def execute(bars, entry_idx, entry_price, params):
    initial_risk_pct = params['initialRiskPct']
    max_r            = params['maxR']
    eod_time         = params.get('eodTime', '15:45')

    R               = entry_price * (initial_risk_pct / 100)
    stop_price      = entry_price - R
    high_water_mark = entry_price
    current_r       = 0

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        r_reached = math.floor((bar_high - entry_price) / R) if R > 0 else 0

        if r_reached > current_r and r_reached <= max_r:
            current_r = r_reached
            new_stop  = entry_price + (r_reached - 1) * R
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        reason = 'hard_stop' if current_r == 0 else 'r_step_stop'

        if bar_open <= stop_price:
            return {
                'exitBar': bar, 'exitReason': reason,
                'stopPrice': bar_open, 'highWaterMark': high_water_mark, 'rAtExit': current_r, 'rValue': R, 'stopTrace': trace,
            }

        if bar_low <= stop_price:
            return {
                'exitBar': bar, 'exitReason': reason,
                'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'rAtExit': current_r, 'rValue': R, 'stopTrace': trace,
            }

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'rAtExit': current_r, 'rValue': R, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'rAtExit': current_r, 'rValue': R, 'stopTrace': trace}
