# ============================================================
# strategies/exit_or_composite.py
# Family 6 — Composite / Conditional Stops
# Generic OR stop — two configurable conditions.
#
# HOW IT WORKS:
#   A generic two-condition OR composite. Either condition fires exit.
#   Combines any two of the available signal types:
#
#   CONDITION TYPES (set condAType / condBType):
#     'premium_pct'   — exit when option premium drops X% from entry
#     'profit_pct'    — exit when option premium rises X% above entry
#     'delta'         — exit when |delta| reaches threshold
#     'dte'           — exit when DTE <= days
#     'theta_level'   — exit when |theta| > daily $ amount
#     'iv_crush'      — exit when IV drops X% from entry IV
#
#   Each condition has a single threshold param (condAValue / condBValue).
#
#   This lets you express rules like:
#     "Exit at 50% profit OR if delta reaches 0.70" →
#       condAType='profit_pct', condAValue=50,
#       condBType='delta',      condBValue=0.70
#
#     "Exit at 21 DTE OR if IV crushes 40%" →
#       condAType='dte',        condAValue=21,
#       condBType='iv_crush',   condBValue=40
#
#   Hard stop always active as floor regardless of conditions.
# ============================================================

from datetime import datetime
from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_delta, implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'or_composite',
    'name':         'OR composite stop',
    'description':  'Exit when EITHER of two configurable conditions fires. '
                    'Mix any two signal types: premium%, profit%, delta, DTE, theta, IV crush.',
    'params': [
        {'key': 'condAType',   'label': 'Condition A type',
         'default': 'profit_pct',
         'hint': 'premium_pct / profit_pct / delta / dte / theta_level / iv_crush'},
        {'key': 'condAValue',  'label': 'Condition A threshold',
         'default': 50.0, 'min': 0.0, 'max': 10000.0, 'step': 1.0,
         'hint': 'profit_pct=50 means exit at +50%. delta=0.40. dte=21. theta=0.05. iv_crush=30.'},
        {'key': 'condBType',   'label': 'Condition B type',
         'default': 'dte',
         'hint': 'premium_pct / profit_pct / delta / dte / theta_level / iv_crush'},
        {'key': 'condBValue',  'label': 'Condition B threshold',
         'default': 21.0, 'min': 0.0, 'max': 10000.0, 'step': 1.0},
        {'key': 'hardStopPct', 'label': 'Hard stop floor (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate','label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}

VALID_TYPES = ('premium_pct', 'profit_pct', 'delta', 'dte', 'theta_level', 'iv_crush')


def validate(params):
    if params['condAType'] not in VALID_TYPES:
        return f"condAType must be one of: {', '.join(VALID_TYPES)}"
    if params['condBType'] not in VALID_TYPES:
        return f"condBType must be one of: {', '.join(VALID_TYPES)}"
    if params['condAType'] == params['condBType']:
        return 'Condition A and B must be different types'
    return None


def _check_cond(cond_type, cond_value, bar, entry_price, entry_iv,
                delta, theta, current_iv, dte):
    """Returns (fired, exit_reason_suffix) or (False, None)."""
    if cond_type == 'premium_pct':
        # Exit when premium drops X% below entry
        stop = entry_price * (1 - cond_value / 100)
        return float(bar['low']) <= stop, 'premium_stop'

    elif cond_type == 'profit_pct':
        # Exit when premium rises X% above entry
        target = entry_price * (1 + cond_value / 100)
        return float(bar['high']) >= target, 'profit_target'

    elif cond_type == 'delta':
        if delta is None:
            return False, None
        return abs(delta) >= cond_value, 'delta_threshold'

    elif cond_type == 'dte':
        if dte is None:
            return False, None
        return dte <= int(cond_value), 'dte_threshold'

    elif cond_type == 'theta_level':
        if theta is None:
            return False, None
        return abs(theta) > cond_value, 'theta_level'

    elif cond_type == 'iv_crush':
        if current_iv is None or entry_iv is None:
            return False, None
        crush_floor = entry_iv * (1 - cond_value / 100)
        return current_iv < crush_floor, 'iv_crush'

    return False, None


def execute(bars, entry_idx, entry_price, params):
    cond_a_type   = params['condAType']
    cond_a_val    = params['condAValue']
    cond_b_type   = params['condBType']
    cond_b_val    = params['condBValue']
    hard_stop_pct = params['hardStopPct'] / 100
    r             = params['riskFreeRate'] / 100
    contract      = params.get('_contract', {})
    cache         = params.get('_cache', {})
    cfg           = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')
    hard_stop   = entry_price * (1 - hard_stop_pct)

    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    # Solve entry IV for iv_crush condition
    entry_iv    = None
    sigma_guess = 0.5
    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                entry_iv    = sg
                sigma_guess = sg

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])

        # Resolve greeks for any condition that needs them
        delta      = get_greek(bar, 'delta')
        theta      = get_greek(bar, 'theta')
        current_iv = get_greek(bar, 'iv')

        needs_bs = any(ct in (cond_a_type, cond_b_type)
                       for ct in ('delta', 'theta_level', 'iv_crush'))

        if needs_bs and (delta is None or theta is None) and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    current_iv  = sigma
                    from strategies._bs_math import bs_greeks
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    delta = g.get('delta')
                    theta = g.get('theta')

        # DTE
        dte = get_greek(bar, 'dte')
        if dte is None and expiry_date:
            try:
                bd = datetime.strptime(bar['time'][:10], '%Y-%m-%d').date()
                ed = datetime.strptime(expiry_date, '%Y-%m-%d').date()
                dte = max(0, (ed - bd).days)
            except (ValueError, TypeError):
                dte = None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        # Hard stop always active
        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        # Check both conditions (OR logic)
        a_fired, a_reason = _check_cond(cond_a_type, cond_a_val, bar, entry_price,
                                        entry_iv, delta, theta, current_iv, dte)
        b_fired, b_reason = _check_cond(cond_b_type, cond_b_val, bar, entry_price,
                                        entry_iv, delta, theta, current_iv, dte)

        if a_fired or b_fired:
            which   = 'A' if a_fired else 'B'
            reason  = a_reason if a_fired else b_reason
            er_type = 'trailing_stop' if reason == 'profit_target' else 'hard_stop'
            return {'exitBar': bar, 'exitReason': er_type, 'stopPrice': bar_close,
                    'exitType': f'or_cond_{which}_{reason}',
                    'condAFired': a_fired, 'condBFired': b_fired,
                    'deltaAtExit': round(delta, 4) if delta else None,
                    'dteAtExit': dte, 'ivAtExit': round(current_iv, 4) if current_iv else None,
                    'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
