# ============================================================
# strategies/exit_spread_widen.py
# Family 1 — Premium-Based Stops
# Variant: Stop based on bid/ask spread widening
#
# HOW IT WORKS:
#   At entry, records the initial bid/ask spread.
#   Exits when the spread widens beyond spreadMultiple × entry_spread.
#
#   Wide spreads signal:
#     - Loss of liquidity (market makers pulling back)
#     - Elevated IV spike (risk event materializing)
#     - The market is repricing the option against you
#
#   This is especially powerful for 0DTE: a spread that was
#   $0.02 at entry widening to $0.10 is a much more reliable
#   danger signal than any price level.
#
#   IMPORTANT: 1-min aggregate bars do not include bid/ask directly.
#   This strategy uses the bar's high/low spread as a proxy:
#   spread_proxy = bar_high - bar_low.
#   At entry we record entry_spread = entry_bar high - low.
#   This is an approximation — real bid/ask spread data would
#   require a quote feed. The proxy is still useful as a vol
#   spike / whipsaw detector.
#
#   A hard stop below entry remains active as a floor.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'spread_widen_stop',
    'name':        'Spread widening stop',
    'description': 'Exit when the bar high-low range (spread proxy) widens beyond '
                   'X× the entry spread. Detects liquidity loss and vol spikes.',
    'params': [
        {
            'key':     'spreadMultiple',
            'label':   'Spread widening trigger (×)',
            'default': 3.0,
            'min':     1.5,
            'max':     10.0,
            'step':    0.5,
            'hint':    '3.0 = exit when spread is 3× the entry spread',
        },
        {
            'key':     'minSpreadDollar',
            'label':   'Min spread to care about ($)',
            'default': 0.05,
            'min':     0.01,
            'max':     1.0,
            'step':    0.01,
            'hint':    'Ignore spread widening below this $ amount (filters noise on cheap options)',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (%)',
            'default': 50,
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
    if params['spreadMultiple'] < 1.0:
        return 'Spread multiple must be >= 1.0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    spread_multiple  = params['spreadMultiple']
    min_spread       = params.get('minSpreadDollar', 0.05)
    hard_stop_pct    = params['hardStopPct'] / 100
    hard_stop        = entry_price * (1 - hard_stop_pct)

    # Record entry bar spread as baseline
    entry_bar    = bars[entry_idx]
    entry_spread = max(
        float(entry_bar['high']) - float(entry_bar['low']),
        min_spread,
    )
    spread_trigger = entry_spread * spread_multiple

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_close = float(bar['close'])

        bar_spread = bar_high - bar_low

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Spread trigger', bar, bar_close - spread_trigger)

        # Spread widening exit — fills at bar close (we've lost the mid)
        if bar_spread >= spread_trigger and bar_spread >= min_spread:
            return {
                'exitBar':        bar,
                'exitReason':     'hard_stop',
                'stopPrice':      bar_close,
                'exitType':       'spread_widening',
                'entrySpread':    round(entry_spread, 4),
                'exitSpread':     round(bar_spread, 4),
                'spreadMultiple': round(bar_spread / entry_spread, 2),
                'stopTrace':      trace,
                'extraTraces':    extras,
            }

        # Hard stop fallback
        if bar_open <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  bar_open,
                'stopTrace':  trace,
                'extraTraces': extras,
            }
        if bar_low <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  hard_stop,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  hard_stop,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  hard_stop,
        'stopTrace':  trace,
        'extraTraces': extras,
    }
