# ============================================================
# strategies/exit_profit_target_pct.py
# Family 1 — Premium-Based Stops
# Variant: % of max profit reached
#
# HOW IT WORKS:
#   You define a max profit target (e.g. 200% above entry).
#   The strategy exits when the option reaches targetPct% of that
#   max profit. So at targetPct=50 and maxProfitPct=200:
#     max_profit_price = entry × 3.0 (entry + 200%)
#     exit_target      = entry + 50% × (max_profit - entry)
#                      = entry × 2.0 (i.e. +100% above entry)
#
#   This is the standard "exit at 50% of max profit" structure
#   used in theta strategies, iron condors, spreads.
#
#   A hard stop below entry protects the downside — the position
#   can still be stopped out before target is reached.
#
#   Covers 25 / 50 / 75% of max profit variants via targetPct.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'profit_target_pct',
    'name':        'Max profit % target',
    'description': 'Exit when premium reaches X% of its defined max profit target. '
                   'Covers 25/50/75% of max profit variants.',
    'params': [
        {
            'key':     'targetPct',
            'label':   '% of max profit to take',
            'default': 50,
            'min':     10,
            'max':     100,
            'step':    5,
            'hint':    '50 = exit when you have captured 50% of the defined max profit',
        },
        {
            'key':     'maxProfitPct',
            'label':   'Max profit definition (% above entry)',
            'default': 200,
            'min':     10,
            'max':     1000,
            'step':    10,
            'hint':    'What you define as "full" profit — e.g. 200 means 3× your entry price',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (%)',
            'default': 50,
            'min':     1,
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
    if not (1 <= params['targetPct'] <= 100):
        return 'Target % must be between 1 and 100'
    if params['maxProfitPct'] <= 0:
        return 'Max profit % must be greater than 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    target_pct      = params['targetPct'] / 100
    max_profit_pct  = params['maxProfitPct'] / 100
    hard_stop_pct   = params['hardStopPct'] / 100

    max_profit_price = entry_price * (1 + max_profit_pct)
    exit_target      = entry_price + target_pct * (max_profit_price - entry_price)
    hard_stop        = entry_price * (1 - hard_stop_pct)

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Profit target', bar, exit_target)
        append_trace(extras, 'Max profit',    bar, max_profit_price)

        # Profit target hit — fill at target (or open if gapped above)
        if bar_high >= exit_target:
            fill = exit_target if bar_open < exit_target else bar_open
            return {
                'exitBar':        bar,
                'exitReason':     'trailing_stop',
                'stopPrice':      fill,
                'exitTarget':     exit_target,
                'maxProfitPrice': max_profit_price,
                'stopTrace':      trace,
                'extraTraces':    extras,
            }

        # Hard stop hit
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
