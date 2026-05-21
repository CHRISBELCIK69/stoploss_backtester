# ============================================================
# strategies/exit_time_condition.py
# Family 4 — Time-Based Stops
# Variant: Time + condition combined (DTE OR profit target)
#
# HOW IT WORKS:
#   Two independent exit triggers, whichever fires first:
#
#     TRIGGER A — Time gate:
#       Exit at EOD when DTE drops to or below dteDays.
#       "Don't hold into expiry week."
#
#     TRIGGER B — Profit target:
#       Exit immediately when premium reaches profitTargetPct
#       above entry. "Take the money early if it's there."
#
#   This is the most common theta-based management rule:
#   "Exit at 50% profit OR 21 DTE, whichever comes first."
#
#   Operator is OR — the first condition to fire wins.
#   A hard stop below entry is always active as the floor.
#
#   Standard presets:
#     (21 DTE, 50% profit)  — conservative income
#     (14 DTE, 50% profit)  — balanced
#     (7 DTE, 75% profit)   — aggressive
# ============================================================

from datetime import datetime
from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'time_condition_exit',
    'name':        'Time + condition exit',
    'description': 'Exit at 50% profit OR 21 DTE — whichever comes first. '
                   'The standard theta management rule.',
    'params': [
        {
            'key':     'dteDays',
            'label':   'DTE exit trigger (days)',
            'default': 21,
            'min':     0,
            'max':     60,
            'step':    1,
            'hint':    'Exit at EOD when this many days remain to expiry',
        },
        {
            'key':     'profitTargetPct',
            'label':   'Profit target (% above entry)',
            'default': 50,
            'min':     5,
            'max':     500,
            'step':    5,
            'hint':    'Exit immediately when premium reaches this % above entry',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (%)',
            'default': 100,
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
    if params['dteDays'] < 0:
        return 'DTE days must be >= 0'
    if params['profitTargetPct'] <= 0:
        return 'Profit target % must be greater than 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def _calc_dte(bar_date_str, expiry_str):
    try:
        bar_d    = datetime.strptime(bar_date_str, '%Y-%m-%d').date()
        expiry_d = datetime.strptime(expiry_str,   '%Y-%m-%d').date()
        return (expiry_d - bar_d).days
    except (ValueError, TypeError):
        return None


def execute(bars, entry_idx, entry_price, params):
    dte_days          = int(params['dteDays'])
    profit_target_pct = params['profitTargetPct'] / 100
    hard_stop_pct     = params['hardStopPct'] / 100
    hard_stop         = entry_price * (1 - hard_stop_pct)
    profit_target     = entry_price * (1 + profit_target_pct)

    contract = params.get('_contract', {})
    expiry   = contract.get('expiry', '')

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_date = bar['time'][:10]

        dte = _calc_dte(bar_date, expiry) if expiry else None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Profit target', bar, profit_target)

        # Hard stop
        if bar_open <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  bar_open,
                'dteAtExit':  dte,
                'stopTrace':  trace,
                'extraTraces': extras,
            }
        if bar_low <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  hard_stop,
                'dteAtExit':  dte,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

        # Trigger B — profit target hit (fires intrabar at bar high)
        if bar_high >= profit_target:
            fill = profit_target if bar_open < profit_target else bar_open
            return {
                'exitBar':    bar,
                'exitReason': 'trailing_stop',
                'stopPrice':  fill,
                'exitType':   'profit_target',
                'dteAtExit':  dte,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

        # Trigger A — DTE gate (fires at EOD on threshold day)
        if dte is not None and dte <= dte_days and should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  float(bar['close']),
                'exitType':   'dte_gate',
                'dteAtExit':  dte,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

        elif should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  float(bar['close']),
                'dteAtExit':  dte,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  hard_stop,
        'dteAtExit':  _calc_dte(bars[-1]['time'][:10], expiry) if expiry else None,
        'stopTrace':  trace,
        'extraTraces': extras,
    }
