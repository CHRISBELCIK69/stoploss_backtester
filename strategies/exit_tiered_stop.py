# ============================================================
# strategies/exit_tiered_stop.py
# Family 6 — Composite / Conditional Stops
# Tiered stop — tightens as profit grows.
#
# HOW IT WORKS:
#   Four profit tiers with different trail widths.
#   As the trade moves into greater profit, the stop tightens
#   automatically — protecting more of the gain at each level.
#   Each tier also optionally uses delta to dynamically adjust
#   the trail within that tier.
#
#   TIER STRUCTURE:
#     Tier 0 (before tier1Pct): hardStopPct flat stop
#     Tier 1 (at tier1Pct):     trailPct1 trail
#     Tier 2 (at tier2Pct):     trailPct2 trail (tighter)
#     Tier 3 (at tier3Pct):     trailPct3 trail (tightest)
#
#   DELTA ADJUSTMENT (optional):
#     Within each tier, the trail can be scaled by live delta.
#     trail = tier_pct × (1 - deltaWeight × abs_delta)
#     At delta=0.50 and deltaWeight=0.5: trail shrinks by 25%.
#     At delta=0.90 (deep ITM): trail shrinks by 45%.
#     This makes the trail self-tighten as the option goes ITM.
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
    'id':           'tiered_stop',
    'name':         'Tiered profit stop',
    'description':  'Stop tightens at each profit milestone. '
                    'Optional delta adjustment within each tier.',
    'params': [
        {'key': 'tier1Pct',    'label': 'Tier 1 profit trigger (%)',
         'default': 25,  'min': 5,   'max': 200, 'step': 5},
        {'key': 'tier2Pct',    'label': 'Tier 2 profit trigger (%)',
         'default': 50,  'min': 5,   'max': 300, 'step': 5},
        {'key': 'tier3Pct',    'label': 'Tier 3 profit trigger (%)',
         'default': 100, 'min': 5,   'max': 500, 'step': 5},
        {'key': 'trailPct1',   'label': 'Tier 1 trail width (%)',
         'default': 30,  'min': 1,   'max': 80,  'step': 1},
        {'key': 'trailPct2',   'label': 'Tier 2 trail width (%)',
         'default': 20,  'min': 1,   'max': 60,  'step': 1},
        {'key': 'trailPct3',   'label': 'Tier 3 trail width (%)',
         'default': 10,  'min': 1,   'max': 40,  'step': 1},
        {'key': 'hardStopPct', 'label': 'Hard stop before tier 1 (%)',
         'default': 50,  'min': 5,   'max': 100, 'step': 5},
        {'key': 'deltaWeight', 'label': 'Delta adjustment weight (0=off)',
         'default': 0.0, 'min': 0.0, 'max': 1.0, 'step': 0.1,
         'hint': '0=pure % trail. 0.5=trail shrinks 50% of abs(delta). 1.0=full delta scaling.'},
        {'key': 'riskFreeRate','label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if not (params['tier1Pct'] < params['tier2Pct'] < params['tier3Pct']):
        return 'Tiers must be ordered: tier1 < tier2 < tier3'
    if not (params['trailPct3'] <= params['trailPct2'] <= params['trailPct1']):
        return 'Trail widths must tighten: trail3 <= trail2 <= trail1'
    return None


def execute(bars, entry_idx, entry_price, params):
    t1_trig    = entry_price * (1 + params['tier1Pct'] / 100)
    t2_trig    = entry_price * (1 + params['tier2Pct'] / 100)
    t3_trig    = entry_price * (1 + params['tier3Pct'] / 100)
    trail_pcts = [params['hardStopPct'] / 100,
                  params['trailPct1']   / 100,
                  params['trailPct2']   / 100,
                  params['trailPct3']   / 100]
    delta_w    = params['deltaWeight']
    hard_stop  = entry_price * (1 - params['hardStopPct'] / 100)
    r          = params['riskFreeRate'] / 100
    contract   = params.get('_contract', {})
    cache      = params.get('_cache', {})
    cfg        = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')

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

    tier            = 0
    high_water_mark = entry_price
    stop_price      = hard_stop
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        was_tier = tier

        # Advance tier (no stop check on transition bar)
        if tier < 3 and bar_high >= t3_trig:
            tier = 3
        elif tier < 2 and bar_high >= t2_trig:
            tier = 2
        elif tier < 1 and bar_high >= t1_trig:
            tier = 1

        # Compute trail width for current tier
        base_trail = trail_pcts[tier]

        # Delta adjustment
        if delta_w > 0:
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
            abs_delta    = abs(delta) if delta else 0.5
            adj_trail    = base_trail * (1 - delta_w * abs_delta)
            eff_trail    = max(0.01, min(adj_trail, base_trail))
        else:
            eff_trail = base_trail

        # Update stop from high water mark
        if tier > 0:
            new_stop = high_water_mark * (1 - eff_trail)
            if new_stop > stop_price:
                stop_price = new_stop

        tier_label = f'Tier {tier}' if tier > 0 else 'Hard stop'
        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        append_trace(extras, 'Hard stop',   bar, hard_stop)
        append_trace(extras, 'T1 trigger',  bar, t1_trig)
        append_trace(extras, 'T2 trigger',  bar, t2_trig)
        append_trace(extras, 'T3 trigger',  bar, t3_trig)

        # Skip stop check on tier transition bar
        if tier == was_tier or tier == 0:
            reason = 'trailing_stop' if tier > 0 else 'hard_stop'
            if bar_open <= stop_price:
                return {'exitBar': bar, 'exitReason': reason, 'stopPrice': bar_open,
                        'tier': tier, 'highWaterMark': high_water_mark,
                        'stopTrace': trace, 'extraTraces': extras}
            if bar_low <= stop_price:
                return {'exitBar': bar, 'exitReason': reason, 'stopPrice': stop_price,
                        'tier': tier, 'highWaterMark': high_water_mark,
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'tier': tier, 'highWaterMark': high_water_mark,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'tier': tier, 'highWaterMark': high_water_mark,
            'stopTrace': trace, 'extraTraces': extras}
