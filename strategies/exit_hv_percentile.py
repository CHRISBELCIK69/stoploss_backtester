# ============================================================
# strategies/exit_hv_percentile.py
# Family 5 — Volatility-Based Stops
# Historical volatility percentile shift exit.
#
# HOW IT WORKS:
#   Tracks a rolling window of realized volatility (HV) readings
#   computed from bar-to-bar returns. Builds an intraday HV
#   distribution and computes a percentile rank.
#
#   HV PERCENTILE = what % of prior HV readings are below current HV
#     - 0 = lowest HV seen today
#     - 100 = highest HV seen today
#
#   EXIT MODES:
#     'low_vol_exit'  — exit when HV percentile < hvThreshold
#                       (vol has compressed — breakout/trend may be over)
#     'high_vol_exit' — exit when HV percentile > hvThreshold
#                       (vol has expanded — regime has shifted, risk up)
#     'regime_shift'  — exit on EITHER extreme (either extreme signals
#                       a regime change from what you entered in)
#
#   This is the intraday equivalent of "exit at 20th HV percentile"
#   used by systematic vol traders.
# ============================================================

import math
from backtest_engine import should_eod_exit, append_trace

META = {
    'enabled':  True,
    'id':       'hv_percentile_exit',
    'name':     'HV percentile exit',
    'description': 'Exit when rolling intraday HV percentile crosses a threshold. '
                   'Detects vol regime shifts from entry conditions.',
    'params': [
        {'key': 'hvPeriod',    'label': 'HV computation window (bars)',
         'default': 20, 'min': 5, 'max': 120, 'step': 5,
         'hint': 'Bars of returns used to compute each HV reading.'},
        {'key': 'rankWindow',  'label': 'Percentile rank window (readings)',
         'default': 30, 'min': 10, 'max': 200, 'step': 10,
         'hint': 'Number of HV readings kept for percentile calculation.'},
        {'key': 'hvThreshold', 'label': 'HV percentile threshold (0–100)',
         'default': 20, 'min': 1, 'max': 99, 'step': 1,
         'hint': 'low_vol_exit: exit when below this. high_vol_exit: exit when above this.'},
        {'key': 'exitMode',    'label': 'Exit mode',
         'default': 'high_vol_exit',
         'hint': 'low_vol_exit / high_vol_exit / regime_shift'},
        {'key': 'minBarsWarmup', 'label': 'Min bars before exit fires',
         'default': 20, 'min': 5, 'max': 60, 'step': 5},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if not (0 < params['hvThreshold'] < 100):
        return 'HV threshold must be between 1 and 99'
    if params['exitMode'] not in ('low_vol_exit', 'high_vol_exit', 'regime_shift'):
        return "exitMode must be 'low_vol_exit', 'high_vol_exit', or 'regime_shift'"
    return None


def _hv(bars, current_idx, period):
    start = max(1, current_idx - period + 1)
    lr = []
    for j in range(start, current_idx + 1):
        pc = float(bars[j - 1]['close'])
        cc = float(bars[j]['close'])
        if pc > 0 and cc > 0:
            lr.append(math.log(cc / pc))
    if len(lr) < 3:
        return None
    n = len(lr)
    m = sum(lr) / n
    v = sum((r - m) ** 2 for r in lr) / (n - 1)
    return math.sqrt(v * 390 * 252)


def execute(bars, entry_idx, entry_price, params):
    hv_period  = int(params['hvPeriod'])
    rank_win   = int(params['rankWindow'])
    threshold  = params['hvThreshold']
    exit_mode  = params.get('exitMode', 'high_vol_exit')
    warmup     = int(params['minBarsWarmup'])
    hard_stop  = entry_price * (1 - params['hardStopPct'] / 100)

    hv_history = []
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar        = bars[i]
        bar_open   = float(bar['open'])
        bar_close  = float(bar['close'])
        bar_low    = float(bar['low'])
        bars_since = i - entry_idx

        hv = _hv(bars, i, hv_period)
        if hv is not None:
            hv_history.append(hv)
            if len(hv_history) > rank_win:
                hv_history.pop(0)

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        if bars_since >= warmup and hv is not None and len(hv_history) >= 5:
            below = sum(1 for v in hv_history if v < hv)
            pct   = below / len(hv_history) * 100

            low_fire  = exit_mode in ('low_vol_exit',  'regime_shift') and pct < threshold
            high_fire = exit_mode in ('high_vol_exit', 'regime_shift') and pct > (100 - threshold)

            if low_fire or high_fire:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'hv_low' if low_fire else 'hv_high',
                        'hvAtExit': round(hv, 4), 'hvPctRank': round(pct, 1),
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
