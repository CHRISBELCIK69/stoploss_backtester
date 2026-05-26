# ============================================================
# strategies/exit_spread_width_pct.py
# Family 7 — Strategy-Specific Stops
# Spread width % stop — unique to vertical spreads.
#
# HOW IT WORKS:
#   A vertical spread has a maximum width = difference between strikes.
#   The spread value oscillates between $0 (full profit) and the max
#   width (full loss). Exit when spread value reaches X% of max width.
#
#   For a CREDIT SPREAD (sold):
#     Entry: received credit (entry_price for the net spread)
#     Max loss: spread_width - credit received
#     Stop fires when current spread value >= stopWidthPct × spread_width
#
#   For a DEBIT SPREAD (bought):
#     Entry: paid debit (entry_price for the net spread)
#     Max profit: spread_width - debit paid
#     Target fires when current spread value >= targetWidthPct × spread_width
#
#   SINGLE-LEG APPROXIMATION:
#   The backtester has one option's bars. spreadWidth is entered as
#   a param (in $ per share). The strategy computes stop/target levels
#   relative to entry_price and spread_width without needing the
#   second leg's live price.
# ============================================================

from backtest_engine import should_eod_exit, append_trace

META = {
    'enabled':  True,
    'id':       'spread_width_stop',
    'name':     'Spread width % stop',
    'description': 'Exit when spread value reaches X% of max spread width. '
                   'Standard vertical spread management — credit and debit spreads.',
    'params': [
        {'key': 'spreadWidth',     'label': 'Spread width ($)',
         'default': 5.0, 'min': 0.5, 'max': 100.0, 'step': 0.5,
         'hint': 'Strike width of the vertical spread in dollars (e.g. 5 for a $5-wide spread).'},
        {'key': 'spreadType',      'label': 'Spread type',
         'default': 'credit',
         'hint': 'credit = sold spread (stop when spread widens). debit = bought spread (target when spreads narrows).'},
        {'key': 'stopWidthPct',    'label': 'Stop at % of spread width',
         'default': 200, 'min': 50, 'max': 1000, 'step': 25,
         'hint': 'credit: exit when spread value reaches this % of width (200 = 2× the width).'},
        {'key': 'targetWidthPct',  'label': 'Target at % of spread width (0=off)',
         'default': 50, 'min': 0, 'max': 100, 'step': 5,
         'hint': 'debit: exit when spread value reaches this % of width (50 = half the width).'},
        {'key': 'hardStopPct',     'label': 'Hard stop on option premium (%)',
         'default': 75, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'eodTime',         'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['spreadWidth'] <= 0:
        return 'Spread width must be > 0'
    if params['spreadType'] not in ('credit', 'debit'):
        return "spreadType must be 'credit' or 'debit'"
    return None


def execute(bars, entry_idx, entry_price, params):
    spread_width   = params['spreadWidth']
    spread_type    = params.get('spreadType', 'credit')
    stop_pct       = params['stopWidthPct'] / 100
    target_pct     = params['targetWidthPct'] / 100
    hard_stop_pct  = params['hardStopPct'] / 100

    # Compute levels in option premium terms
    hard_stop      = entry_price * (1 - hard_stop_pct)

    if spread_type == 'credit':
        # Sold spread: entry_price = net credit received
        # Stop when the spread widens to stopWidthPct × spread_width
        stop_level   = spread_width * stop_pct     # spread VALUE at stop
        target_level = entry_price * (1 - target_pct) if target_pct > 0 else None
        # In terms of the short leg's option premium:
        # We approximate stop as when option premium rises to stop_level
        option_stop  = entry_price + (stop_level - entry_price)  # net move
        option_stop  = max(option_stop, entry_price * 1.5)       # floor at 50% loss
    else:
        # Bought spread: entry_price = net debit paid
        # Target when spread widens to targetWidthPct × spread_width
        option_stop   = hard_stop
        target_level  = spread_width * target_pct if target_pct > 0 else None

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])

        spread_pct_of_width = bar_close / spread_width * 100

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop',     bar, hard_stop)
        append_trace(extras, 'Spread width',  bar, spread_width)
        if target_level:
            append_trace(extras, 'Width target', bar, target_level)

        # Hard stop always active
        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'spreadPctWidth': round(spread_pct_of_width, 1),
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'spreadPctWidth': round(spread_pct_of_width, 1),
                    'stopTrace': trace, 'extraTraces': extras}

        if spread_type == 'credit':
            # Stop: spread has widened beyond stop threshold
            if bar_high >= option_stop:
                return {'exitBar': bar, 'exitReason': 'hard_stop',
                        'stopPrice': option_stop, 'exitType': 'spread_width_stop',
                        'spreadPctWidth': round(spread_pct_of_width, 1),
                        'stopTrace': trace, 'extraTraces': extras}
            # Profit target
            if target_level and bar_low <= target_level:
                return {'exitBar': bar, 'exitReason': 'trailing_stop',
                        'stopPrice': target_level, 'exitType': 'profit_target',
                        'spreadPctWidth': round(spread_pct_of_width, 1),
                        'stopTrace': trace, 'extraTraces': extras}
        else:
            # Debit spread: profit target = spread has widened to targetWidthPct
            if target_level and bar_high >= target_level:
                return {'exitBar': bar, 'exitReason': 'trailing_stop',
                        'stopPrice': target_level, 'exitType': 'spread_width_target',
                        'spreadPctWidth': round(spread_pct_of_width, 1),
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'spreadPctWidth': round(spread_pct_of_width, 1),
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
