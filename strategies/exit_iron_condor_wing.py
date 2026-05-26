# ============================================================
# strategies/exit_iron_condor_wing.py
# Family 7 — Strategy-Specific Stops
# Iron condor wing stop.
#
# HOW IT WORKS:
#   Standard IC management: exit the entire position when
#   EITHER individual wing premium doubles from credit received.
#   The backtester approximates this using the single-leg option
#   currently in the bar feed.
#
#   ACTUAL IC MANAGEMENT RULES (implemented here):
#     Rule 1 — Wing double: exit when current option premium >=
#       entry_credit × wingMultiple (default 2.0 = "double")
#       This is the standard "close if the wing doubles" rule.
#
#     Rule 2 — Profit target: exit when current premium <=
#       entry_credit × (1 - profitTargetPct/100)
#       Standard "close at 50% of max profit" for an IC.
#
#     Rule 3 — DTE threshold: exit at DTE <= dteDays regardless
#       of P&L (standard "close at 21 DTE" IC management).
#
#   All three are optional — enable/disable via params.
#   First condition to fire wins. Hard stop always active.
#
#   BACKTESTING NOTE:
#   A true IC backtest requires 4 legs. This file manages the
#   SHORT leg of a single vertical spread (the at-risk side).
#   The entry_credit = entry_price of the short leg option.
# ============================================================

from datetime import datetime
from backtest_engine import should_eod_exit, append_trace

META = {
    'enabled':  True,
    'id':       'iron_condor_wing',
    'name':     'Iron condor wing stop',
    'description': 'Exit when short wing premium doubles, profit target hit, '
                   'or DTE threshold reached. Standard IC management rules.',
    'params': [
        {'key': 'wingMultiple',     'label': 'Wing exit (× entry credit)',
         'default': 2.0, 'min': 1.1, 'max': 5.0, 'step': 0.1,
         'hint': '2.0 = exit when short wing premium doubles (standard IC rule).'},
        {'key': 'profitTargetPct',  'label': 'Profit target (% of max profit, 0=off)',
         'default': 50, 'min': 0, 'max': 100, 'step': 5,
         'hint': '50 = exit when premium has decayed to 50% of entry credit.'},
        {'key': 'dteDays',          'label': 'DTE exit threshold (0=off)',
         'default': 21, 'min': 0, 'max': 60, 'step': 1,
         'hint': 'Exit at EOD when DTE reaches this value.'},
        {'key': 'hardStopPct',      'label': 'Hard stop % (on option premium)',
         'default': 200, 'min': 100, 'max': 500, 'step': 25,
         'hint': 'Catastrophic stop — beyond normal wing management. 200=3× credit.'},
        {'key': 'eodTime',          'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['wingMultiple'] <= 1.0:
        return 'Wing multiple must be > 1.0'
    return None


def _calc_dte(bar_time, expiry):
    try:
        bar_d    = datetime.strptime(bar_time[:10], '%Y-%m-%d').date()
        expiry_d = datetime.strptime(expiry, '%Y-%m-%d').date()
        return max(0, (expiry_d - bar_d).days)
    except (ValueError, TypeError):
        return None


def execute(bars, entry_idx, entry_price, params):
    wing_mult      = params['wingMultiple']
    profit_pct     = params['profitTargetPct'] / 100
    dte_days       = int(params['dteDays'])
    hard_stop_pct  = params['hardStopPct'] / 100
    contract       = params.get('_contract', {})
    expiry_date    = contract.get('expiry', '')

    # IC management levels — all relative to short leg credit (entry_price)
    wing_exit_level   = entry_price * wing_mult
    profit_target     = entry_price * (1 - profit_pct) if profit_pct > 0 else None
    hard_stop         = entry_price * (1 + hard_stop_pct)  # IC: losing trade = premium RISING

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])
        dte       = _calc_dte(bar['time'], expiry_date) if expiry_date else None

        # For a short option: higher price = loss. Show wing exit as "stop" on chart.
        trace.append({'time': bar['time'], 'stopPrice': wing_exit_level})
        append_trace(extras, 'Hard stop',      bar, hard_stop)
        append_trace(extras, 'Wing exit (2×)', bar, wing_exit_level)
        if profit_target:
            append_trace(extras, 'Profit target', bar, profit_target)

        # Hard stop — short option blows through wing double
        if bar_high >= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': hard_stop, 'dteAtExit': dte,
                    'stopTrace': trace, 'extraTraces': extras}

        # Wing double — exit at wing exit level
        if bar_high >= wing_exit_level:
            fill = wing_exit_level if bar_open < wing_exit_level else bar_open
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': fill, 'exitType': 'wing_double',
                    'wingMultiple': wing_mult, 'dteAtExit': dte,
                    'stopTrace': trace, 'extraTraces': extras}

        # Profit target — premium has decayed to target
        if profit_target and bar_low <= profit_target:
            return {'exitBar': bar, 'exitReason': 'trailing_stop',
                    'stopPrice': profit_target, 'exitType': 'profit_target',
                    'dteAtExit': dte, 'stopTrace': trace, 'extraTraces': extras}

        # DTE threshold
        if dte_days > 0 and dte is not None and dte <= dte_days:
            if should_eod_exit(bar, params):
                return {'exitBar': bar, 'exitReason': 'eod',
                        'stopPrice': bar_close, 'exitType': 'dte_gate',
                        'dteAtExit': dte, 'stopTrace': trace, 'extraTraces': extras}

        elif should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'dteAtExit': dte, 'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
