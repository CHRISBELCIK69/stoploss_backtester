# ============================================================
# strategies/exit_tsm_volspike.py
# TSM Volatility Spike Exit (Kaufman - MA Volatility Exits)
#
# HOW IT WORKS:
#   Exits when the current bar's return is extreme relative to
#   recent volatility AND the move is in your favor (profitable spike).
#
#   Condition: rolling_stddev(returns) > exitAbove × |bar_return|
#              AND the bar moved in the profitable direction
#
#   This catches "blow-off tops" — a violent profitable move that
#   signals the momentum is exhausted. You take the gift and exit.
#   Think of it as selling into strength rather than waiting for
#   the reversal to eat your gains.
#
#   Falls back to a hard stop if no vol spike occurs.
# ============================================================

import math
from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'tsm_volspike',
    'name':        'TSM volatility spike exit',
    'description': 'Exits on profitable volatility spikes — sells into strength. '
                   'Hard stop as fallback. Kaufman TSM Ch.22.',
    'params': [
        {'key': 'volPeriod',  'label': 'Vol lookback (bars)',    'default': 20,  'min': 5,  'max': 100, 'step': 5},
        {'key': 'exitAbove',  'label': 'Spike threshold',       'default': 3.0, 'min': 1,  'max': 10,  'step': 0.5,
         'hint': 'Exit when vol > this × |return| on a profitable bar'},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',        'default': 50,  'min': 5,  'max': 100, 'step': 5},
        {'key': 'eodTime',    'label': 'EOD exit (CST)',        'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['exitAbove'] <= 0:
        return 'Spike threshold must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    vol_period  = int(params['volPeriod'])
    exit_above  = params['exitAbove']
    hard_stop_pct = params['hardStopPct']
    eod_time    = params.get('eodTime', '15:45')

    stop_price  = entry_price * (1 - hard_stop_pct / 100)
    trace       = []

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])
        bar_mins  = to_minutes(bar['time'][11:16])

        prev_close = float(bars[i - 1]['close'])
        bar_return = (bar_close / prev_close - 1) if prev_close > 0 else 0

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Compute rolling vol of returns
        window_start = max(entry_idx + 1, i - vol_period + 1)
        if i - window_start >= 5:
            returns = []
            for j in range(window_start, i + 1):
                pc = float(bars[j - 1]['close'])
                if pc > 0:
                    returns.append(float(bars[j]['close']) / pc - 1)
            if len(returns) >= 2:
                mean_r  = sum(returns) / len(returns)
                var_r   = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
                vol     = math.sqrt(var_r)

                # Profitable spike: vol is huge AND the move was in our favor (long → up)
                if abs(bar_return) > 0 and vol > exit_above * abs(bar_return) and bar_return > 0:
                    return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_close,
                            'highWaterMark': bar_close, 'spikeVol': round(vol, 6),
                            'spikeReturn': round(bar_return, 6), 'stopTrace': trace}

        # Hard stop fallback
        if bar_open <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'highWaterMark': entry_price, 'stopTrace': trace}

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': stop_price,
                    'highWaterMark': entry_price, 'stopTrace': trace}

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'highWaterMark': entry_price, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'highWaterMark': entry_price, 'stopTrace': trace}
