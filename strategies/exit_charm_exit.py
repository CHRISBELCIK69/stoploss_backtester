# ============================================================
# strategies/exit_charm_exit.py
# Family 3 — Greeks-Based Stops
# Charm-based exit near expiration.
#
# HOW IT WORKS:
#   Charm = dDelta/dT — the rate at which delta decays per calendar day.
#   For long calls: charm is negative (delta falling as time passes).
#   For long puts: charm is positive (|delta| falling as time passes).
#
#   Near expiry, charm accelerates violently for near-the-money options:
#     - An ATM call at 1 DTE can have charm of -0.20/day
#     - By 0DTE that same option may have charm of -0.80/day
#     - This means delta is dropping 0.20 per day just from time passage
#
#   TWO EXIT SIGNALS:
#
#   SIGNAL 1 — Charm level:
#     |charm| > charmThreshold. The option's delta is now decaying fast
#     enough that holding overnight becomes very risky.
#
#   SIGNAL 2 — DTE gate:
#     Exit when DTE drops to or below dteDays AND |charm| is elevated.
#     Combines calendar (DTE) with greek severity — more precise than
#     DTE alone (avoids exiting early when charm is still manageable).
#
#   Hard stop always active as floor.
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
    'id':           'charm_exit',
    'name':         'Charm-based exit',
    'description':  'Exit when charm (dDelta/dT) exceeds threshold near expiry. '
                    'Catches runaway delta decay before it destroys the premium.',
    'params': [
        {'key': 'charmThreshold', 'label': 'Charm threshold (|charm|/day)',
         'default': 0.10, 'min': 0.01, 'max': 1.0, 'step': 0.01,
         'hint': '0.10 = exit when delta is decaying at 0.10/day just from time passage.'},
        {'key': 'dteTrigger',     'label': 'Only fire within DTE (0=always)',
         'default': 5, 'min': 0, 'max': 30, 'step': 1,
         'hint': '5 = charm exit only activates when 5 or fewer days remain.'},
        {'key': 'requireProfit',  'label': 'Require profitable position first',
         'default': False, 'type': 'boolean',
         'hint': 'If True, charm exit only fires after position has been profitable.'},
        {'key': 'hardStopPct',    'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',   'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',        'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['charmThreshold'] <= 0:
        return 'Charm threshold must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    charm_thresh   = params['charmThreshold']
    dte_trigger    = int(params['dteTrigger'])
    require_profit = params.get('requireProfit', False)
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

    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    sigma_guess  = 0.5
    was_profitable = False
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

        if bar_close > entry_price:
            was_profitable = True

        charm = get_greek(bar, 'charm')
        dte   = get_greek(bar, 'dte')

        if charm is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    charm = g.get('charm')
                    dte   = g.get('dte') if dte is None else dte

        abs_charm = abs(charm) if charm is not None else None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'charmAtExit': charm, 'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'charmAtExit': charm, 'stopTrace': trace, 'extraTraces': extras}

        if abs_charm is not None and abs_charm >= charm_thresh:
            # DTE gate — only fire within dteTrigger days
            dte_ok = (dte_trigger == 0) or (dte is not None and dte <= dte_trigger)
            profit_ok = (not require_profit) or was_profitable

            if dte_ok and profit_ok:
                return {
                    'exitBar':     bar,
                    'exitReason':  'hard_stop',
                    'stopPrice':   bar_close,
                    'exitType':    'charm_threshold',
                    'charmAtExit': round(charm, 6),
                    'dteAtExit':   dte,
                    'stopTrace':   trace,
                    'extraTraces': extras,
                }

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'charmAtExit': charm, 'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
