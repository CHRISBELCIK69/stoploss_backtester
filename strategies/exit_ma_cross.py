# ============================================================
# strategies/exit_ma_cross.py
# Family 2 — Underlying Price Stops
# Variant: Moving average cross (20 / 50 / 200 day)
#
# HOW IT DIFFERS FROM exit_tsm_bollinger.py:
#   tsm_bollinger — mean-reversion TAKE-PROFIT. Exits when
#   price returns to MA from above. Uses option premium MA.
#   Works as a "the move is exhausted" signal.
#
#   THIS FILE — directional STOP-LOSS based on MA cross.
#   Exits when the option premium closes BELOW its rolling MA
#   and STAYS below for confirmBars consecutive bars, indicating
#   the trend has flipped. Additionally requires the option to
#   have been above the MA at some point (avoids triggering
#   immediately on entries below the MA).
#
#   The maPeriod param maps to the "day" variants:
#     maPeriod = 20  → 20-bar MA (fast — reacts to intraday moves)
#     maPeriod = 50  → 50-bar MA (medium — ~1 hour of 1-min bars)
#     maPeriod = 200 → 200-bar MA (slow — ~3.3 hours of 1-min bars)
#
#   NOTE: On 1-min bars "200-day MA" becomes "200-bar MA" = ~3.3 hrs
#   of intraday data. This is intentional — the MA period controls
#   the smoothing window, not literal calendar days. For true
#   daily MA levels you'd need to fetch daily underlying bars.
#
#   Two phases:
#     PHASE 1 (warmup): hard stop only, MA exit not yet active
#     PHASE 2 (after option crosses above MA once): MA cross
#     can trigger exit on confirmBars closes below MA
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'ma_cross_stop',
    'name':        'MA cross stop',
    'description': 'Exit on N consecutive closes below the rolling MA. '
                   'Covers 20/50/200-bar variants via maPeriod param.',
    'params': [
        {
            'key':     'maPeriod',
            'label':   'MA period (bars)',
            'default': 20,
            'min':     5,
            'max':     200,
            'step':    5,
            'hint':    '20 = fast intraday MA. 50 = ~1hr MA. 200 = slow ~3hr MA.',
        },
        {
            'key':     'confirmBars',
            'label':   'Consecutive closes below MA to exit',
            'default': 2,
            'min':     1,
            'max':     10,
            'step':    1,
            'hint':    '1 = exit immediately on first close below MA. 3 = wait for 3 bars to confirm trend flip.',
        },
        {
            'key':     'requireAboveFirst',
            'label':   'Require option above MA before exit can fire',
            'default': True,
            'type':    'boolean',
            'hint':    'Prevents exiting immediately on entries where price is already below the MA',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (%)',
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
    if params['maPeriod'] < 2:
        return 'MA period must be at least 2'
    if params['confirmBars'] < 1:
        return 'Confirm bars must be at least 1'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    ma_period          = int(params['maPeriod'])
    confirm_bars       = int(params['confirmBars'])
    require_above      = params.get('requireAboveFirst', True)
    hard_stop_pct      = params['hardStopPct'] / 100
    hard_stop          = entry_price * (1 - hard_stop_pct)

    was_above_ma       = False
    bars_below_ma      = 0
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])

        # Rolling MA of close prices
        window_start = max(0, i - ma_period + 1)
        window = [float(bars[j]['close']) for j in range(window_start, i + 1)]
        ma = sum(window) / len(window)

        # Track whether option has ever closed above MA since entry
        if bar_close > ma:
            was_above_ma = True
            bars_below_ma = 0
        else:
            bars_below_ma += 1

        trace.append({'time': bar['time'], 'stopPrice': ma})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        # Hard stop always active
        if bar_open <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  bar_open,
                'maAtExit':   round(ma, 4),
                'stopTrace':  trace,
                'extraTraces': extras,
            }
        if bar_low <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  hard_stop,
                'maAtExit':   round(ma, 4),
                'stopTrace':  trace,
                'extraTraces': extras,
            }

        # MA cross exit
        ma_exit_eligible = (not require_above) or was_above_ma
        if ma_exit_eligible and bars_below_ma >= confirm_bars:
            # Fill at bar close — confirmed cross means close is the realistic fill
            return {
                'exitBar':         bar,
                'exitReason':      'trailing_stop',
                'stopPrice':       bar_close,
                'maAtExit':        round(ma, 4),
                'barsBelowMa':     bars_below_ma,
                'exitType':        'ma_cross',
                'stopTrace':       trace,
                'extraTraces':     extras,
            }

        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  ma,
                'maAtExit':   round(ma, 4),
                'stopTrace':  trace,
                'extraTraces': extras,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  hard_stop,
        'stopTrace':  trace,
        'extraTraces': extras,
    }
