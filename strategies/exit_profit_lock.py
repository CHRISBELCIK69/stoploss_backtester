# ============================================================
# strategies/exit_profit_lock.py
# Profit-lock trailing stop — the most popular structure for 0DTE.
#
# HOW IT WORKS:
#   Phase 1: Hard stop holds. No trailing yet — gives the trade
#            room to breathe through early noise.
#   Phase 2: Once price reaches +X% profit, the trailing stop
#            activates and follows the high water mark by Y%.
#
#   On the chart you'll see a flat stop line that suddenly starts
#   climbing once the activation target is hit. This prevents
#   premature stop-outs on early dips while still locking gains.
# ============================================================

from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'profit_lock',
    'name':        'Profit-lock trailing stop',
    'description': 'Hard stop until profit target hit, then trails below peak by a %. '
                   'Prevents early stop-outs, then locks in gains on runners.',
    'params': [
        {'key': 'activationPct', 'label': 'Profit to activate trail (%)',    'default': 50, 'min': 5,  'max': 500, 'step': 5,
         'hint': 'e.g. 50 = trail activates once price is up 50% from entry'},
        {'key': 'trailPct',      'label': 'Trail distance (%)',              'default': 25, 'min': 1,  'max': 99,  'step': 1,
         'hint': 'How far below the high the stop sits once active'},
        {'key': 'hardStopPct',   'label': 'Hard stop before activation (%)', 'default': 50, 'min': 1,  'max': 100, 'step': 1,
         'hint': 'Fixed stop loss before the trail kicks in'},
        {'key': 'eodTime',       'label': 'EOD exit (CST)',                  'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['trailPct'] >= params['activationPct']:
        return 'Trail % should be less than activation % (otherwise stop triggers immediately on activation)'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    activation_pct  = params['activationPct']
    trail_pct       = params['trailPct']
    hard_stop_pct   = params['hardStopPct']
    eod_time        = params.get('eodTime', '15:45')

    high_water_mark   = entry_price
    trail_active      = False
    stop_price        = entry_price * (1 - hard_stop_pct / 100)
    activation_target = entry_price * (1 + activation_pct / 100)

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        # Track whether trail was already active BEFORE this bar
        was_trail_active = trail_active

        if not trail_active and bar_high >= activation_target:
            trail_active    = True
            high_water_mark = bar_high
            stop_price      = high_water_mark * (1 - trail_pct / 100)
            # Don't check stop this bar — activation and stop-out
            # can't happen on the same bar (let next bar be first trigger)

        if was_trail_active and trail_active and bar_high > high_water_mark:
            high_water_mark = bar_high
            new_stop = high_water_mark * (1 - trail_pct / 100)
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Only check stop if trail was already active before this bar,
        # OR if we're still in the hard-stop phase
        if not trail_active or was_trail_active:
            reason = 'trailing_stop' if trail_active else 'hard_stop'

            # Gap-through: open already below stop → fill at open
            if bar_open <= stop_price:
                return {
                    'exitBar': bar, 'exitReason': reason,
                    'stopPrice': bar_open, 'highWaterMark': high_water_mark, 'trailActivated': trail_active, 'stopTrace': trace,
                }

            if bar_low <= stop_price:
                return {
                    'exitBar': bar, 'exitReason': reason,
                    'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'trailActivated': trail_active, 'stopTrace': trace,
                }

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'trailActivated': trail_active, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'trailActivated': trail_active, 'stopTrace': trace}
