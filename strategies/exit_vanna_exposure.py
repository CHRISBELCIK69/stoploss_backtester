# ============================================================
# strategies/exit_vanna_exposure.py
# Family 3 — Greeks-Based Stops
# Vanna exposure in directional moves.
#
# HOW IT WORKS:
#   Vanna = dDelta/dSigma = dVega/dS
#   It measures how much delta changes when IV changes, and equivalently
#   how much vega changes when the underlying moves.
#
#   Why it matters for directional options:
#     - A large positive vanna means: when IV rises, your delta rises too
#       (doubly beneficial for a long call on a sell-off)
#     - A large negative vanna means: when IV rises, your delta FALLS
#       (your hedge is working against you)
#
#   PRACTICAL USE CASE:
#   On a large underlying move, IV tends to spike. Vanna tells you whether
#   that IV spike will ADD to or SUBTRACT from your delta. If the IV spike
#   is subtracting (negative vanna for your position), your P&L exposure
#   is worse than delta alone suggests. Exit before the full effect lands.
#
#   EXIT SIGNAL:
#     |vanna| > vannaThreshold AND the implied IV change (from bar-to-bar
#     IV movement) would push delta in the unfavorable direction.
#     If no IV change data: exit purely on |vanna| > threshold.
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
    'id':           'vanna_exposure',
    'name':         'Vanna exposure exit',
    'description':  'Exit when vanna exposure signals that an IV move will work against delta. '
                    'Catches directional positions where IV and delta are misaligned.',
    'params': [
        {'key': 'vannaThreshold', 'label': 'Vanna threshold',
         'default': 0.10, 'min': 0.01, 'max': 2.0, 'step': 0.01,
         'hint': '0.10 = exit when |vanna| exceeds 0.10 (significant IV-delta coupling).'},
        {'key': 'requireAdverse', 'label': 'Only exit when vanna is adverse',
         'default': True, 'type': 'boolean',
         'hint': 'True = only exit if vanna direction would hurt the position. '
                 'False = exit on any high vanna (pure risk management).'},
        {'key': 'minBarsWarmup', 'label': 'Min bars before exit fires',
         'default': 5, 'min': 0, 'max': 30, 'step': 1},
        {'key': 'hardStopPct',   'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',  'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',       'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['vannaThreshold'] <= 0:
        return 'Vanna threshold must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    vanna_thresh    = params['vannaThreshold']
    require_adverse = params.get('requireAdverse', True)
    warmup          = int(params['minBarsWarmup'])
    hard_stop_pct   = params['hardStopPct'] / 100
    r               = params['riskFreeRate'] / 100
    contract        = params.get('_contract', {})
    cache           = params.get('_cache', {})
    cfg             = params.get('_config', {})

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

    prev_iv = None
    trace = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar        = bars[i]
        bar_open   = float(bar['open'])
        bar_close  = float(bar['close'])
        bar_low    = float(bar['low'])
        bars_since = i - entry_idx

        vanna      = get_greek(bar, 'vanna')
        current_iv = get_greek(bar, 'iv')

        if (vanna is None or current_iv is None) and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    current_iv  = sigma
                    g = bs_greeks(spot, K, T, r, sigma, opt_type)
                    vanna = g.get('vanna')

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'vannaAtExit': vanna, 'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'vannaAtExit': vanna, 'stopTrace': trace, 'extraTraces': extras}

        if bars_since >= warmup and vanna is not None and abs(vanna) >= vanna_thresh:
            # Check if adverse: IV is rising AND vanna would hurt our delta
            iv_rising = (prev_iv is not None and current_iv is not None and
                         current_iv > prev_iv)

            if opt_type == 'C':
                # Long call: we want delta to stay high.
                # Adverse vanna for long call = vanna negative AND IV rising
                # (IV up → delta falls when vanna < 0)
                adverse = iv_rising and vanna < 0
            else:
                # Long put: we want |delta| to stay high (delta is negative).
                # Adverse vanna for long put = vanna positive AND IV rising
                # (IV up → delta rises toward 0 when vanna > 0)
                adverse = iv_rising and vanna > 0

            fire = (not require_adverse) or adverse

            if fire:
                return {
                    'exitBar':     bar,
                    'exitReason':  'hard_stop',
                    'stopPrice':   bar_close,
                    'exitType':    'vanna_exposure',
                    'vannaAtExit': round(vanna, 6),
                    'ivAtExit':    round(current_iv, 4) if current_iv else None,
                    'ivRising':    iv_rising,
                    'stopTrace':   trace,
                    'extraTraces': extras,
                }

        if current_iv:
            prev_iv = current_iv

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'vannaAtExit': vanna, 'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
