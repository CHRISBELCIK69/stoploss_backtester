# ============================================================
# strategies/exit_diagonal_roll.py
# Family 7 — Strategy-Specific Stops
# Diagonal spread roll trigger.
#
# HOW IT WORKS:
#   A diagonal spread = long far-dated option + short near-dated option.
#   The short leg needs to be rolled when it becomes too risky.
#
#   ROLL TRIGGER CONDITIONS (any fires):
#
#   CONDITION 1 — Short leg delta breach:
#     When the short leg's |delta| reaches shortDeltaTrigger, the short
#     leg is too deep ITM and needs to be rolled to a new strike.
#     This is the primary roll signal for a diagonal.
#
#   CONDITION 2 — Short leg DTE:
#     When DTE <= shortDteDays, the short leg is too close to expiry
#     to manage cleanly. Roll before pin risk kicks in.
#
#   CONDITION 3 — Short leg premium appreciation:
#     When the short leg's current premium >= entry × rollPremiumMult,
#     rolling becomes necessary to prevent max-loss scenario.
#
#   In the backtester: we track the SHORT leg only (the option in the bar
#   feed). The "roll trigger" exit reason indicates the position needs
#   to be restructured rather than closed. P&L represents the short leg.
# ============================================================

from datetime import datetime
from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_delta, implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'diagonal_roll',
    'name':         'Diagonal roll trigger',
    'description':  'Signal to roll short leg: fires on delta breach, DTE threshold, '
                    'or premium appreciation. Specific to diagonal spread management.',
    'params': [
        {'key': 'shortDeltaTrigger',  'label': 'Short leg delta trigger (|delta|)',
         'default': 0.70, 'min': 0.30, 'max': 0.95, 'step': 0.05,
         'hint': 'Roll when |delta| reaches this. 0.70 = roll when short is 70-delta.'},
        {'key': 'shortDteDays',       'label': 'Short leg DTE trigger (days)',
         'default': 7, 'min': 0, 'max': 30, 'step': 1,
         'hint': 'Roll when DTE reaches this many days.'},
        {'key': 'rollPremiumMult',    'label': 'Roll premium trigger (× entry, 0=off)',
         'default': 2.0, 'min': 0.0, 'max': 5.0, 'step': 0.1,
         'hint': 'Roll when short premium reaches this × entry credit. 2.0 = double.'},
        {'key': 'hardStopPct',        'label': 'Hard stop on short premium (%)',
         'default': 300, 'min': 100, 'max': 500, 'step': 25,
         'hint': 'Close entire position if short premium reaches this % above entry.'},
        {'key': 'riskFreeRate',       'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',            'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if not (0 < params['shortDeltaTrigger'] < 1):
        return 'Delta trigger must be between 0 and 1'
    return None


def _calc_dte(bar_time, expiry):
    try:
        bd = datetime.strptime(bar_time[:10], '%Y-%m-%d').date()
        ed = datetime.strptime(expiry, '%Y-%m-%d').date()
        return max(0, (ed - bd).days)
    except (ValueError, TypeError):
        return None


def execute(bars, entry_idx, entry_price, params):
    delta_trigger  = params['shortDeltaTrigger']
    dte_trigger    = int(params['shortDteDays'])
    prem_mult      = params['rollPremiumMult']
    hard_stop_pct  = params['hardStopPct'] / 100
    r              = params['riskFreeRate'] / 100
    contract       = params.get('_contract', {})
    cache          = params.get('_cache', {})
    cfg            = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')

    hard_stop     = entry_price * (1 + hard_stop_pct)  # short leg: higher = worse
    roll_prem_lvl = entry_price * prem_mult if prem_mult > 0 else None

    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    sigma_guess = 0.5
    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                sigma_guess = sg

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])
        dte       = _calc_dte(bar['time'], expiry_date)

        delta = get_greek(bar, 'delta')
        if delta is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    delta = bs_delta(spot, K, T, r, sigma, opt_type)

        trace.append({'time': bar['time'], 'stopPrice': entry_price})
        append_trace(extras, 'Hard close level', bar, hard_stop)
        if roll_prem_lvl:
            append_trace(extras, 'Roll premium trigger', bar, roll_prem_lvl)

        # Hard stop — close everything
        if bar_high >= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': hard_stop, 'exitType': 'hard_close',
                    'dteAtExit': dte, 'deltaAtExit': delta,
                    'stopTrace': trace, 'extraTraces': extras}

        # Roll condition 1 — delta breach
        if delta is not None and abs(delta) >= delta_trigger:
            return {'exitBar': bar, 'exitReason': 'trailing_stop',
                    'stopPrice': bar_close, 'exitType': 'roll_delta',
                    'deltaAtExit': round(delta, 4), 'dteAtExit': dte,
                    'stopTrace': trace, 'extraTraces': extras}

        # Roll condition 2 — DTE threshold
        if dte is not None and dte <= dte_trigger:
            if should_eod_exit(bar, params):
                return {'exitBar': bar, 'exitReason': 'eod',
                        'stopPrice': bar_close, 'exitType': 'roll_dte',
                        'dteAtExit': dte, 'deltaAtExit': delta,
                        'stopTrace': trace, 'extraTraces': extras}

        # Roll condition 3 — premium appreciation
        if roll_prem_lvl and bar_high >= roll_prem_lvl:
            return {'exitBar': bar, 'exitReason': 'hard_stop',
                    'stopPrice': roll_prem_lvl, 'exitType': 'roll_premium',
                    'premiumAtExit': bar_close, 'dteAtExit': dte,
                    'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'dteAtExit': dte, 'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
