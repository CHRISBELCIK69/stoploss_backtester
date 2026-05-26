# ============================================================
# strategies/exit_regime_stop.py
# Family 6 — Composite / Conditional Stops
# Dynamic regime stop — adjusts trail based on IV rank.
#
# HOW IT WORKS:
#   Uses rolling intraday IV rank (same as iv_rank_exit) to
#   select between a wide and a tight trailing stop.
#
#   LOW IV REGIME  (IV rank < ivLowThreshold):
#     → Use wideTrailPct. Low vol = price moves are smaller =
#       a tight stop would whipsaw you out on noise.
#
#   HIGH IV REGIME (IV rank > ivHighThreshold):
#     → Use tightTrailPct. High vol = momentum is real and fast =
#       a tight stop protects gains before the reversal.
#
#   NEUTRAL REGIME (between thresholds):
#     → Interpolate trail width linearly between wide and tight.
#
#   This is the systematic version of "widen stops in choppy markets,
#   tighten stops in trending markets."
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
    'id':           'regime_stop',
    'name':         'IV regime stop',
    'description':  'Trail width adjusts dynamically with IV rank. '
                    'Wide in low-vol, tight in high-vol regimes.',
    'params': [
        {'key': 'ivLowThreshold',  'label': 'Low IV regime (rank < this)',
         'default': 30, 'min': 5, 'max': 50, 'step': 5},
        {'key': 'ivHighThreshold', 'label': 'High IV regime (rank > this)',
         'default': 70, 'min': 50, 'max': 95, 'step': 5},
        {'key': 'wideTrailPct',    'label': 'Wide trail (low IV) %',
         'default': 35, 'min': 5, 'max': 80, 'step': 5},
        {'key': 'tightTrailPct',   'label': 'Tight trail (high IV) %',
         'default': 10, 'min': 1, 'max': 40, 'step': 1},
        {'key': 'ivWindow',        'label': 'IV rank window (bars)',
         'default': 30, 'min': 5, 'max': 120, 'step': 5},
        {'key': 'hardStopPct',     'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',    'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',         'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['ivLowThreshold'] >= params['ivHighThreshold']:
        return 'Low IV threshold must be < high IV threshold'
    if params['tightTrailPct'] >= params['wideTrailPct']:
        return 'Tight trail must be < wide trail'
    return None


def _iv_rank(current_iv, iv_window):
    lo, hi = min(iv_window), max(iv_window)
    if hi <= lo:
        return 50.0
    return (current_iv - lo) / (hi - lo) * 100


def execute(bars, entry_idx, entry_price, params):
    iv_low    = params['ivLowThreshold']
    iv_high   = params['ivHighThreshold']
    wide_pct  = params['wideTrailPct']  / 100
    tight_pct = params['tightTrailPct'] / 100
    iv_win    = int(params['ivWindow'])
    hard_stop = entry_price * (1 - params['hardStopPct'] / 100)
    r         = params['riskFreeRate'] / 100
    contract  = params.get('_contract', {})
    cache     = params.get('_cache', {})
    cfg       = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')

    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    sigma_guess     = 0.5
    iv_history      = []
    high_water_mark = entry_price
    stop_price      = hard_stop

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
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        current_iv = get_greek(bar, 'iv')
        if current_iv is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    current_iv  = sigma

        if current_iv:
            iv_history.append(current_iv)
            if len(iv_history) > iv_win:
                iv_history.pop(0)

        # Determine trail width from IV rank
        if len(iv_history) >= 5 and current_iv:
            rank = _iv_rank(current_iv, iv_history)
            if rank <= iv_low:
                trail_pct = wide_pct
                regime    = 'low_vol'
            elif rank >= iv_high:
                trail_pct = tight_pct
                regime    = 'high_vol'
            else:
                # Linear interpolation between low and high thresholds
                t = (rank - iv_low) / (iv_high - iv_low)
                trail_pct = wide_pct - t * (wide_pct - tight_pct)
                regime    = 'neutral'
        else:
            trail_pct = wide_pct
            regime    = 'warmup'

        new_stop = high_water_mark * (1 - trail_pct)
        if new_stop > stop_price:
            stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        append_trace(extras, 'Hard stop floor', bar, hard_stop)

        if bar_open <= max(stop_price, hard_stop):
            fill = bar_open
            reason = 'hard_stop' if bar_open <= hard_stop else 'trailing_stop'
            return {'exitBar': bar, 'exitReason': reason, 'stopPrice': fill,
                    'regime': regime, 'highWaterMark': high_water_mark,
                    'stopTrace': trace, 'extraTraces': extras}

        active_stop = max(stop_price, hard_stop)
        if bar_low <= active_stop:
            reason = 'hard_stop' if active_stop == hard_stop else 'trailing_stop'
            return {'exitBar': bar, 'exitReason': reason, 'stopPrice': active_stop,
                    'regime': regime, 'highWaterMark': high_water_mark,
                    'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'regime': regime, 'highWaterMark': high_water_mark,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'highWaterMark': high_water_mark,
            'stopTrace': trace, 'extraTraces': extras}
