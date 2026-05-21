# ============================================================
# strategies/exit_premium_pct.py
# Family 1 — Premium-Based Stops
# Variant: % of premium paid stop loss
#
# HOW IT WORKS:
#   Stop is set at entry × (1 - stopPct / 100).
#   If the option premium falls to that level at any bar's low,
#   you exit at the stop price. Gap-through opens fill at open.
#
#   Covers all 6 variants (25 / 50 / 75 / 100 / 150 / 200%)
#   via the stopPct param — register once, configure per variant.
#
#   At 100% stopPct the stop is $0.00 — the option must go to zero
#   before triggering. At 200% it can never trigger (you'd need to
#   lose more than you paid, which is impossible). So 25/50/75/100
#   are the practical variants.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'premium_pct_stop',
    'name':        'Premium % stop',
    'description': 'Exit when premium falls X% from entry price. '
                   'Covers 25/50/75/100% loss variants via stopPct param.',
    'params': [
        {
            'key':     'stopPct',
            'label':   'Stop loss (% of premium paid)',
            'default': 50,
            'min':     1,
            'max':     100,
            'step':    5,
            'hint':    '50 = exit when premium falls 50% below entry (e.g. $2.00 entry → exit at $1.00)',
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
    if not (1 <= params['stopPct'] <= 100):
        return 'Stop % must be between 1 and 100'
    return None


def execute(bars, entry_idx, entry_price, params):
    stop_pct   = params['stopPct'] / 100
    stop_price = entry_price * (1 - stop_pct)
    trace      = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        if bar_open <= stop_price:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  bar_open,
                'stopTrace':  trace,
            }
        if bar_low <= stop_price:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  stop_price,
                'stopTrace':  trace,
            }
        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  stop_price,
                'stopTrace':  trace,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  stop_price,
        'stopTrace':  trace,
    }
