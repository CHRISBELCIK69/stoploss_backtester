# ============================================================
# strategies/exit_vix_trigger.py
# Family 5 — Volatility-Based Stops
# VIX level trigger exit.
#
# HOW IT WORKS:
#   VIX is not available in Polygon 1-min option bars. This strategy
#   provides two modes:
#
#   MODE 'proxy':
#     Approximates VIX from the intraday realized vol of the underlying
#     bars (SPY/QQQ), annualised and scaled to match VIX convention.
#     proxy_vix = rolling_stddev(underlying_log_returns, period) × sqrt(252 × 390) × 100
#     This gives a number comparable to VIX (e.g. 15, 20, 35).
#     Exit when proxy_vix crosses vixLevel in the specified direction.
#
#   MODE 'manual':
#     You enter the VIX level at entry (entryVIX param) and a threshold
#     percent change (vixChangePct). Exit when implied movement from
#     the option's IV has changed by that % vs your entry VIX reading.
#     This lets you approximate a VIX trigger without a live VIX feed.
#
#   EXIT DIRECTIONS:
#     'above' — exit when VIX rises above vixLevel (fear spike)
#     'below' — exit when VIX falls below vixLevel (vol crush)
#     'either' — exit on either breach
#
#   Hard stop always active.
# ============================================================

import math
from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'vix_trigger',
    'name':         'VIX level trigger',
    'description':  'Exit on VIX-proxy level breach. Approximates VIX from '
                    'underlying realized vol or entry VIX param.',
    'params': [
        {'key': 'vixMode',     'label': 'VIX mode',
         'default': 'proxy',
         'hint': 'proxy = compute from underlying bars. manual = use entryVIX param.'},
        {'key': 'vixLevel',    'label': 'VIX trigger level',
         'default': 25.0, 'min': 5.0, 'max': 100.0, 'step': 1.0,
         'hint': 'proxy: exit when proxy-VIX crosses this level. manual: baseline VIX level.'},
        {'key': 'vixDirection','label': 'Exit direction',
         'default': 'above',
         'hint': 'above = fear spike exit. below = vol crush exit. either = both.'},
        {'key': 'vixChangePct','label': 'VIX change % trigger (manual mode)',
         'default': 30, 'min': 5, 'max': 200, 'step': 5,
         'hint': 'manual mode: exit when VIX proxy has changed by this % from entry.'},
        {'key': 'rvPeriod',    'label': 'Realized vol lookback (bars)',
         'default': 20, 'min': 5, 'max': 60, 'step': 5,
         'hint': 'proxy mode: bars used to compute rolling realized vol.'},
        {'key': 'entryVIX',    'label': 'VIX at entry (manual mode)',
         'default': 18.0, 'min': 5.0, 'max': 100.0, 'step': 0.5,
         'hint': 'manual mode: your recorded VIX when you entered the trade.'},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate','label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['vixMode'] not in ('proxy', 'manual'):
        return "vixMode must be 'proxy' or 'manual'"
    if params['vixDirection'] not in ('above', 'below', 'either'):
        return "vixDirection must be 'above', 'below', or 'either'"
    if params['vixLevel'] <= 0:
        return 'VIX level must be > 0'
    return None


def _proxy_vix(bars, current_idx, period):
    """Annualised realised vol × 100, scaled like VIX."""
    start = max(1, current_idx - period + 1)
    lr = []
    for j in range(start, current_idx + 1):
        pc = float(bars[j - 1]['close'])
        cc = float(bars[j]['close'])
        if pc > 0 and cc > 0:
            lr.append(math.log(cc / pc))
    if len(lr) < 3:
        return None
    n = len(lr)
    m = sum(lr) / n
    v = sum((r - m) ** 2 for r in lr) / (n - 1)
    return math.sqrt(v * 390 * 252) * 100   # annualised %, VIX-like


def execute(bars, entry_idx, entry_price, params):
    vix_mode      = params.get('vixMode', 'proxy')
    vix_level     = params['vixLevel']
    vix_dir       = params.get('vixDirection', 'above')
    vix_chg_pct   = params['vixChangePct'] / 100
    rv_period     = int(params['rvPeriod'])
    entry_vix     = params['entryVIX']
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

    # In proxy mode: use underlying bars for RV computation
    # In manual mode: use option IV vs entry_vix
    use_underlying_rv = (vix_mode == 'proxy') and have_spot

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])

        # Compute VIX proxy
        if use_underlying_rv and underlying_bars:
            # Build a matching index into underlying_bars by time
            ub_closes = []
            bar_time  = bar['time'][:16]
            for ub in underlying_bars:
                if ub['time'][:16] <= bar_time:
                    ub_closes.append(float(ub['close']))
            if len(ub_closes) > rv_period:
                ub_slice = ub_closes[-rv_period - 1:]
                lr = []
                for j in range(1, len(ub_slice)):
                    pc, cc = ub_slice[j - 1], ub_slice[j]
                    if pc > 0 and cc > 0:
                        lr.append(math.log(cc / pc))
                if len(lr) >= 3:
                    n = len(lr)
                    m = sum(lr) / n
                    v = sum((r2 - m) ** 2 for r2 in lr) / (n - 1)
                    proxy_vix = math.sqrt(v * 390 * 252) * 100
                else:
                    proxy_vix = None
            else:
                proxy_vix = _proxy_vix(bars, i, rv_period)
        elif vix_mode == 'manual':
            # Use IV change as VIX proxy in manual mode
            current_iv = get_greek(bar, 'iv')
            if current_iv is None and have_spot and K > 0:
                spot = spot_at(spot_idx, bar['time'])
                if spot:
                    T = years_to_expiry(bar['time'], expiry_date)
                    sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                       initial_guess=sigma_guess)
                    if sigma > 0:
                        sigma_guess = sigma
                        current_iv = sigma
            # Convert IV to VIX-like: IV × 100
            proxy_vix = (current_iv * 100) if current_iv else None
        else:
            proxy_vix = _proxy_vix(bars, i, rv_period)

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)
        append_trace(extras, f'VIX level ({vix_level})', bar, hard_stop)

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'vixProxy': proxy_vix, 'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'vixProxy': proxy_vix, 'stopTrace': trace, 'extraTraces': extras}

        if proxy_vix is not None:
            # Determine effective trigger level
            if vix_mode == 'manual':
                trigger_above = entry_vix * (1 + vix_chg_pct)
                trigger_below = entry_vix * (1 - vix_chg_pct)
            else:
                trigger_above = vix_level
                trigger_below = vix_level

            above_fire = (vix_dir in ('above', 'either') and proxy_vix > trigger_above)
            below_fire = (vix_dir in ('below', 'either') and proxy_vix < trigger_below)

            if above_fire or below_fire:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'vix_above' if above_fire else 'vix_below',
                        'vixProxy': round(proxy_vix, 2),
                        'vixTrigger': trigger_above if above_fire else trigger_below,
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'vixProxy': proxy_vix, 'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'stopTrace': trace, 'extraTraces': extras}
