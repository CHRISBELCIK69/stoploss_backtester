# ============================================================
# strategies/exit_break_even.py
# Break-even stop.
#
# HOW IT WORKS:
#   Phase 1: Hard stop below entry — normal downside protection.
#   Phase 2: Once price reaches +X% profit, stop jumps to your
#            entry price. Now you can't lose money on this trade.
#   Phase 3 (optional): After activation, the stop trails above
#            break-even, locking in gains.
#
#   The chart shows a flat stop that suddenly jumps to entry price
#   when the activation target is hit, then optionally climbs.
#   Common 0DTE play: "once I'm up 30%, move stop to entry."
# ============================================================

from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'break_even',
    'name':        'Break-even stop',
    'description': 'Hard stop until profit target hit, then stop jumps to entry price (break-even). '
                   'Guarantees no loss once activated. Optionally trails above break-even.',
    'params': [
        {'key': 'activationPct', 'label': 'Profit to move stop to break-even (%)', 'default': 30,      'min': 5,  'max': 200, 'step': 5,
         'hint': 'Once price hits this profit %, stop moves to your entry price'},
        {'key': 'continueTrail', 'label': 'Trail above break-even after activation', 'default': True,  'type': 'boolean'},
        {'key': 'trailPct',      'label': 'Trail distance above break-even (%)',    'default': 20,      'min': 1,  'max': 99,  'step': 1,
         'hint': 'Only used if Continue Trail is enabled'},
        {'key': 'hardStopPct',   'label': 'Hard stop before activation (%)',        'default': 50,      'min': 1,  'max': 100, 'step': 1},
        {'key': 'eodTime',       'label': 'EOD exit (CST)',                         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['activationPct'] <= 0:
        return 'Activation % must be greater than 0'
    if params['continueTrail'] and params['trailPct'] <= 0:
        return 'Trail % must be greater than 0 when continue trail is enabled'
    return None


def execute(bars, entry_idx, entry_price, params):
    activation_pct  = params['activationPct']
    continue_trail  = params['continueTrail']
    trail_pct       = params['trailPct']
    hard_stop_pct   = params['hardStopPct']
    eod_time        = params.get('eodTime', '15:45')

    high_water_mark   = entry_price
    break_even_active = False
    stop_price        = entry_price * (1 - hard_stop_pct / 100)
    activation_target = entry_price * (1 + activation_pct / 100)

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        was_be_active = break_even_active

        if not break_even_active and bar_high >= activation_target:
            break_even_active = True
            high_water_mark   = bar_high
            stop_price        = entry_price
            # Don't check stop this bar — activation bar gets a pass

        if was_be_active and break_even_active and continue_trail and bar_high > high_water_mark:
            high_water_mark = bar_high
            new_stop = high_water_mark * (1 - trail_pct / 100)
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Only check stop if BE was already active before this bar,
        # OR if we're still in the hard-stop phase
        if not break_even_active or was_be_active:
            reason = 'break_even_stop' if break_even_active else 'hard_stop'

            if bar_open <= stop_price:
                return {
                    'exitBar': bar, 'exitReason': reason,
                    'stopPrice': bar_open, 'highWaterMark': high_water_mark, 'breakEvenActive': break_even_active, 'stopTrace': trace,
                }

            if bar_low <= stop_price:
                return {
                    'exitBar': bar, 'exitReason': reason,
                    'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'breakEvenActive': break_even_active, 'stopTrace': trace,
                }

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'breakEvenActive': break_even_active, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price, 'highWaterMark': high_water_mark, 'breakEvenActive': break_even_active, 'stopTrace': trace}
