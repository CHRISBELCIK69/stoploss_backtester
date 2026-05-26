# ============================================================
# strategies/exit_vol_skew_change.py
# Family 5 — Volatility-Based Stops
# Volatility surface skew change exit.
#
# HOW IT WORKS:
#   True vol surface skew requires multiple strikes simultaneously.
#   We approximate intraday skew change from DIRECTION and RATE
#   of IV movement vs price movement.
#
#   The key insight: on a normal vol surface, IV rises when the
#   underlying falls (negative skew / put skew). If IV and the
#   underlying are moving in the SAME direction, skew is changing —
#   the market's fear structure has shifted.
#
#   TWO SIGNALS:
#
#   SIGNAL 1 — IV/price correlation flip:
#     Normally IV rises when the underlying falls (negative correlation).
#     When IV and the underlying move together for skewFlipBars
#     consecutive bars, skew has flipped — exit.
#
#   SIGNAL 2 — IV acceleration vs price:
#     When the rate of IV change is disproportionate to the underlying
#     move (ivToMoveRatio), something unusual is happening to the vol
#     surface. Exit before the repricing works against you.
#
#   Hard stop always active.
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
    'id':           'vol_skew_change',
    'name':         'Vol skew change exit',
    'description':  'Exit when vol surface skew structure changes. '
                    'Approximated from IV direction vs underlying direction.',
    'params': [
        {'key': 'skewFlipBars',  'label': 'Skew flip bars to confirm',
         'default': 3, 'min': 1, 'max': 10, 'step': 1,
         'hint': 'Consecutive bars where IV and underlying move together (abnormal correlation).'},
        {'key': 'ivToMoveRatio', 'label': 'IV acceleration ratio threshold (0=off)',
         'default': 3.0, 'min': 0.0, 'max': 20.0, 'step': 0.5,
         'hint': '0=off. >0: exit when |IV_change %| / |underlying_change %| > this ratio.'},
        {'key': 'minBarsWarmup', 'label': 'Min bars before exit fires',
         'default': 10, 'min': 0, 'max': 30, 'step': 1},
        {'key': 'hardStopPct',   'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',  'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',       'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['skewFlipBars'] < 1:
        return 'Skew flip bars must be >= 1'
    return None


def execute(bars, entry_idx, entry_price, params):
    flip_bars     = int(params['skewFlipBars'])
    iv_ratio      = params['ivToMoveRatio']
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

    sigma_guess   = 0.5
    flip_streak   = 0
    prev_iv       = None
    prev_spot     = None

    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                sigma_guess = sg
                prev_iv     = sg
            prev_spot = es

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar        = bars[i]
        bar_open   = float(bar['open'])
        bar_close  = float(bar['close'])
        bar_low    = float(bar['low'])
        bars_since = i - entry_idx

        current_iv   = get_greek(bar, 'iv')
        current_spot = spot_at(spot_idx, bar['time']) if have_spot else None

        if current_iv is None and have_spot and K > 0 and current_spot:
            T = years_to_expiry(bar['time'], expiry_date)
            sigma = implied_vol(bar_close, current_spot, K, T, r, opt_type,
                                initial_guess=sigma_guess)
            if sigma > 0:
                sigma_guess = sigma
                current_iv  = sigma

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        if bars_since >= warmup and current_iv and prev_iv and current_spot and prev_spot:
            iv_chg   = current_iv   - prev_iv
            spot_chg = current_spot - prev_spot

            # Signal 1 — skew flip: IV and underlying moving same direction
            # (normal: IV rises when underlying falls → opposite signs)
            if spot_chg != 0 and iv_chg != 0:
                same_direction = (iv_chg > 0 and spot_chg > 0) or \
                                 (iv_chg < 0 and spot_chg < 0)
                flip_streak = flip_streak + 1 if same_direction else 0

                if flip_streak >= flip_bars:
                    return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                            'exitType': 'skew_flip', 'flipStreak': flip_streak,
                            'ivAtExit': round(current_iv, 4),
                            'stopTrace': trace, 'extraTraces': extras}

            # Signal 2 — IV acceleration vs price move
            if iv_ratio > 0 and spot_chg != 0 and prev_spot > 0 and prev_iv > 0:
                spot_pct = abs(spot_chg / prev_spot)
                iv_pct   = abs(iv_chg / prev_iv)
                if spot_pct > 0 and iv_pct / spot_pct > iv_ratio:
                    return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                            'exitType': 'iv_acceleration', 'ivSpotRatio': round(iv_pct / spot_pct, 2),
                            'ivAtExit': round(current_iv, 4),
                            'stopTrace': trace, 'extraTraces': extras}

        if current_iv:
            prev_iv = current_iv
        if current_spot:
            prev_spot = current_spot

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
