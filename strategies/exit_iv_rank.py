# ============================================================
# strategies/exit_iv_rank.py
# Family 3 — Greeks-Based Stops
# IV rank / IV percentile exit.
#
# HOW IT WORKS:
#   Implied vol (IV) is back-solved from each bar's close price by
#   _bs_math.enrich_bars_with_greeks — stored as bar['greeks']['iv'].
#   This gives us a rolling intraday IV series.
#
#   We track a rolling window of IV readings since entry and compute:
#     IV RANK      = (current_IV - window_low) / (window_high - window_low)
#     IV PERCENTILE = fraction of window readings below current_IV
#
#   Both range 0–100. High = IV expanded vs recent history. Low = crushed.
#
#   EXIT MODES:
#     'crush_exit' — exit when IV rank < ivThreshold (IV has collapsed —
#                    vega is working against you, theta accelerating)
#     'spike_exit' — exit when IV rank > ivThreshold (IV has expanded —
#                    sell into elevated vol before it mean-reverts)
#
#   This is the intraday version of the classic "exit at 50% IV rank"
#   theta-spread management rule, adapted for directional options.
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
    'id':           'iv_rank_exit',
    'name':         'IV rank / percentile exit',
    'description':  'Exit when rolling intraday IV rank crosses a threshold. '
                    'Catches IV crush (falling rank) or IV expansion (rising rank).',
    'params': [
        {'key': 'ivThreshold',  'label': 'IV rank threshold (0–100)',
         'default': 25, 'min': 1, 'max': 99, 'step': 1,
         'hint': 'crush_exit: exit when IV rank drops below this. spike_exit: above this.'},
        {'key': 'ivExitMode',   'label': 'Exit mode',
         'default': 'crush_exit',
         'hint': 'crush_exit = exit on IV collapse. spike_exit = exit on IV expansion.'},
        {'key': 'ivMetric',     'label': 'IV metric',
         'default': 'rank',
         'hint': 'rank = IV Rank. percentile = IV Percentile. Both use rolling intraday window.'},
        {'key': 'windowBars',   'label': 'Rolling IV window (bars)',
         'default': 30, 'min': 5, 'max': 120, 'step': 5,
         'hint': 'Number of recent bars used to compute the IV rank window.'},
        {'key': 'minBarsWarmup','label': 'Min bars before exit can fire',
         'default': 10, 'min': 0, 'max': 60, 'step': 5},
        {'key': 'hardStopPct',  'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate', 'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',      'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if not (0 < params['ivThreshold'] < 100):
        return 'IV threshold must be between 1 and 99'
    if params['ivExitMode'] not in ('crush_exit', 'spike_exit'):
        return "ivExitMode must be 'crush_exit' or 'spike_exit'"
    if params['ivMetric'] not in ('rank', 'percentile'):
        return "ivMetric must be 'rank' or 'percentile'"
    return None


def _iv_rank(current_iv, iv_window):
    lo = min(iv_window)
    hi = max(iv_window)
    if hi <= lo:
        return 50.0
    return (current_iv - lo) / (hi - lo) * 100


def _iv_percentile(current_iv, iv_window):
    below = sum(1 for v in iv_window if v < current_iv)
    return below / len(iv_window) * 100


def execute(bars, entry_idx, entry_price, params):
    iv_threshold  = params['ivThreshold']
    exit_mode     = params.get('ivExitMode', 'crush_exit')
    iv_metric     = params.get('ivMetric', 'rank')
    window_bars   = int(params['windowBars'])
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

    iv_window = []
    trace = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])
        bars_since = i - entry_idx

        # Read IV from enriched bar, fallback to live solve
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

        if current_iv and current_iv > 0:
            iv_window.append(current_iv)
            if len(iv_window) > window_bars:
                iv_window.pop(0)

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        # IV rank exit — only after warmup and enough window data
        if bars_since >= warmup and current_iv and len(iv_window) >= 5:
            if iv_metric == 'rank':
                score = _iv_rank(current_iv, iv_window)
            else:
                score = _iv_percentile(current_iv, iv_window)

            triggered = (
                (exit_mode == 'crush_exit' and score < iv_threshold) or
                (exit_mode == 'spike_exit' and score > iv_threshold)
            )
            if triggered:
                return {
                    'exitBar':    bar,
                    'exitReason': 'hard_stop',
                    'stopPrice':  bar_close,
                    'exitType':   f'iv_{exit_mode}',
                    'ivScore':    round(score, 1),
                    'currentIV':  round(current_iv, 4),
                    'ivMetric':   iv_metric,
                    'stopTrace':  trace,
                    'extraTraces': extras,
                }

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
