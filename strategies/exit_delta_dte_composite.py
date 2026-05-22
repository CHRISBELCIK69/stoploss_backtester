# ============================================================
# strategies/exit_delta_dte_composite.py
# Family 6 — Composite / Conditional Stops
# AND stop: delta threshold AND DTE threshold
#
# HOW IT WORKS:
#   Exits ONLY when BOTH conditions are true simultaneously:
#     1. |delta| >= deltaThreshold (option has gone sufficiently ITM)
#     2. DTE <= dteDays (we're close enough to expiry to care)
#
#   This is more precise than either condition alone:
#   - Delta alone exits too early if expiry is weeks away
#     (option still has time value to exploit)
#   - DTE alone exits on the right day but ignores whether
#     the option has actually moved ITM or not
#
#   Combined: "exit when the option is deep enough ITM AND we're
#   within the last N days — meaning the delta edge has been
#   captured and time decay risk is now dominant."
#
#   Classic theta spread management: "close at 0.30 delta or 21 DTE,
#   whichever comes first" is the OR version — this is the AND version
#   for traders who want both conditions confirmed before exiting.
# ============================================================

from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_delta, implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'delta_dte_and',
    'name':         'Delta AND DTE exit',
    'description':  'Exit when BOTH delta threshold AND DTE threshold are met. '
                    'More selective than either condition alone.',
    'params': [
        {'key': 'deltaThreshold', 'label': 'Delta threshold (|delta|)',
         'default': 0.40, 'min': 0.05, 'max': 0.95, 'step': 0.05,
         'hint': 'Condition 1: |delta| must reach this level.'},
        {'key': 'dteDays',        'label': 'DTE threshold (days)',
         'default': 21, 'min': 0, 'max': 60, 'step': 1,
         'hint': 'Condition 2: DTE must be at or below this.'},
        {'key': 'hardStopPct',    'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate',   'label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',        'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if not (0 < params['deltaThreshold'] < 1):
        return 'Delta threshold must be between 0 and 1'
    if params['dteDays'] < 0:
        return 'DTE days must be >= 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    delta_target  = params['deltaThreshold']
    dte_days      = int(params['dteDays'])
    hard_stop_pct = params['hardStopPct'] / 100
    r             = params['riskFreeRate'] / 100
    contract      = params.get('_contract', {})
    cache         = params.get('_cache', {})
    cfg           = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')
    hard_stop   = entry_price * (1 - hard_stop_pct)

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

    trace = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])

        delta = get_greek(bar, 'delta')
        dte   = get_greek(bar, 'dte')

        if delta is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    delta = bs_delta(spot, K, T, r, sigma, opt_type)

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop',      bar, hard_stop)
        append_trace(extras, 'Delta target',   bar, delta_target)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'stopTrace': trace, 'extraTraces': extras}

        # AND condition: both must be true
        cond_delta = delta is not None and abs(delta) >= delta_target
        cond_dte   = dte is not None and dte <= dte_days

        if cond_delta and cond_dte:
            return {
                'exitBar':       bar,
                'exitReason':    'trailing_stop',
                'stopPrice':     bar_close,
                'exitType':      'delta_AND_dte',
                'deltaAtExit':   round(delta, 4),
                'dteAtExit':     dte,
                'stopTrace':     trace,
                'extraTraces':   extras,
            }

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
