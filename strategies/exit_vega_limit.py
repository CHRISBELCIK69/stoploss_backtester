# ============================================================
# strategies/exit_vega_limit.py
# Family 3 — Greeks-Based Stops
# Vega exposure limit exit.
#
# HOW IT WORKS:
#   Vega = how much the option gains/loses per 1% IV move.
#   A long option has positive vega — gains when IV rises, loses
#   on IV crush. This strategy exits when vega exposure becomes
#   uncomfortably large relative to a threshold.
#
#   TWO MODES:
#     'absolute'      — exit when |vega| > vegaLimit
#                       e.g. 0.10 = $10 risk per 1% IV drop per contract
#     'vega_to_theta' — exit when |vega/theta| ratio > vegaLimit
#                       ratio = how many minutes of theta you risk per
#                       1% IV move. Ratio > 5 = IV-dominated position.
#
#   EXIT DIRECTION:
#     'rising'  — exit when vega grows above limit (IV expanding,
#                 risk of crush is now high — take profits or cut)
#     'falling' — exit when vega drops below limit (IV already crushed,
#                 the edge is gone — exit before theta finishes you)
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
    'id':           'vega_limit',
    'name':         'Vega exposure limit',
    'description':  'Exit when vega or vega/theta ratio breaches a threshold. '
                    'Protects against IV crush on high-vega positions.',
    'params': [
        {'key': 'vegaLimit',     'label': 'Vega limit',
         'default': 0.10, 'min': 0.01, 'max': 5.0, 'step': 0.01,
         'hint': 'absolute: exit when |vega|>this ($per 1% IV). vega_to_theta: exit when ratio>this.'},
        {'key': 'vegaMode',      'label': 'Vega mode',
         'default': 'absolute',
         'hint': 'absolute or vega_to_theta'},
        {'key': 'exitDirection', 'label': 'Exit when vega is',
         'default': 'rising',
         'hint': 'rising = vega grew above limit. falling = vega dropped below limit.'},
        {'key': 'hardStopPct',   'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',  'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',       'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['vegaLimit'] <= 0:
        return 'Vega limit must be > 0'
    if params['vegaMode'] not in ('absolute', 'vega_to_theta'):
        return "vegaMode must be 'absolute' or 'vega_to_theta'"
    if params['exitDirection'] not in ('rising', 'falling'):
        return "exitDirection must be 'rising' or 'falling'"
    return None


def execute(bars, entry_idx, entry_price, params):
    vega_limit    = params['vegaLimit']
    vega_mode     = params.get('vegaMode', 'absolute')
    exit_dir      = params.get('exitDirection', 'rising')
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

    trace = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])

        vega  = get_greek(bar, 'vega')
        theta = get_greek(bar, 'theta')

        if vega is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    vega  = g.get('vega')
                    theta = g.get('theta')

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        if vega is not None:
            abs_vega = abs(vega)
            if vega_mode == 'absolute':
                metric = abs_vega
            elif vega_mode == 'vega_to_theta' and theta and abs(theta) > 1e-8:
                metric = abs_vega / abs(theta)
            else:
                metric = None

            if metric is not None:
                triggered = (
                    (exit_dir == 'rising'  and metric > vega_limit) or
                    (exit_dir == 'falling' and metric < vega_limit)
                )
                if triggered:
                    return {
                        'exitBar':     bar,
                        'exitReason':  'hard_stop',
                        'stopPrice':   bar_close,
                        'exitType':    f'vega_{exit_dir}',
                        'vegaAtExit':  round(abs_vega, 6),
                        'thetaAtExit': round(abs(theta), 6) if theta else None,
                        'vegaMetric':  round(metric, 4),
                        'stopTrace':   trace,
                        'extraTraces': extras,
                    }

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
