# ============================================================
# strategies/exit_vwap_stop.py
# VWAP-based exit strategy.
#
# HOW IT WORKS:
#   Phase 1: ATR-based hard stop below entry (same as ATR strategy)
#   Phase 2: Once price reaches profit target, switch to VWAP exit:
#            Exit if bar closes below its VWAP for N consecutive bars.
#            Floor stop also active in phase 2 — guaranteed min profit.
#
#   VWAP is the natural institutional intraday level. When an option
#   in profit breaks back below VWAP and stays there, momentum has
#   flipped. Polygon includes VWAP in every minute bar.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit
from data_provider  import fetch_daily_bars

META = {
    'enabled':     True,
    'id':          'vwap_stop',
    'name':        'VWAP momentum exit',
    'description': 'Phase 1: ATR hard stop. Phase 2 (after profit target): exit on N '
                   'consecutive closes below VWAP. Floor stop active in phase 2.',
    'params': [
        {'key': 'atrDays',         'label': 'ATR lookback (days)',  'default': 14, 'min': 3, 'max': 30,  'step': 1},
        {'key': 'initialStopPct',  'label': 'Max initial stop (%)', 'default': 25, 'min': 5, 'max': 80,  'step': 5},
        {'key': 'profitTargetPct', 'label': 'Profit target (%)',    'default': 25, 'min': 5, 'max': 200, 'step': 5,
         'hint': 'VWAP mode activates at this profit %'},
        {'key': 'floorPct',        'label': 'Floor (%)',            'default': 10, 'min': 0, 'max': 100, 'step': 1,
         'hint': 'Phase 2 stop never drops below entry × (1 + this %)'},
        {'key': 'vwapBarsBelow',   'label': 'Bars below VWAP',      'default': 2,  'min': 1, 'max': 10,  'step': 1,
         'hint': '1 = aggressive (single close exits), 3 = patient'},
        {'key': 'eodTime',         'label': 'EOD exit (CST)',       'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['initialStopPct'] <= 0:
        return 'Initial stop % must be greater than 0'
    if params['vwapBarsBelow'] < 1:
        return 'Bars below VWAP must be at least 1'
    return None


def _compute_atr(daily_bars):
    if not daily_bars:
        return None
    trs = []
    for i, bar in enumerate(daily_bars):
        h, l, c = bar['high'], bar['low'], bar['close']
        if i == 0:
            tr = h - l
        else:
            prev_c = daily_bars[i - 1]['close']
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def execute(bars, entry_idx, entry_price, params):
    atr_days        = int(params['atrDays'])
    initial_stop    = params['initialStopPct'] / 100
    profit_target_p = params['profitTargetPct'] / 100
    floor_pct       = params['floorPct'] / 100
    vwap_bars_below = int(params['vwapBarsBelow'])
    eod_time        = params.get('eodTime', '15:45')

    contract = params.get('_contract', {})
    cfg      = params.get('_config', {})
    cache    = params.get('_cache', {})

    # ── ATR (use shared cache when available) ──
    atr = None
    cached_daily = cache.get('dailyBars')
    if cached_daily:
        daily = cached_daily[-atr_days:] if len(cached_daily) > atr_days else cached_daily
        atr = _compute_atr(daily)
    elif contract and cfg:
        daily = fetch_daily_bars(contract.get('occ', ''), contract.get('entryDate', ''), atr_days, cfg)
        atr = _compute_atr(daily)
    if atr is None:
        atr = entry_price * initial_stop

    max_gap       = entry_price * initial_stop
    gap           = min(atr, max_gap)
    hard_stop     = entry_price - gap
    profit_target = entry_price * (1 + profit_target_p)
    floor_stop    = entry_price * (1 + floor_pct)

    phase2_active   = False
    bars_below      = 0
    stop_price      = hard_stop
    high_water_mark = entry_price
    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_close = float(bar['close'])
        bar_vwap  = float(bar.get('vwap', bar_close))
        bar_mins  = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        was_phase2 = phase2_active

        # ── Activate phase 2 ──
        if not phase2_active and bar_high >= profit_target:
            phase2_active = True
            stop_price    = floor_stop
            bars_below    = 0

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # ── Phase 2: VWAP momentum exit ──
        if was_phase2 and phase2_active:
            if bar_close < bar_vwap:
                bars_below += 1
            else:
                bars_below = 0

            if bars_below >= vwap_bars_below:
                fill = max(bar_close, floor_stop)
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': fill,
                        'highWaterMark': high_water_mark, 'phase2Active': True,
                        'atr': round(atr, 4), 'vwapBarsBelow': bars_below, 'stopTrace': trace}

            if bar_open <= floor_stop:
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_open,
                        'highWaterMark': high_water_mark, 'phase2Active': True,
                        'atr': round(atr, 4), 'stopTrace': trace}

            if bar_low <= floor_stop:
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': floor_stop,
                        'highWaterMark': high_water_mark, 'phase2Active': True,
                        'atr': round(atr, 4), 'stopTrace': trace}

        # ── Phase 1: hard ATR stop ──
        if not phase2_active:
            if bar_open <= hard_stop:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                        'highWaterMark': high_water_mark, 'phase2Active': False,
                        'atr': round(atr, 4), 'stopTrace': trace}

            if bar_low <= hard_stop:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                        'highWaterMark': high_water_mark, 'phase2Active': False,
                        'atr': round(atr, 4), 'stopTrace': trace}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'highWaterMark': high_water_mark, 'phase2Active': phase2_active,
                    'atr': round(atr, 4), 'stopTrace': trace}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'highWaterMark': high_water_mark, 'phase2Active': phase2_active,
            'atr': round(atr, 4), 'stopTrace': trace}
