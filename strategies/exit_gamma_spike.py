# ============================================================
# strategies/exit_gamma_spike.py
# Family 3 — Greeks-Based Stops
# Gamma spike / delta acceleration exit.
#
# HOW IT WORKS:
#   TWO independent signals — either fires the exit:
#
#   SIGNAL 1 — Gamma spike:
#     Gamma measures how fast delta changes per $1 underlying move.
#     High gamma = option P&L can flip violently on the next bar.
#     Near 0DTE expiry, gamma spikes dramatically for ATM options —
#     the position becomes a binary bet.
#     Exit when gamma > gammaThreshold.
#
#   SIGNAL 2 — Delta acceleration:
#     Bar-to-bar change in |delta| > deltaAccelThreshold.
#     A delta moving 0.30 → 0.52 in one bar signals the underlying
#     made a large move and gamma is in effect.
#
#   exitOnSpike controls direction:
#     'profitable' — only exit if spike is in your favor
#     'against'    — only exit if spike is against you
#     'either'     — always exit on spike (default)
#
#   Hard stop on option premium always active as floor.
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
    'id':           'gamma_spike',
    'name':         'Gamma spike exit',
    'description':  'Exit on gamma spike or rapid delta acceleration. '
                    'Catches binary-risk moments near expiry or large underlying moves.',
    'params': [
        {'key': 'gammaThreshold',      'label': 'Gamma spike threshold',
         'default': 0.15, 'min': 0.01, 'max': 1.0, 'step': 0.01,
         'hint': 'Exit when gamma exceeds this. 0.10-0.20 = high gamma for 0DTE ATM.'},
        {'key': 'deltaAccelThreshold', 'label': 'Delta accel threshold (0=off)',
         'default': 0.12, 'min': 0.0, 'max': 1.0, 'step': 0.01,
         'hint': 'Exit when |delta| changes by more than this in a single bar.'},
        {'key': 'exitOnSpike',         'label': 'Exit direction',
         'default': 'either',
         'hint': 'either / profitable / against'},
        {'key': 'hardStopPct',         'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',        'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',             'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['gammaThreshold'] <= 0 and params['deltaAccelThreshold'] <= 0:
        return 'At least one of gammaThreshold or deltaAccelThreshold must be > 0'
    if params['exitOnSpike'] not in ('either', 'profitable', 'against'):
        return "exitOnSpike must be 'either', 'profitable', or 'against'"
    return None


def execute(bars, entry_idx, entry_price, params):
    gamma_thresh  = params['gammaThreshold']
    accel_thresh  = params['deltaAccelThreshold']
    exit_dir      = params.get('exitOnSpike', 'either')
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

    sigma_guess = 0.5
    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                sigma_guess = sg

    prev_delta = None
    trace = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])

        gamma = get_greek(bar, 'gamma')
        delta = get_greek(bar, 'delta')

        if (gamma is None or delta is None) and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    gamma = g.get('gamma')
                    delta = g.get('delta')

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'gammaAtExit': gamma, 'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'gammaAtExit': gamma, 'stopTrace': trace, 'extraTraces': extras}

        profitable = bar_close > entry_price
        fire = (exit_dir == 'either' or
                (exit_dir == 'profitable' and profitable) or
                (exit_dir == 'against' and not profitable))

        if gamma is not None and gamma_thresh > 0 and gamma >= gamma_thresh and fire:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_close,
                    'exitType': 'gamma_spike', 'gammaAtExit': round(gamma, 6),
                    'deltaAtExit': round(delta, 4) if delta else None,
                    'stopTrace': trace, 'extraTraces': extras}

        if delta is not None and prev_delta is not None and accel_thresh > 0:
            accel = abs(abs(delta) - abs(prev_delta))
            if accel >= accel_thresh and fire:
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_close,
                        'exitType': 'delta_acceleration', 'deltaAccel': round(accel, 4),
                        'deltaAtExit': round(delta, 4), 'gammaAtExit': round(gamma, 6) if gamma else None,
                        'stopTrace': trace, 'extraTraces': extras}

        if delta is not None:
            prev_delta = delta

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
