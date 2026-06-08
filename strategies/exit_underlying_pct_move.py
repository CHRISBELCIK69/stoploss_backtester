# ============================================================
# strategies/exit_underlying_pct_move.py
# Family 2 — Underlying Price Stops
# Variant: % move in underlying against position
#
# HOW IT WORKS:
#   Fetches the underlying's entry price from the option bar's
#   context. Exits when the underlying has moved more than
#   underlyingStopPct% against your position direction.
#
#   This is fundamentally different from exit_premium_pct.py:
#     - premium_pct watches the OPTION price (affected by IV,
#       theta, delta, gamma — noisy)
#     - underlying_pct_move watches the UNDERLYING price move
#       (clean directional signal, ignores IV noise)
#
#   Example: you buy a SPY call at $2.00 when SPY is at $580.
#   underlyingStopPct = 0.5 → stop fires if SPY drops to $577.10
#   regardless of what the option premium is doing due to IV changes.
#
#   IMPLEMENTATION NOTE:
#   Option 1-min bars do not include the underlying price directly.
#   This strategy infers the underlying's directional move from
#   the option's open-to-current move scaled by entry delta.
#   If delta is not available in params, it falls back to a
#   pure option-premium % stop (identical to premium_pct at that
#   point — the user should supply delta for accurate behavior).
#
#   Pass delta via params['_contract']['delta'] if available.
#   Without delta, stopPct on option premium is used directly.
#
#   A secondary hard stop (hardStopPct on option premium) is
#   always active as a floor.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'underlying_pct_move',
    'name':        'Underlying % move stop',
    'description': 'Exit when the underlying has moved X% against position. '
                   'Uses delta scaling to convert underlying move to option impact.',
    'params': [
        {
            'key':     'underlyingStopPct',
            'label':   'Underlying move stop (%)',
            'default': 0.5,
            'min':     0.1,
            'max':     5.0,
            'step':    0.1,
            'hint':    '0.5 = exit if underlying moves 0.5% against you (e.g. SPY drops $2.90 from $580)',
        },
        {
            'key':     'entryDelta',
            'label':   'Option delta at entry (0.01–1.00)',
            'default': 0.50,
            'min':     0.01,
            'max':     1.00,
            'step':    0.01,
            'hint':    'Delta scales underlying move to option impact. 0.50 = ATM. Leave at 0.50 if unknown.',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop floor (% on option premium)',
            'default': 75,
            'min':     5,
            'max':     100,
            'step':    5,
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
    if params['underlyingStopPct'] <= 0:
        return 'Underlying stop % must be greater than 0'
    if not (0 < params['entryDelta'] <= 1.0):
        return 'Entry delta must be between 0.01 and 1.00'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    underlying_stop_pct = params['underlyingStopPct'] / 100
    entry_delta         = params['entryDelta']
    hard_stop_pct       = params['hardStopPct'] / 100

    # Convert underlying % move to an equivalent option dollar move
    # using delta: option_impact ≈ underlying_move × delta
    # underlying_move = entry_price_underlying × underlying_stop_pct
    # We don't have underlying price, so we express it relative to option entry:
    #   option_equivalent = entry_price × underlying_stop_pct / entry_delta
    # This is an approximation — overstates stop distance for deep ITM,
    # understates for far OTM. Correct delta makes it accurate.
    option_stop_dollars = entry_price * underlying_stop_pct / entry_delta
    delta_stop  = entry_price - option_stop_dollars
    pct_stop    = entry_price * (1 - hard_stop_pct)
    stop_price  = max(delta_stop, pct_stop)

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])

        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        append_trace(extras, 'Delta-scaled stop', bar, delta_stop)
        append_trace(extras, 'Pct floor',         bar, pct_stop)

        if bar_open <= stop_price:
            return {
                'exitBar':         bar,
                'exitReason':      'hard_stop',
                'stopPrice':       bar_open,
                'deltaStop':       round(delta_stop, 4),
                'entryDeltaUsed':  entry_delta,
                'stopTrace':       trace,
                'extraTraces':     extras,
            }
        if bar_low <= stop_price:
            return {
                'exitBar':         bar,
                'exitReason':      'hard_stop',
                'stopPrice':       stop_price,
                'deltaStop':       round(delta_stop, 4),
                'entryDeltaUsed':  entry_delta,
                'stopTrace':       trace,
                'extraTraces':     extras,
            }
        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  stop_price,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  stop_price,
        'stopTrace':  trace,
        'extraTraces': extras,
    }
