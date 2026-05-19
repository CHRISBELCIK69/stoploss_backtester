# ============================================================
# strategies/exit_delta_threshold.py
# Delta threshold breach exit.
#
# HOW IT WORKS:
#   Computes Black-Scholes delta every bar from:
#     - Underlying spot price (from pre-fetched underlying bars)
#     - Strike (from OCC symbol)
#     - Time to expiry (from bar timestamp → expiry 16:00 ET)
#     - Implied volatility (Newton-Raphson solve to match option price)
#
#   For CALLS:  exit when delta >= deltaThreshold (option got too ITM,
#               most of the premium move has happened; sell into strength)
#   For PUTS:   exit when |delta| >= deltaThreshold (delta is negative for
#               puts, we compare absolute value)
#
#   Hard stop below entry is always active as catastrophic protection.
#
#   Default threshold: 0.40 (option is ~40 delta — common managed-risk
#   exit point used by many directional options traders).
# ============================================================

from backtest_engine     import to_minutes, should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_delta, implied_vol, years_to_expiry, build_underlying_index, spot_at,
)

META = {
    'enabled':       True,
    'needs_greeks':  True,   # tells main.py to pre-fetch underlying bars
                              # and enrich bar['greeks'] before running
    'id':          'delta_threshold',
    'name':        'Delta threshold exit',
    'description': 'Exit when Black-Scholes delta reaches the threshold. '
                   'Sell into strength once option is "X-delta deep" ITM.',
    'params': [
        {'key': 'deltaThreshold', 'label': 'Delta threshold', 'default': 0.40,
         'min': 0.05, 'max': 0.95, 'step': 0.05,
         'hint': 'Exit when |delta| reaches this level. 0.30 / 0.40 / 0.50 are common.'},
        {'key': 'hardStopPct',    'label': 'Hard stop (%)',   'default': 25,
         'min': 5, 'max': 100, 'step': 5,
         'hint': 'Catastrophic protective stop below entry'},
        {'key': 'riskFreeRate',   'label': 'Risk-free rate (%)', 'default': 5.0,
         'min': 0.0, 'max': 10.0, 'step': 0.25,
         'hint': 'Annual risk-free rate for Black-Scholes'},
        {'key': 'eodTime',        'label': 'EOD exit (CST)',  'default': '15:45',
         'type': 'time'},
    ],
}


def validate(params):
    dt = params['deltaThreshold']
    if dt <= 0 or dt >= 1:
        return 'Delta threshold must be between 0 and 1'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    delta_target  = float(params['deltaThreshold'])
    hard_stop_pct = float(params['hardStopPct'])
    r             = float(params['riskFreeRate']) / 100.0
    contract = params.get('_contract', {})
    cache    = params.get('_cache', {})
    cfg      = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', contract.get('entryDate', ''))
    symbol      = contract.get('symbol', '')

    hard_stop = entry_price * (1 - hard_stop_pct / 100.0)
    trace     = []
    extras    = {}

    # ── Underlying spot data ──
    # Prefer pre-fetched bars from main.py's shared cache. Fallback fetch
    # if missing (single-strategy run via /api/run_one).
    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol and contract.get('entryDate'):
        underlying_bars = fetch_underlying_bars(
            symbol, contract['entryDate'], expiry_date, cfg,
        )
    spot_idx = build_underlying_index(underlying_bars or [])

    # If we still have no spot data, fall back to hard-stop-only behavior
    have_spot = len(spot_idx) > 0

    # IV warm-start: solve once at entry, then use that sigma as a starting
    # guess for subsequent bars (Newton converges much faster from a good
    # initial guess).
    sigma_guess = 0.5
    entry_bar   = bars[entry_idx]
    entry_spot  = spot_at(spot_idx, entry_bar['time']) if have_spot else None
    if entry_spot is not None and K > 0:
        T0 = years_to_expiry(entry_bar['time'], expiry_date)
        sigma_guess = implied_vol(entry_price, entry_spot, K, T0, r, opt_type)
        if sigma_guess <= 0:
            sigma_guess = 0.5

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])

        # Default: trace shows hard stop until we have delta data
        trace_value = hard_stop

        # ── Compute delta ──
        delta_value = None
        if have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot is not None:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma   # warm-start next bar
                    delta_value = bs_delta(spot, K, T, r, sigma, opt_type)

        # Show abs(delta) as trace value (delta line on chart) once available
        if delta_value is not None:
            trace_value = abs(delta_value)
        trace.append({'time': bar['time'], 'stopPrice': trace_value})
        append_trace(extras, 'Hard stop',       bar, hard_stop)
        append_trace(extras, 'Delta target',    bar, delta_target)

        # ── Hard stop (always active) ──
        if bar_open <= hard_stop:
            return {
                'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                'highWaterMark': entry_price, 'deltaAtExit': delta_value,
                'stopTrace': trace, 'extraTraces': extras,
            }
        if bar_low <= hard_stop:
            return {
                'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                'highWaterMark': entry_price, 'deltaAtExit': delta_value,
                'stopTrace': trace, 'extraTraces': extras,
            }

        # ── Delta breach ──
        if delta_value is not None and abs(delta_value) >= delta_target:
            return {
                'exitBar': bar, 'exitReason': 'trailing_stop',
                'stopPrice': bar_close,
                'highWaterMark': entry_price,
                'deltaAtExit': round(delta_value, 4),
                'deltaThreshold': delta_target,
                'stopTrace': trace, 'extraTraces': extras,
            }

        if should_eod_exit(bar, params):
            return {
                'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                'highWaterMark': entry_price, 'deltaAtExit': delta_value,
                'stopTrace': trace, 'extraTraces': extras,
            }

    last = bars[-1]
    return {
        'exitBar': last, 'exitReason': 'expiry',
        'stopPrice': float(last['close']),
        'highWaterMark': entry_price, 'deltaAtExit': None,
        'stopTrace': trace, 'extraTraces': extras,
    }
