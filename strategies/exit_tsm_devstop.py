# ============================================================
# strategies/exit_tsm_devstop.py
# TSM DevStop - Tiered Standard Deviation Stop (Kaufman / Kase)
#
# HOW IT WORKS:
#   Uses a 2-bar true range and its stddev to create 3 tiered stops:
#     Level 1 (nearest):  entry - (avg_DTR + 1.0 × sd)
#     Level 2 (middle):   entry - (avg_DTR + 2.2 × sd)
#     Level 3 (widest):   entry - (avg_DTR + 3.6 × sd)
#
#   All 3 levels are computed at entry and stay fixed.
#   The reported stop is Level 2 (middle) — exits if bar low touches it.
#   Level 1 is a "warning" line, Level 3 is the catastrophic stop.
#
#   This is a FIXED stop — it never trails. Its purpose is to set
#   a volatility-adjusted initial stop that's smarter than a flat %.
# ============================================================

import math
from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'tsm_devstop',
    'name':        'TSM DevStop (Kase)',
    'description': '3-tier stop based on 2-bar true range + stddev. '
                   'Fixed at entry — adapts to vol. Kaufman/Kase.',
    'params': [
        {'key': 'dtrPeriod', 'label': 'DTR lookback (bars)',    'default': 20, 'min': 5, 'max': 100, 'step': 5,
         'hint': 'Period for averaging the 2-bar true range'},
        {'key': 'stopLevel', 'label': 'Stop level (1/2/3)',     'default': 2,  'min': 1, 'max': 3,   'step': 1,
         'hint': '1=tight (1.0σ), 2=mid (2.2σ), 3=wide (3.6σ)'},
        {'key': 'eodTime',   'label': 'EOD exit (CST)',         'default': '15:45', 'type': 'time'},
    ],
}

SIGMA_FACTORS = {1: 1.0, 2: 2.2, 3: 3.6}


def validate(params):
    if params['stopLevel'] not in (1, 2, 3):
        return 'Stop level must be 1, 2, or 3'
    return None


def execute(bars, entry_idx, entry_price, params):
    dtr_period  = int(params['dtrPeriod'])
    stop_level  = int(params['stopLevel'])
    eod_time    = params.get('eodTime', '15:45')
    sigma_mul   = SIGMA_FACTORS[stop_level]

    # Compute 2-bar true range series leading up to entry
    dtrs = []
    for j in range(max(2, entry_idx - dtr_period * 2), entry_idx + 1):
        if j < 2:
            continue
        h   = float(bars[j]['high'])
        l_2 = float(bars[j - 2]['low'])
        c_2 = float(bars[j - 2]['close'])
        dtr = max(h - l_2, abs(h - c_2), abs(float(bars[j]['low']) - c_2))
        dtrs.append(dtr)

    if len(dtrs) >= dtr_period:
        recent = dtrs[-dtr_period:]
        avg_dtr = sum(recent) / len(recent)
        mean_d  = sum(recent) / len(recent)
        var_d   = sum((d - mean_d) ** 2 for d in recent) / max(1, len(recent) - 1)
        sd      = math.sqrt(var_d)
        stop_price = entry_price - (avg_dtr + sigma_mul * sd)
    else:
        # Not enough data — fall back to 50% hard stop
        stop_price = entry_price * 0.50

    # DevStop levels for trace annotation
    all_levels = {}
    if len(dtrs) >= dtr_period:
        recent = dtrs[-dtr_period:]
        avg_dtr = sum(recent) / len(recent)
        mean_d  = sum(recent) / len(recent)
        var_d   = sum((d - mean_d) ** 2 for d in recent) / max(1, len(recent) - 1)
        sd      = math.sqrt(var_d)
        for lv, sf in SIGMA_FACTORS.items():
            all_levels[f'devStop{lv}'] = round(entry_price - (avg_dtr + sf * sd), 4)

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    **all_levels, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': stop_price,
                    **all_levels, 'stopTrace': trace}

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    **all_levels, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            **all_levels, 'stopTrace': trace}
