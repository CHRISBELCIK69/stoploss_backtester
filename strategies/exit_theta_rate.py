# ============================================================
# strategies/exit_theta_rate.py
# Family 3 — Greeks-Based Stops
# Theta decay rate change exit.
#
# HOW IT WORKS:
#   Theta measures daily dollar decay on the option. For long options,
#   theta is negative and accelerates as expiry approaches.
#
#   This strategy exits when theta crosses an absolute threshold OR when
#   the rate of theta change (acceleration) exceeds a limit.
#
#   TWO EXIT SIGNALS:
#
#   SIGNAL 1 — Theta level:
#     Exit when |theta| > thetaLimit (e.g. $0.05/day per contract).
#     The option is now decaying too fast to hold — theta is working
#     against you faster than the underlying can offset it.
#
#   SIGNAL 2 — Theta acceleration:
#     Exit when bar-to-bar |theta| increase > thetaAccelThreshold.
#     Theta acceleration near expiry can go from manageable to brutal
#     in a few bars — this catches the inflection.
#
#   Most useful for same-day and next-day options where theta is
#   the dominant P&L driver in the afternoon session.
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
    'id':           'theta_rate_exit',
    'name':         'Theta rate exit',
    'description':  'Exit when theta level or theta acceleration exceeds threshold. '
                    'Protects against runaway decay near expiry.',
    'params': [
        {'key': 'thetaLimit',          'label': 'Theta level limit ($/day, 0=off)',
         'default': 0.05, 'min': 0.0, 'max': 2.0, 'step': 0.01,
         'hint': 'Exit when |theta| > this (e.g. 0.05 = $5/day decay per contract).'},
        {'key': 'thetaAccelThreshold', 'label': 'Theta acceleration threshold (0=off)',
         'default': 0.01, 'min': 0.0, 'max': 0.5, 'step': 0.001,
         'hint': 'Exit when bar-to-bar |theta| increase exceeds this.'},
        {'key': 'onlyAfterProfitPct',  'label': 'Only fire after profit % (0=always)',
         'default': 0, 'min': 0, 'max': 200, 'step': 5,
         'hint': 'Theta exit only fires once the position is profitable by this %.'},
        {'key': 'hardStopPct',         'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',        'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',             'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['thetaLimit'] <= 0 and params['thetaAccelThreshold'] <= 0:
        return 'At least one of thetaLimit or thetaAccelThreshold must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    theta_limit    = params['thetaLimit']
    accel_thresh   = params['thetaAccelThreshold']
    profit_gate    = params['onlyAfterProfitPct'] / 100
    hard_stop_pct  = params['hardStopPct'] / 100
    r              = params['riskFreeRate'] / 100
    contract       = params.get('_contract', {})
    cache          = params.get('_cache', {})
    cfg            = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')
    hard_stop   = entry_price * (1 - hard_stop_pct)
    profit_target = entry_price * (1 + profit_gate) if profit_gate > 0 else 0

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

    prev_abs_theta  = None
    profit_unlocked = profit_gate == 0
    high_water_mark = entry_price
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
        if not profit_unlocked and high_water_mark >= profit_target:
            profit_unlocked = True

        theta = get_greek(bar, 'theta')
        if theta is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    theta = g.get('theta')

        abs_theta = abs(theta) if theta is not None else None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)
        if abs_theta is not None and theta_limit > 0:
            append_trace(extras, 'Theta limit', bar, theta_limit)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        if profit_unlocked and abs_theta is not None:
            # Signal 1 — theta level
            if theta_limit > 0 and abs_theta > theta_limit:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'theta_level', 'thetaAtExit': round(theta, 6),
                        'stopTrace': trace, 'extraTraces': extras}

            # Signal 2 — theta acceleration
            if accel_thresh > 0 and prev_abs_theta is not None:
                accel = abs_theta - prev_abs_theta
                if accel > accel_thresh:
                    return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                            'exitType': 'theta_acceleration', 'thetaAccel': round(accel, 6),
                            'thetaAtExit': round(theta, 6),
                            'stopTrace': trace, 'extraTraces': extras}

        if abs_theta is not None:
            prev_abs_theta = abs_theta

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'highWaterMark': high_water_mark,
            'stopTrace': trace, 'extraTraces': extras}
