# ============================================================
# strategies/exit_ratio_spread.py
# Family 7 — Strategy-Specific Stops
# Ratio spread exit — net credit turns to debit.
#
# HOW IT WORKS:
#   A ratio spread (e.g. 1×2 call spread) has:
#     - 1 long call at lower strike (cost = longPremium)
#     - 2 short calls at upper strike (credit = 2 × shortPremium)
#     - Net credit = shortPremium × shortRatio - longPremium
#
#   The position is entered for a net credit. The key risk:
#   if the short legs appreciate faster than the long leg,
#   the net position flips from credit to debit.
#
#   EXIT CONDITIONS:
#     1. Net position turns to debit (primary signal):
#        current_net = short_approx - long_approx < 0
#        We approximate: if the option has moved beyond the strike
#        differential × shortRatio, the structure is inverted.
#
#     2. Premium appreciation stop:
#        Exit when current option premium >= entry × maxLossMult
#        "The short leg has moved too far — max loss approaching."
#
#     3. Profit target:
#        Exit when current premium <= entry × (1 - profitTargetPct/100)
#        "The short leg has decayed — lock in the credit."
#
#   SINGLE-LEG APPROXIMATION:
#   We track the short leg premium (entry_price = short leg premium
#   at entry). The ratio and long leg premium are params.
# ============================================================

from backtest_engine import should_eod_exit, append_trace

META = {
    'enabled':  True,
    'id':       'ratio_spread_exit',
    'name':     'Ratio spread exit',
    'description': 'Exit when net credit turns to debit, profit target hit, '
                   'or max loss multiple reached. Ratio spread specific.',
    'params': [
        {'key': 'shortRatio',       'label': 'Short leg ratio (e.g. 2 for 1×2)',
         'default': 2, 'min': 2, 'max': 5, 'step': 1,
         'hint': 'Number of short legs per long leg (2 = 1×2 ratio).'},
        {'key': 'longLegPremium',   'label': 'Long leg premium at entry ($)',
         'default': 1.50, 'min': 0.01, 'max': 50.0, 'step': 0.05,
         'hint': 'Premium paid for the long leg at trade entry.'},
        {'key': 'maxLossMult',      'label': 'Max loss trigger (× entry credit)',
         'default': 3.0, 'min': 1.5, 'max': 10.0, 'step': 0.5,
         'hint': 'Exit when short leg premium reaches this × entry. 3.0 = short has tripled.'},
        {'key': 'profitTargetPct',  'label': 'Profit target (% credit decay, 0=off)',
         'default': 50, 'min': 0, 'max': 100, 'step': 5,
         'hint': '50 = exit when short leg has decayed to 50% of entry premium.'},
        {'key': 'hardStopPct',      'label': 'Hard stop on short leg (%)',
         'default': 200, 'min': 50, 'max': 500, 'step': 25},
        {'key': 'eodTime',          'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['shortRatio'] < 2:
        return 'Short ratio must be >= 2'
    if params['longLegPremium'] <= 0:
        return 'Long leg premium must be > 0'
    if params['maxLossMult'] <= 1.0:
        return 'Max loss multiplier must be > 1.0'
    return None


def execute(bars, entry_idx, entry_price, params):
    short_ratio    = int(params['shortRatio'])
    long_premium   = params['longLegPremium']
    max_loss_mult  = params['maxLossMult']
    profit_pct     = params['profitTargetPct'] / 100
    hard_stop_pct  = params['hardStopPct'] / 100

    # Entry net credit = short_ratio × entry_price - long_premium
    entry_net_credit = short_ratio * entry_price - long_premium

    # Stop levels on the SHORT leg premium
    max_loss_level  = entry_price * max_loss_mult
    hard_stop_level = entry_price * (1 + hard_stop_pct / 100)  # short: higher = worse
    profit_target   = entry_price * (1 - profit_pct) if profit_pct > 0 else None

    # Net-debit flip level: when net position becomes negative
    # net = short_ratio × current_short - long_approx
    # Approximate long_approx as moving proportionally with short
    # At ratio inversion: short_ratio × current ≈ long_premium
    debit_flip_level = long_premium / short_ratio   # short price where net flips

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])

        # Net P&L estimate
        current_net = short_ratio * bar_close - long_premium

        trace.append({'time': bar['time'], 'stopPrice': max_loss_level})
        append_trace(extras, 'Hard stop',        bar, hard_stop_level)
        append_trace(extras, 'Net debit flip',   bar, debit_flip_level)
        append_trace(extras, 'Max loss trigger', bar, max_loss_level)
        if profit_target:
            append_trace(extras, 'Profit target', bar, profit_target)

        # Hard stop — extreme appreciation
        if bar_high >= hard_stop_level:
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': hard_stop_level,
                    'exitType': 'hard_stop', 'currentNet': round(current_net, 4),
                    'stopTrace': trace, 'extraTraces': extras}

        # Max loss multiplier
        if bar_high >= max_loss_level:
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': max_loss_level,
                    'exitType': 'max_loss_mult',
                    'currentNet': round(current_net, 4),
                    'stopTrace': trace, 'extraTraces': extras}

        # Net credit → debit flip
        if bar_close >= debit_flip_level and current_net <= 0:
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': bar_close,
                    'exitType': 'net_debit_flip',
                    'currentNet': round(current_net, 4),
                    'entryNetCredit': round(entry_net_credit, 4),
                    'stopTrace': trace, 'extraTraces': extras}

        # Profit target — short has decayed
        if profit_target and bar_low <= profit_target:
            return {'exitBar': bar, 'exitReason': 'trailing_stop',
                    'stopPrice': profit_target,
                    'exitType': 'profit_target',
                    'currentNet': round(current_net, 4),
                    'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'currentNet': round(current_net, 4),
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'currentNet': short_ratio * float(bars[-1]['close']) - long_premium,
            'stopTrace': trace, 'extraTraces': extras}
