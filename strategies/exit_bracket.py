# ============================================================
# strategies/exit_bracket.py
# Bracket order — fixed take-profit + fixed stop-loss.
#
# HOW IT WORKS:
#   The simplest exit structure: two flat horizontal lines.
#     TP line at entry × (1 + takeProfitPct / 100)
#     SL line at entry × (1 - stopLossPct / 100)
#   Whichever bar high/low touches first wins. Hold to EOD if neither.
#
#   This is the BASELINE every other strategy should beat. If your
#   complex trailing logic can't outperform a fixed bracket, the
#   complexity isn't earning its keep.
#
#   On the chart you see two horizontal levels — TP in green-tint
#   (target), SL in red-tint (safety). The space between them is
#   the "trade corridor."
#
#   The Bracket Lab UI uses this strategy module to run custom
#   TP/SL combinations on demand via the slider controls.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'bracket',
    'name':        'Bracket (TP + SL)',
    'description': 'Fixed take-profit + fixed stop-loss. Whichever hits first wins. '
                   'The baseline structure — beat this with anything more complex.',
    'params': [
        {'key': 'takeProfitPct', 'label': 'Take profit (%)', 'default': 50, 'min': 1, 'max': 500, 'step': 5,
         'hint': 'Exit when price reaches entry × (1 + this %)'},
        {'key': 'stopLossPct',   'label': 'Stop loss (%)',   'default': 25, 'min': 1, 'max': 100, 'step': 5,
         'hint': 'Exit when price drops to entry × (1 - this %)'},
        {'key': 'eodTime',       'label': 'EOD exit (CST)',  'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['takeProfitPct'] <= 0:
        return 'Take profit % must be greater than 0'
    if params['stopLossPct'] <= 0 or params['stopLossPct'] > 100:
        return 'Stop loss % must be between 1 and 100'
    return None


def execute(bars, entry_idx, entry_price, params):
    tp_pct = params['takeProfitPct']
    sl_pct = params['stopLossPct']

    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)

    trace  = []
    extras = {}
    high_water_mark = entry_price

    for i in range(entry_idx + 1, len(bars)):
        bar = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        # Primary trace: the stop line (the live decision level if price falls)
        trace.append({'time': bar['time'], 'stopPrice': sl_price})
        # Extras: both levels as named lines (TP = target/green, SL = safety/red)
        append_trace(extras, 'Take profit', bar, tp_price)
        append_trace(extras, 'Stop loss',   bar, sl_price)

        # Gap-through SL (open already below stop → fill at open, worse than SL)
        if bar_open <= sl_price:
            return {
                'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                'highWaterMark': high_water_mark, 'tpPrice': tp_price, 'slPrice': sl_price,
                'tpHit': False, 'stopTrace': trace, 'extraTraces': extras,
            }

        # Gap-through TP (open above target → fill at open, better than TP)
        if bar_open >= tp_price:
            return {
                'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_open,
                'highWaterMark': high_water_mark, 'tpPrice': tp_price, 'slPrice': sl_price,
                'tpHit': True, 'stopTrace': trace, 'extraTraces': extras,
            }

        # SL hit intrabar
        if bar_low <= sl_price:
            return {
                'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': sl_price,
                'highWaterMark': high_water_mark, 'tpPrice': tp_price, 'slPrice': sl_price,
                'tpHit': False, 'stopTrace': trace, 'extraTraces': extras,
            }

        # TP hit intrabar
        if bar_high >= tp_price:
            return {
                'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': tp_price,
                'highWaterMark': high_water_mark, 'tpPrice': tp_price, 'slPrice': sl_price,
                'tpHit': True, 'stopTrace': trace, 'extraTraces': extras,
            }

        if should_eod_exit(bar, params):
            return {
                'exitBar': bar, 'exitReason': 'eod', 'stopPrice': sl_price,
                'highWaterMark': high_water_mark, 'tpPrice': tp_price, 'slPrice': sl_price,
                'tpHit': False, 'stopTrace': trace, 'extraTraces': extras,
            }

    return {
        'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': sl_price,
        'highWaterMark': high_water_mark, 'tpPrice': tp_price, 'slPrice': sl_price,
        'tpHit': False, 'stopTrace': trace, 'extraTraces': extras,
    }
