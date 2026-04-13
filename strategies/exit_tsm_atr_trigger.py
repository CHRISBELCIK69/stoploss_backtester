# ============================================================
# strategies/exit_tsm_atr_trigger.py
# TSM ATR Trailing Stop with Trigger (Kaufman - Trend with Trailing Stop)
#
# HOW IT WORKS:
#   The trail only activates after price moves trigger × ATR in your
#   favor from entry. Before activation, a hard stop holds.
#   After trigger: trail = highest_close - stopFactor × ATR.
#
#   ATR is computed from the 1-min bars themselves (intraday ATR).
#   The trigger prevents premature trailing on early noise — the
#   trade must PROVE itself before the trail arms.
# ============================================================

import math
from backtest_engine import to_minutes

META = {
    'enabled':     True,
    'id':          'tsm_atr_trigger',
    'name':        'TSM ATR trigger trail',
    'description': 'Trail activates after price moves trigger×ATR in your favor. '
                   'Then trails at stopFactor×ATR below the high. Kaufman TSM Ch.22.',
    'params': [
        {'key': 'atrPeriod',  'label': 'ATR period (bars)',    'default': 20,   'min': 5,   'max': 100, 'step': 5},
        {'key': 'stopFactor', 'label': 'Trail factor (×ATR)',  'default': 2.5,  'min': 0.5, 'max': 10,  'step': 0.5,
         'hint': 'Stop distance as multiple of ATR'},
        {'key': 'trigger',    'label': 'Trigger (×ATR)',       'default': 2.0,  'min': 0.5, 'max': 10,  'step': 0.5,
         'hint': 'Profit needed to arm the trail — in ATR multiples'},
        {'key': 'hardStopPct', 'label': 'Hard stop before trigger (%)', 'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'eodTime',    'label': 'EOD exit (CST)',       'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['stopFactor'] <= 0:
        return 'Stop factor must be greater than 0'
    if params['trigger'] <= 0:
        return 'Trigger must be greater than 0'
    return None


def _calc_atr(bars, idx, period):
    """Compute ATR from 1-min bars ending at idx."""
    if idx < period:
        return None
    trs = []
    for j in range(idx - period + 1, idx + 1):
        h = float(bars[j]['high'])
        l = float(bars[j]['low'])
        if j > 0:
            prev_c = float(bars[j - 1]['close'])
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        else:
            tr = h - l
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def execute(bars, entry_idx, entry_price, params):
    atr_period  = int(params['atrPeriod'])
    stop_factor = params['stopFactor']
    trigger_mul = params['trigger']
    hard_stop_pct = params['hardStopPct']
    eod_time    = params.get('eodTime', '15:45')

    high_price    = entry_price
    trigger_armed = False
    stop_price    = entry_price * (1 - hard_stop_pct / 100)
    trace         = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_close = float(bar['close'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        high_price = max(high_price, bar_close)

        atr = _calc_atr(bars, i, atr_period)

        was_armed = trigger_armed

        # Check trigger activation
        if atr and not trigger_armed:
            if bar_close - entry_price >= trigger_mul * atr:
                trigger_armed = True

        # Update trail if armed (only if was already armed before this bar)
        if was_armed and trigger_armed and atr:
            new_stop = high_price - stop_factor * atr
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Stop check — skip on the activation bar
        if not trigger_armed or was_armed:
            reason = 'trailing_stop' if trigger_armed else 'hard_stop'

            if bar_open <= stop_price:
                return {'exitBar': bar, 'exitReason': reason, 'stopPrice': bar_open,
                        'highWaterMark': high_price, 'triggerArmed': trigger_armed, 'stopTrace': trace}

            if bar_low <= stop_price:
                return {'exitBar': bar, 'exitReason': reason, 'stopPrice': stop_price,
                        'highWaterMark': high_price, 'triggerArmed': trigger_armed, 'stopTrace': trace}

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'highWaterMark': high_price, 'triggerArmed': trigger_armed, 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'highWaterMark': high_price, 'triggerArmed': trigger_armed, 'stopTrace': trace}
