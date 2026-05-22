# ============================================================
# strategies/exit_gamma_hardstop_or.py
# Family 6 — Composite / Conditional Stops
# OR stop: gamma spike OR premium hard stop
#
# HOW IT WORKS:
#   Two completely independent exit mechanisms — either fires:
#
#   EXIT A — Premium hard stop:
#     Standard fixed % stop on option premium. Always active.
#     Catches directionally wrong trades before greeks matter.
#
#   EXIT B — Gamma spike:
#     Exit when gamma > gammaThreshold. Catches the binary-risk
#     moment when the option becomes too sensitive to underlying moves.
#     Particularly important in the last 30 minutes of a 0DTE session.
#
#   The OR structure means you always have protection (hard stop)
#   AND get the early exit on binary risk events (gamma spike).
#   This is a conservative Family 6 composite — both conditions are
#   meaningful and independent. No AND logic needed.
# ============================================================

from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_greeks, implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'gamma_or_hardstop',
    'name':         'Gamma spike OR hard stop',
    'description':  'OR composite: exits on gamma spike (binary risk) OR premium hard stop. '
                    'Two independent protection layers.',
    'params': [
        {'key': 'gammaThreshold', 'label': 'Gamma spike threshold',
         'default': 0.12, 'min': 0.01, 'max': 1.0, 'step': 0.01,
         'hint': 'EXIT B: exit when gamma exceeds this.'},
        {'key': 'hardStopPct',    'label': 'Hard stop % (EXIT A)',
         'default': 40, 'min': 5, 'max': 100, 'step': 5,
         'hint': 'EXIT A: fixed premium stop, always active.'},
        {'key': 'gammaOnlyAfterProfitPct', 'label': 'Gamma exit only after profit %',
         'default': 0, 'min': 0, 'max': 200, 'step': 5,
         'hint': '0 = gamma exit always active. >0 = gamma exit only after this profit %.'},
        {'key': 'riskFreeRate',   'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',        'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['gammaThreshold'] <= 0:
        return 'Gamma threshold must be > 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    gamma_thresh     = params['gammaThreshold']
    hard_stop_pct    = params['hardStopPct'] / 100
    gamma_profit_pct = params['gammaOnlyAfterProfitPct'] / 100
    r                = params['riskFreeRate'] / 100
    contract         = params.get('_contract', {})
    cache            = params.get('_cache', {})
    cfg              = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')
    hard_stop   = entry_price * (1 - hard_stop_pct)
    gamma_profit_target = entry_price * (1 + gamma_profit_pct) if gamma_profit_pct > 0 else 0

    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    sigma_guess     = 0.5
    gamma_unlocked  = gamma_profit_pct == 0
    high_water_mark = entry_price

    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                sigma_guess = sg

    trace = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])

        if bar_high > high_water_mark:
            high_water_mark = bar_high
        if not gamma_unlocked and high_water_mark >= gamma_profit_target:
            gamma_unlocked = True

        gamma = get_greek(bar, 'gamma')
        if gamma is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    gamma = g.get('gamma')

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop (EXIT A)', bar, hard_stop)

        # EXIT A — hard stop (always)
        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'exitLeg': 'A_hard_stop', 'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'exitLeg': 'A_hard_stop', 'stopTrace': trace, 'extraTraces': extras}

        # EXIT B — gamma spike (conditional on profit gate)
        if gamma_unlocked and gamma is not None and gamma >= gamma_thresh:
            return {
                'exitBar':      bar,
                'exitReason':   'trailing_stop',
                'stopPrice':    bar_close,
                'exitLeg':      'B_gamma_spike',
                'gammaAtExit':  round(gamma, 6),
                'stopTrace':    trace,
                'extraTraces':  extras,
            }

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
