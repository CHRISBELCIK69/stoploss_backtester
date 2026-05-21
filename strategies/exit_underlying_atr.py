# ============================================================
# strategies/exit_underlying_atr.py
# Family 2 — Underlying Price Stops
# Variant: ATR-based stop on underlying (1×, 1.5×, 2× ATR)
#
# HOW THIS DIFFERS FROM exit_atr_stop.py:
#   exit_atr_stop.py — fetches DAILY OPTION bars to compute ATR,
#   then uses that ATR to set a stop on the OPTION PREMIUM price.
#   The ATR is an option-volatility measure.
#
#   THIS FILE — computes ATR from the INTRADAY OPTION BARS
#   themselves (1-min bars, rolling window from entry), then
#   converts that to a stop on the option premium using the
#   atrMultiplier. The rolling intraday ATR adapts bar by bar
#   to how the option is actually moving today — not historical
#   daily vol. This is a true intraday adaptive stop.
#
# HOW IT WORKS:
#   Every bar, recomputes the rolling Average True Range (ATR)
#   from the last atrPeriod 1-minute bars of the option itself.
#   Stop = high_water_mark - atrMultiplier × ATR
#
#   Phase 1 (before profitTargetPct): stop is fixed below entry
#   at the initial ATR-computed distance. Does not trail yet.
#
#   Phase 2 (after profitTargetPct): stop trails the running
#   high by atrMultiplier × current ATR.
#
#   Covers 1×, 1.5×, 2× ATR variants via atrMultiplier param.
#   Lower multiplier = tighter stop = faster exit on pullback.
#   Higher multiplier = more room = better for runners.
#
#   Fallback: if fewer than atrPeriod bars exist, uses
#   hardStopPct as the initial stop until ATR is computable.
# ============================================================

import math
from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'underlying_atr_stop',
    'name':        'Underlying ATR stop (intraday)',
    'description': 'Stop trails at N× rolling intraday ATR below high water mark. '
                   'Covers 1×/1.5×/2× via atrMultiplier. Distinct from daily-ATR atr_stop.',
    'params': [
        {
            'key':     'atrMultiplier',
            'label':   'ATR multiplier (×)',
            'default': 1.5,
            'min':     0.5,
            'max':     5.0,
            'step':    0.5,
            'hint':    '1.0 = tight (exits on small pullbacks), 2.0 = wide (lets runners run)',
        },
        {
            'key':     'atrPeriod',
            'label':   'ATR period (1-min bars)',
            'default': 14,
            'min':     5,
            'max':     60,
            'step':    1,
            'hint':    'Rolling window of 1-min bars to compute ATR from',
        },
        {
            'key':     'profitTargetPct',
            'label':   'Profit target to activate trail (%)',
            'default': 25,
            'min':     5,
            'max':     200,
            'step':    5,
            'hint':    'Trail activates once option is up this % from entry',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop before trail activates (%)',
            'default': 50,
            'min':     5,
            'max':     100,
            'step':    5,
        },
        {
            'key':     'eodTime',
            'label':   'EOD exit (CST)',
            'default': '15:45',
            'type':    'time',
        },
    ],
}


def validate(params):
    if params['atrMultiplier'] <= 0:
        return 'ATR multiplier must be greater than 0'
    if params['atrPeriod'] < 2:
        return 'ATR period must be at least 2'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def _calc_intraday_atr(bars, current_idx, period):
    """Rolling ATR from 1-min bars up to current_idx."""
    start = max(1, current_idx - period + 1)
    trs = []
    for j in range(start, current_idx + 1):
        h = float(bars[j]['high'])
        l = float(bars[j]['low'])
        prev_c = float(bars[j - 1]['close'])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def execute(bars, entry_idx, entry_price, params):
    atr_mult          = params['atrMultiplier']
    atr_period        = int(params['atrPeriod'])
    profit_target_pct = params['profitTargetPct'] / 100
    hard_stop_pct     = params['hardStopPct'] / 100

    profit_target   = entry_price * (1 + profit_target_pct)
    hard_stop       = entry_price * (1 - hard_stop_pct)
    stop_price      = hard_stop
    high_water_mark = entry_price
    trail_active    = False

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        atr = _calc_intraday_atr(bars, i, atr_period)

        was_trail = trail_active

        # Activate trail once profit target hit
        if not trail_active and bar_high >= profit_target:
            trail_active = True

        # Update trail stop if already active before this bar
        if was_trail and atr is not None:
            new_stop = high_water_mark - atr_mult * atr
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        append_trace(extras, 'Hard stop',      bar, hard_stop)
        append_trace(extras, 'Profit target',  bar, profit_target)
        if atr is not None:
            append_trace(extras, f'ATR ({atr_mult}×)', bar,
                         high_water_mark - atr_mult * atr)

        # Skip stop check on trail activation bar
        if not trail_active or was_trail:
            reason = 'trailing_stop' if trail_active else 'hard_stop'

            if bar_open <= stop_price:
                return {
                    'exitBar':        bar,
                    'exitReason':     reason,
                    'stopPrice':      bar_open,
                    'highWaterMark':  high_water_mark,
                    'trailActive':    trail_active,
                    'atrAtExit':      round(atr, 4) if atr else None,
                    'stopTrace':      trace,
                    'extraTraces':    extras,
                }
            if bar_low <= stop_price:
                return {
                    'exitBar':        bar,
                    'exitReason':     reason,
                    'stopPrice':      stop_price,
                    'highWaterMark':  high_water_mark,
                    'trailActive':    trail_active,
                    'atrAtExit':      round(atr, 4) if atr else None,
                    'stopTrace':      trace,
                    'extraTraces':    extras,
                }

        if should_eod_exit(bar, params):
            return {
                'exitBar':       bar,
                'exitReason':    'eod',
                'stopPrice':     stop_price,
                'highWaterMark': high_water_mark,
                'trailActive':   trail_active,
                'stopTrace':     trace,
                'extraTraces':   extras,
            }

    return {
        'exitBar':       bars[-1],
        'exitReason':    'expiry',
        'stopPrice':     stop_price,
        'highWaterMark': high_water_mark,
        'trailActive':   trail_active,
        'stopTrace':     trace,
        'extraTraces':   extras,
    }
