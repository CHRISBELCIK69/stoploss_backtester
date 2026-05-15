# ============================================================
# strategies/exit_tsm_stddev.py
# TSM StdDev Trailing Stop (Kaufman - Moving Average Stop Loss)
#
# HOW IT WORKS:
#   Tracks the highest close since entry. Stop = high_price - factor × stddev.
#   StdDev is computed from bar-to-bar close changes over a lookback window.
#   The wider the recent volatility, the wider the stop.
#   As vol contracts, the stop tightens automatically.
#
#   Unlike a fixed % trail, this adapts in real-time to how the
#   option is actually moving — volatile days get a wider leash,
#   quiet days get a tight leash.
# ============================================================

import math
from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'tsm_stddev',
    'name':        'TSM StdDev trailing',
    'description': 'Stop = highest close - factor × stddev(close changes). '
                   'Adapts to real-time volatility. Kaufman TSM Ch.22.',
    'params': [
        {'key': 'lookback',  'label': 'StdDev lookback (bars)', 'default': 30,  'min': 5,  'max': 200, 'step': 5,
         'hint': 'Number of 1-min bars to compute stddev from'},
        {'key': 'stopFactor', 'label': 'Stop factor',           'default': 3.0, 'min': 0.5, 'max': 20, 'step': 0.5,
         'hint': 'Multiplier on stddev — higher = wider stop'},
        {'key': 'hardStopPct', 'label': 'Hard stop before warmup (%)', 'default': 50, 'min': 5, 'max': 100, 'step': 5,
         'hint': 'Fixed stop until enough bars for stddev computation'},
        {'key': 'eodTime',   'label': 'EOD exit (CST)',         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['stopFactor'] <= 0:
        return 'Stop factor must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    lookback    = int(params['lookback'])
    stop_factor = params['stopFactor']
    hard_stop_pct = params['hardStopPct']
    eod_time    = params.get('eodTime', '15:45')

    high_price  = entry_price
    stop_price  = entry_price * (1 - hard_stop_pct / 100)
    trace       = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        high_price = max(high_price, bar_close)

        # Compute stddev of close-to-close changes once we have enough bars
        window_start = max(0, i - lookback + 1)
        if i - window_start >= 5:  # need at least 5 bars
            changes = []
            for j in range(window_start + 1, i + 1):
                changes.append(float(bars[j]['close']) - float(bars[j - 1]['close']))
            if changes:
                mean_c = sum(changes) / len(changes)
                variance = sum((c - mean_c) ** 2 for c in changes) / max(1, len(changes) - 1)
                stddev = math.sqrt(variance)
                new_stop = high_price - stop_factor * stddev
                if new_stop > stop_price:
                    stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_open,
                    'highWaterMark': high_price, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': stop_price,
                    'highWaterMark': high_price, 'stopTrace': trace}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'highWaterMark': high_price, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'highWaterMark': high_price, 'stopTrace': trace}
