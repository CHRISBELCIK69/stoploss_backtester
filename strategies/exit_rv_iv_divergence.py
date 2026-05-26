# ============================================================
# strategies/exit_rv_iv_divergence.py
# Family 5 — Volatility-Based Stops
# Realized vs implied volatility divergence exit.
#
# HOW IT WORKS:
#   Realized Volatility (RV) = annualised stddev of bar-to-bar
#   log returns over the last rvPeriod bars.
#   Implied Volatility (IV) = back-solved from bar close via BS.
#
#   The IV–RV spread is the options market's "vol premium" —
#   how much extra you're paying for options vs what the underlying
#   is actually moving. When this spread collapses or reverses:
#     - IV/RV < divergenceThreshold → IV has crashed below actual movement
#       (option is now cheap vs realized moves — edge is gone)
#     - IV/RV > divergenceThreshold → IV has spiked far above realized
#       (option is now expensive — sell into it)
#
#   exitMode controls which divergence fires:
#     'crush'  — exit when IV/RV drops below threshold (IV crushed)
#     'spike'  — exit when IV/RV rises above threshold (IV expanded)
#     'either' — exit on either divergence
#
#   Hard stop always active.
# ============================================================

import math
from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'rv_iv_divergence',
    'name':         'RV vs IV divergence exit',
    'description':  'Exit when realized vol diverges from implied vol beyond a threshold. '
                    'Detects when the vol premium has collapsed or spiked.',
    'params': [
        {'key': 'rvPeriod',           'label': 'Realized vol lookback (bars)',
         'default': 20, 'min': 5, 'max': 120, 'step': 5,
         'hint': 'Number of 1-min bars to compute realized vol from.'},
        {'key': 'divergenceThreshold','label': 'IV/RV ratio threshold',
         'default': 0.80, 'min': 0.1, 'max': 3.0, 'step': 0.05,
         'hint': 'crush: exit when IV/RV < this. spike: exit when IV/RV > this.'},
        {'key': 'exitMode',           'label': 'Exit mode',
         'default': 'crush',
         'hint': 'crush / spike / either'},
        {'key': 'minBarsWarmup',      'label': 'Min bars before exit fires',
         'default': 15, 'min': 5, 'max': 60, 'step': 5},
        {'key': 'hardStopPct',        'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',       'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',            'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['divergenceThreshold'] <= 0:
        return 'Divergence threshold must be > 0'
    if params['exitMode'] not in ('crush', 'spike', 'either'):
        return "exitMode must be 'crush', 'spike', or 'either'"
    return None


def _realized_vol(bars, current_idx, period):
    """Annualised stddev of log returns over last period bars."""
    start = max(1, current_idx - period + 1)
    log_returns = []
    for j in range(start, current_idx + 1):
        prev_c = float(bars[j - 1]['close'])
        curr_c = float(bars[j]['close'])
        if prev_c > 0 and curr_c > 0:
            log_returns.append(math.log(curr_c / prev_c))
    if len(log_returns) < 3:
        return None
    n    = len(log_returns)
    mean = sum(log_returns) / n
    var  = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    # Annualise: 390 bars/day × 252 trading days
    return math.sqrt(var * 390 * 252)


def execute(bars, entry_idx, entry_price, params):
    rv_period   = int(params['rvPeriod'])
    threshold   = params['divergenceThreshold']
    exit_mode   = params.get('exitMode', 'crush')
    warmup      = int(params['minBarsWarmup'])
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

        rv = _realized_vol(bars, i, rv_period)

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        if bars_since >= warmup and current_iv and rv and rv > 0:
            ratio = current_iv / rv
            crush_fire = (exit_mode in ('crush', 'either') and ratio < threshold)
            spike_fire = (exit_mode in ('spike', 'either') and ratio > (1 / threshold if threshold < 1 else threshold * 1.5))

            if crush_fire or spike_fire:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'iv_crush' if crush_fire else 'iv_spike',
                        'ivAtExit': round(current_iv, 4), 'rvAtExit': round(rv, 4),
                        'ivRvRatio': round(ratio, 3),
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
