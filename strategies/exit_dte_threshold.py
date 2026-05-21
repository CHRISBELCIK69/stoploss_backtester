# ============================================================
# strategies/exit_dte_threshold.py
# Family 4 — Time-Based Stops
# Variant: DTE threshold exit
#
# HOW IT WORKS:
#   Exits the position when the number of calendar days remaining
#   to expiry (DTE) drops to or below dteDays.
#
#   Standard variants: 21 / 14 / 7 / 5 / 2 DTE — configure
#   via the dteDays param. One file, all variants.
#
#   DTE is computed from the contract expiry date stored in
#   params['_contract']['expiry'] (YYYY-MM-DD). The bar date is
#   parsed from bar['time'][:10].
#
#   Combined with a hard stop below entry — the DTE exit fires
#   at the EOD exit time on the threshold day (first bar at/after
#   eodTime whose DTE is <= dteDays). This means the exit fires
#   at end of the threshold day, not at open, giving you the
#   full session if you want it.
#
#   A hardStop below entry stays active every bar — you can
#   always be stopped out on price before DTE fires.
# ============================================================

from datetime import datetime
from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'dte_threshold',
    'name':        'DTE threshold exit',
    'description': 'Exit at end of session when DTE drops to or below dteDays. '
                   'Covers 21/14/7/5/2 DTE variants. Hard stop always active.',
    'params': [
        {
            'key':     'dteDays',
            'label':   'Exit at DTE <=',
            'default': 21,
            'min':     0,
            'max':     60,
            'step':    1,
            'hint':    '21 = exit at end of session when 21 or fewer days remain to expiry',
        },
        {
            'key':     'exitAtOpen',
            'label':   'Exit at open (vs EOD) on DTE day',
            'default': False,
            'type':    'boolean',
            'hint':    'True = exit on the first bar of the DTE day; False = hold to EOD then exit',
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
    if params['dteDays'] < 0:
        return 'DTE days must be >= 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def _calc_dte(bar_date_str, expiry_str):
    """Return calendar days from bar_date to expiry (inclusive of expiry day)."""
    try:
        bar_date    = datetime.strptime(bar_date_str, '%Y-%m-%d').date()
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return (expiry_date - bar_date).days
    except (ValueError, TypeError):
        return None


def execute(bars, entry_idx, entry_price, params):
    dte_days      = int(params['dteDays'])
    exit_at_open  = params.get('exitAtOpen', False)
    hard_stop_pct = params['hardStopPct'] / 100
    hard_stop     = entry_price * (1 - hard_stop_pct)

    contract = params.get('_contract', {})
    expiry   = contract.get('expiry', '')

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])
        bar_date = bar['time'][:10]

        dte = _calc_dte(bar_date, expiry) if expiry else None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        if dte is not None:
            append_trace(extras, f'DTE = {dte}', bar, hard_stop)

        # Hard stop always active
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

        # DTE exit
        if dte is not None and dte <= dte_days:
            if exit_at_open:
                # Exit immediately at open of the DTE threshold day
                return {
                    'exitBar':    bar,
                    'exitReason': 'eod',
                    'stopPrice':  bar_open,
                    'exitType':   'dte_threshold',
                    'dteAtExit':  dte,
                    'stopTrace':  trace,
                    'extraTraces': extras,
                }
            else:
                # Hold to EOD on the threshold day
                if should_eod_exit(bar, params):
                    return {
                        'exitBar':    bar,
                        'exitReason': 'eod',
                        'stopPrice':  float(bar['close']),
                        'exitType':   'dte_threshold',
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
