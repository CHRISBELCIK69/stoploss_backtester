# ============================================================
# strategies/exit_delta_divergence.py
# Family 6 — Composite / Conditional Stops
# Correlation-based exit — delta divergence.
#
# HOW IT WORKS:
#   For a correctly-priced option, the option's bar-to-bar price
#   change should approximately equal:
#       expected_move = delta × underlying_move
#
#   When the actual option move DIVERGES significantly from what
#   delta predicts, something unusual is happening:
#     - Market makers repricing (vol surface shift)
#     - Liquidity event (wide bid/ask, stale quotes)
#     - Gamma effect on large moves
#
#   DIVERGENCE SIGNAL:
#     actual_move    = bar_close - prev_option_close
#     expected_move  = delta × (spot - prev_spot)
#     residual       = actual_move - expected_move
#     residual_pct   = |residual| / entry_price
#
#   Exit when residual_pct > divergenceThreshold for confirmBars
#   consecutive bars — the option is no longer tracking the
#   underlying as expected.
#
#   Hard stop always active.
# ============================================================

from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_delta, implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'delta_divergence',
    'name':         'Delta divergence exit',
    'description':  'Exit when option price diverges from delta-predicted move. '
                    'Detects vol surface shifts and liquidity events.',
    'params': [
        {'key': 'divergenceThreshold', 'label': 'Divergence threshold (% of entry)',
         'default': 5.0, 'min': 0.5, 'max': 30.0, 'step': 0.5,
         'hint': 'Exit when |actual_move - delta×spot_move| > this % of entry price.'},
        {'key': 'confirmBars',         'label': 'Confirmation bars',
         'default': 2, 'min': 1, 'max': 10, 'step': 1,
         'hint': 'Consecutive divergence bars needed to fire exit.'},
        {'key': 'minBarsWarmup',       'label': 'Min bars before exit fires',
         'default': 5, 'min': 0, 'max': 30, 'step': 1},
        {'key': 'hardStopPct',         'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',        'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',             'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['divergenceThreshold'] <= 0:
        return 'Divergence threshold must be > 0'
    if params['confirmBars'] < 1:
        return 'Confirm bars must be >= 1'
    return None


def execute(bars, entry_idx, entry_price, params):
    div_thresh    = params['divergenceThreshold'] / 100
    confirm_bars  = int(params['confirmBars'])
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

    sigma_guess    = 0.5
    div_streak     = 0
    prev_opt_close = entry_price
    prev_spot      = None

    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                sigma_guess = sg
            prev_spot = es

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar        = bars[i]
        bar_open   = float(bar['open'])
        bar_close  = float(bar['close'])
        bar_low    = float(bar['low'])
        bars_since = i - entry_idx

        current_spot = spot_at(spot_idx, bar['time']) if have_spot else None
        delta        = get_greek(bar, 'delta')

        if delta is None and have_spot and K > 0 and current_spot:
            T = years_to_expiry(bar['time'], expiry_date)
            sigma = implied_vol(bar_close, current_spot, K, T, r, opt_type,
                                initial_guess=sigma_guess)
            if sigma > 0:
                sigma_guess = sigma
                delta = bs_delta(current_spot, K, T, r, sigma, opt_type)

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        # Compute divergence
        if (bars_since >= warmup and delta is not None and
                current_spot is not None and prev_spot is not None):
            actual_move   = bar_close - prev_opt_close
            expected_move = abs(delta) * (current_spot - prev_spot)
            residual      = abs(actual_move - expected_move)
            residual_pct  = residual / entry_price if entry_price > 0 else 0

            if residual_pct > div_thresh:
                div_streak += 1
            else:
                div_streak = 0

            if div_streak >= confirm_bars:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'delta_divergence', 'divergenceStreak': div_streak,
                        'residualPct': round(residual_pct * 100, 2),
                        'deltaAtExit': round(delta, 4),
                        'stopTrace': trace, 'extraTraces': extras}

        prev_opt_close = bar_close
        if current_spot:
            prev_spot = current_spot

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
