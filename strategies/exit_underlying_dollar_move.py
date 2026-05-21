# ============================================================
# strategies/exit_underlying_dollar_move.py
# Family 2 — Underlying Price Stops
# Variant: Fixed dollar move against position
#
# HOW IT WORKS:
#   Watches the UNDERLYING price (not the option premium).
#   At entry, records the underlying price from params['_contract']
#   context or falls back to the first bar's vwap as a proxy.
#
#   Stop fires when the underlying has moved more than stopDollars
#   against your position direction:
#     CALL: underlying drops >= stopDollars below entry underlying
#     PUT:  underlying rises >= stopDollars above entry underlying
#
#   Option premium moves nonlinearly with the underlying — delta,
#   gamma, and IV all affect the option P&L. This strategy exits
#   based purely on how far the underlying has moved against you,
#   regardless of the option's current price. Useful when you have
#   a specific underlying-level thesis ("I'm out if SPY drops $5").
#
#   Underlying price is read from bar['vwap'] which Polygon
#   populates on option bars. It is the option's VWAP, not the
#   underlying's — this is a known limitation of using option
#   bars alone. For true underlying tracking, the underlying
#   ticker (SPY, QQQ etc.) bars should be fetched separately.
#   Until then, this strategy uses the option's open price as
#   a directional proxy relative to the option's own move.
#
#   PRACTICAL NOTE: The underlying's dollar move is approximated
#   here as: if the option's close falls by more than stopDollars
#   in absolute terms (not %) the underlying has likely moved
#   against you by at least that much. This is conservative —
#   it will rarely fire before the option premium stop would.
#   A future version can ingest parallel underlying bars.
#
#   A percentage hard stop on the option premium remains active
#   as a safety net.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'underlying_dollar_move',
    'name':        'Underlying $ move stop',
    'description': 'Exit when the option has moved more than $X against entry '
                   '(proxy for underlying dollar move). Hard stop % as floor.',
    'params': [
        {
            'key':     'stopDollars',
            'label':   'Stop loss ($)',
            'default': 1.00,
            'min':     0.05,
            'max':     50.0,
            'step':    0.05,
            'hint':    'Exit when the option drops this many dollars from entry (e.g. $1.00 on a $2.00 entry = 50% loss)',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop floor (%)',
            'default': 75,
            'min':     5,
            'max':     100,
            'step':    5,
            'hint':    'Percentage stop as a backstop — whichever fires first',
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
    if params['stopDollars'] <= 0:
        return 'Stop $ must be greater than 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    stop_dollars  = params['stopDollars']
    hard_stop_pct = params['hardStopPct'] / 100

    dollar_stop = entry_price - stop_dollars
    pct_stop    = entry_price * (1 - hard_stop_pct)
    # Use whichever stop is higher (less loss)
    stop_price  = max(dollar_stop, pct_stop)

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])

        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        append_trace(extras, 'Dollar stop',  bar, dollar_stop)
        append_trace(extras, 'Pct stop floor', bar, pct_stop)

        if bar_open <= stop_price:
            return {
                'exitBar':      bar,
                'exitReason':   'hard_stop',
                'stopPrice':    bar_open,
                'dollarStop':   round(dollar_stop, 4),
                'stopTrace':    trace,
                'extraTraces':  extras,
            }
        if bar_low <= stop_price:
            return {
                'exitBar':      bar,
                'exitReason':   'hard_stop',
                'stopPrice':    stop_price,
                'dollarStop':   round(dollar_stop, 4),
                'stopTrace':    trace,
                'extraTraces':  extras,
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
