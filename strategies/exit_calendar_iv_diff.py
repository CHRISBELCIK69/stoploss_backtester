# ============================================================
# strategies/exit_calendar_iv_diff.py
# Family 7 — Strategy-Specific Stops
# Calendar spread IV differential exit.
#
# HOW IT WORKS:
#   A calendar spread profits when the near-term IV is higher than
#   the far-term IV (positive IV differential = vega spread income).
#   The edge is gone when this differential collapses.
#
#   CALENDAR SPREAD IV EDGE:
#     Entry edge:  near_IV > far_IV  (near term more expensive)
#     Collapse:    near_IV ≈ far_IV  (no more vol differential)
#     Reversal:    near_IV < far_IV  (structure has inverted — lose on vega)
#
#   SINGLE-LEG APPROXIMATION:
#   We have one option's bars (the near-term leg being tracked).
#   The far-term IV is specified as a param (entryFarIV — entered
#   by the user at trade initiation). As the near IV changes
#   intraday, we monitor whether the differential vs the far leg
#   has collapsed beyond ivDiffThreshold.
#
#   TWO EXIT CONDITIONS:
#     1. IV differential collapsed: near_IV - far_IV < ivDiffThreshold
#        The calendar's vega edge has vanished.
#     2. IV differential inverted: near_IV < far_IV - ivInvertThreshold
#        The structure has reversed — now losing on vega.
# ============================================================

from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'calendar_iv_diff',
    'name':         'Calendar IV differential exit',
    'description':  'Exit when near-far IV differential collapses or inverts. '
                    'Detects when calendar spread vega edge has vanished.',
    'params': [
        {'key': 'entryFarIV',       'label': 'Far-term IV at entry (decimal, e.g. 0.25)',
         'default': 0.25, 'min': 0.05, 'max': 2.0, 'step': 0.01,
         'hint': 'The far-leg implied vol when you entered the calendar. Enter as decimal (0.25 = 25%).'},
        {'key': 'ivDiffThreshold',  'label': 'Min IV differential to hold (decimal)',
         'default': 0.02, 'min': 0.0, 'max': 0.20, 'step': 0.005,
         'hint': 'Exit when near_IV - far_IV < this. 0.02 = exit when differential drops below 2%.'},
        {'key': 'ivInvertThreshold','label': 'Inversion exit threshold (decimal, 0=off)',
         'default': 0.01, 'min': 0.0, 'max': 0.10, 'step': 0.005,
         'hint': 'Exit when near_IV < far_IV - this (structure has inverted).'},
        {'key': 'minBarsWarmup',    'label': 'Min bars before exit fires',
         'default': 5, 'min': 0, 'max': 30, 'step': 1},
        {'key': 'hardStopPct',      'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',     'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',          'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['entryFarIV'] <= 0:
        return 'Entry far IV must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    far_iv        = params['entryFarIV']
    diff_thresh   = params['ivDiffThreshold']
    invert_thresh = params['ivInvertThreshold']
    warmup        = int(params['minBarsWarmup'])
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

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar        = bars[i]
        bar_open   = float(bar['open'])
        bar_close  = float(bar['close'])
        bar_low    = float(bar['low'])
        bars_since = i - entry_idx

        near_iv = get_greek(bar, 'iv')
        if near_iv is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    near_iv     = sigma

        iv_diff = (near_iv - far_iv) if near_iv else None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'nearIV': near_iv, 'farIV': far_iv, 'ivDiff': iv_diff,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'nearIV': near_iv, 'farIV': far_iv, 'ivDiff': iv_diff,
                    'stopTrace': trace, 'extraTraces': extras}

        if bars_since >= warmup and iv_diff is not None:
            # Condition 1: differential has collapsed
            if iv_diff < diff_thresh:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'iv_diff_collapsed', 'nearIV': round(near_iv, 4),
                        'farIV': round(far_iv, 4), 'ivDiff': round(iv_diff, 4),
                        'stopTrace': trace, 'extraTraces': extras}

            # Condition 2: structure inverted
            if invert_thresh > 0 and iv_diff < -invert_thresh:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'iv_diff_inverted', 'nearIV': round(near_iv, 4),
                        'farIV': round(far_iv, 4), 'ivDiff': round(iv_diff, 4),
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'nearIV': near_iv, 'ivDiff': iv_diff,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
