# ============================================================
# strategies/exit_atr_stop.py
# ATR-based two-phase stop strategy.
#
# HOW IT WORKS:
#   Uses Average True Range (ATR) from daily bars to set the
#   initial stop distance — adapts to actual option volatility
#   instead of using an arbitrary fixed percentage.
#
#   PHASE 1 — Initial stop (before profit target):
#     Fetches N daily bars to compute ATR.
#     Stop gap = min(ATR, entry × initialStopPct).
#     Stop = entry - gap.
#     If ATR is smaller than the % cap, the stop is tighter
#     (low-vol option). If ATR exceeds the cap, the cap holds
#     (prevents blown-out stops on wild options).
#
#   PHASE 2 — Trailing floor stop (after profit target hit):
#     Once price reaches entry × (1 + profitTargetPct):
#       Trail stop = price × (1 - trailGapPct)
#       Floor stop = entry × (1 + floorPct)
#       Actual stop = max(trail, floor)
#     The floor guarantees a minimum profit — the trail can
#     only tighten above it as price climbs higher.
#
#   On the chart: flat ATR-based stop in Phase 1, then a
#   rising trail with a visible floor line in Phase 2.
# ============================================================

from backtest_engine import to_minutes
from data_provider import fetch_daily_bars

META = {
    'enabled':     True,
    'id':          'atr_stop',
    'name':        'ATR adaptive stop',
    'description': 'Phase 1: ATR-based initial stop (adapts to volatility). '
                   'Phase 2: trailing stop with profit floor after target hit.',
    'params': [
        {'key': 'atrDays',         'label': 'ATR lookback (days)', 'default': 14,   'min': 3,    'max': 30,  'step': 1,
         'hint': 'Number of daily bars to compute ATR from'},
        {'key': 'initialStopPct',  'label': 'Max initial stop (%)', 'default': 25,  'min': 5,    'max': 80,  'step': 5,
         'hint': 'Cap on initial stop — gap = min(ATR, entry × this %)'},
        {'key': 'profitTargetPct', 'label': 'Profit target (%)',    'default': 25,  'min': 5,    'max': 200, 'step': 5,
         'hint': 'Phase 2 activates when price reaches this % above entry'},
        {'key': 'trailGapPct',     'label': 'Trail gap (%)',        'default': 15,  'min': 1,    'max': 50,  'step': 1,
         'hint': 'Phase 2 trail distance — stop sits this % below current price'},
        {'key': 'floorPct',        'label': 'Floor profit (%)',     'default': 10,  'min': 0,    'max': 100, 'step': 5,
         'hint': 'Guaranteed profit floor — stop never drops below entry × (1 + this %)'},
        {'key': 'eodTime',         'label': 'EOD exit (CST)',       'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['initialStopPct'] <= 0:
        return 'Initial stop % must be greater than 0'
    if params['profitTargetPct'] <= 0:
        return 'Profit target % must be greater than 0'
    if params['trailGapPct'] <= 0:
        return 'Trail gap % must be greater than 0'
    return None


def _compute_atr(daily_bars):
    """Compute ATR from daily OHLC bars."""
    if not daily_bars:
        return None

    true_ranges = []
    for i, bar in enumerate(daily_bars):
        h, l, c = bar['high'], bar['low'], bar['close']
        if i == 0:
            tr = h - l
        else:
            prev_c = daily_bars[i - 1]['close']
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    return sum(true_ranges) / len(true_ranges) if true_ranges else None


def execute(bars, entry_idx, entry_price, params):
    initial_stop_pct  = params['initialStopPct'] / 100
    profit_target_pct = params['profitTargetPct'] / 100
    trail_gap_pct     = params['trailGapPct'] / 100
    floor_pct         = params['floorPct'] / 100
    atr_days          = int(params['atrDays'])
    eod_time          = params.get('eodTime', '15:45')

    contract = params.get('_contract', {})
    cfg      = params.get('_config', {})

    # ── Compute ATR from daily bars ──
    atr = None
    if contract and cfg:
        occ        = contract.get('occ', '')
        entry_date = contract.get('entryDate', '')
        if occ and entry_date:
            daily_bars = fetch_daily_bars(occ, entry_date, atr_days, cfg)
            atr = _compute_atr(daily_bars)

    # Fallback: if no daily data, estimate ATR from first 30 min of 1-min bars
    if atr is None:
        window = bars[max(0, entry_idx - 30):entry_idx]
        if len(window) >= 5:
            trs = [float(b['high']) - float(b['low']) for b in window]
            atr = sum(trs) / len(trs) * 6.5  # scale 1-min TR to daily estimate
        else:
            atr = entry_price * initial_stop_pct  # last resort: use the cap

    # ── Phase 1 setup ──
    max_gap       = entry_price * initial_stop_pct
    gap           = min(atr, max_gap)
    stop_price    = entry_price - gap
    profit_target = entry_price * (1 + profit_target_pct)
    floor_stop    = entry_price * (1 + floor_pct)

    phase2_active   = False
    high_water_mark = entry_price

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        # Track whether phase2 was already active before this bar
        was_phase2 = phase2_active

        # ── Activate Phase 2 ──
        if not phase2_active and bar_high >= profit_target:
            phase2_active = True
            # Set initial phase 2 trail from the high
            trail_stop = high_water_mark * (1 - trail_gap_pct)
            stop_price = max(trail_stop, floor_stop)
            # Don't check stop on the activation bar

        # ── Phase 2 trailing ──
        if was_phase2 and phase2_active:
            trail_stop = high_water_mark * (1 - trail_gap_pct)
            new_stop   = max(trail_stop, floor_stop)
            if new_stop > stop_price:
                stop_price = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Only check stop if phase2 was already active, or still in phase 1
        if not phase2_active or was_phase2:
            reason = 'trailing_stop' if phase2_active else 'hard_stop'

            # Gap-through: open already below stop → fill at open
            if bar_open <= stop_price:
                return {
                    'exitBar': bar, 'exitReason': reason,
                    'stopPrice': bar_open, 'highWaterMark': high_water_mark,
                    'phase2Active': phase2_active, 'atr': round(atr, 4),
                    'gapUsed': round(gap, 4), 'stopTrace': trace,
                }

            if bar_low <= stop_price:
                return {
                    'exitBar': bar, 'exitReason': reason,
                    'stopPrice': stop_price, 'highWaterMark': high_water_mark,
                    'phase2Active': phase2_active, 'atr': round(atr, 4),
                    'gapUsed': round(gap, 4), 'stopTrace': trace,
                }

        if bar_mins >= to_minutes(eod_time):
            return {
                'exitBar': bar, 'exitReason': 'eod',
                'stopPrice': stop_price, 'highWaterMark': high_water_mark,
                'phase2Active': phase2_active, 'atr': round(atr, 4),
                'gapUsed': round(gap, 4), 'stopTrace': trace,
            }

    return {
        'exitBar': bars[-1], 'exitReason': 'expiry',
        'stopPrice': stop_price, 'highWaterMark': high_water_mark,
        'phase2Active': phase2_active, 'atr': round(atr, 4),
        'gapUsed': round(gap, 4), 'stopTrace': trace,
    }
