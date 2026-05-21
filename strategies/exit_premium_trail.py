# ============================================================
# strategies/exit_premium_trail.py
# Family 1 — Premium-Based Stops
# Variant: Trailing stop on premium value
#
# HOW IT WORKS:
#   Tracks the highest premium seen since entry (high water mark).
#   Trail stop = high_water_mark × (1 - trailPct / 100).
#   Only moves up — never down.
#
#   Differs from exit_trailing_pct.py in intent and framing:
#   this is explicitly a "protect my premium gains" exit, not a
#   generic trailing stop. The params surface premium-centric
#   language and the initial hard stop is set tighter by default
#   (25% vs 50%) since premium-based thinking favors capital
#   preservation over letting winners run.
#
#   Also adds a minimum trail floor: once the stop rises above
#   entry price (break-even), it never drops back below entry.
#   This guarantees at worst a scratch trade after the position
#   has moved in your favor.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'premium_trail',
    'name':        'Premium trailing stop',
    'description': 'Trails X% below the highest premium seen. '
                   'Stop auto-floors at entry once above break-even.',
    'params': [
        {
            'key':     'trailPct',
            'label':   'Trail distance (% of premium)',
            'default': 20,
            'min':     5,
            'max':     80,
            'step':    5,
            'hint':    '20 = stop sits 20% below the running high of the premium',
        },
        {
            'key':     'hardStopPct',
            'label':   'Initial hard stop (%)',
            'default': 25,
            'min':     5,
            'max':     100,
            'step':    5,
            'hint':    'Fixed stop until the trailing stop rises above it',
        },
        {
            'key':     'floorAtBreakEven',
            'label':   'Floor at break-even once profitable',
            'default': True,
            'type':    'boolean',
            'hint':    'Once stop rises above entry price, it never drops back below entry',
        },
        {
            'key':     'eodTime',
            'label':   'EOD exit (CST)',
            'default': '15:45',
            'type':    'time',
        },
    ],
}


def validate(params):
    if params['trailPct'] <= 0:
        return 'Trail % must be greater than 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    trail_pct          = params['trailPct'] / 100
    hard_stop_pct      = params['hardStopPct'] / 100
    floor_at_breakeven = params.get('floorAtBreakEven', True)

    high_water_mark = entry_price
    stop_price      = entry_price * (1 - hard_stop_pct)
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])

        if bar_high > high_water_mark:
            high_water_mark = bar_high
            new_trail = high_water_mark * (1 - trail_pct)
            # Apply break-even floor
            if floor_at_breakeven:
                new_trail = max(new_trail, entry_price)
            if new_trail > stop_price:
                stop_price = new_trail

        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        append_trace(extras, 'Break-even floor', bar, entry_price)

        if bar_open <= stop_price:
            return {
                'exitBar':        bar,
                'exitReason':     'trailing_stop',
                'stopPrice':      bar_open,
                'highWaterMark':  high_water_mark,
                'stopTrace':      trace,
                'extraTraces':    extras,
            }
        if bar_low <= stop_price:
            return {
                'exitBar':        bar,
                'exitReason':     'trailing_stop',
                'stopPrice':      stop_price,
                'highWaterMark':  high_water_mark,
                'stopTrace':      trace,
                'extraTraces':    extras,
            }
        if should_eod_exit(bar, params):
            return {
                'exitBar':       bar,
                'exitReason':    'eod',
                'stopPrice':     stop_price,
                'highWaterMark': high_water_mark,
                'stopTrace':     trace,
                'extraTraces':   extras,
            }

    return {
        'exitBar':       bars[-1],
        'exitReason':    'expiry',
        'stopPrice':     stop_price,
        'highWaterMark': high_water_mark,
        'stopTrace':     trace,
        'extraTraces':   extras,
    }
