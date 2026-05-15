# ============================================================
# strategies/exit_multi_stage_lock.py
# Multi-stage ratcheting profit lock with ATR initial stop.
#
# HOW IT WORKS:
#   Stage 0 (before stage1Pct): ATR-based hard stop below entry
#   Stage 1 (at stage1Pct):     stop moves to break-even (entry)
#   Stage 2 (at stage2Pct):     stop moves to entry + stage2LockPct
#   Stage 3 (at stage3Pct):     trail kicks in at trailGapPct below high water mark
#
#   Each stage moves the stop up permanently — you can never give
#   back more than one stage worth of gains.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace
from data_provider  import fetch_daily_bars

META = {
    'enabled':     True,
    'id':          'multi_stage_lock',
    'name':        'Multi-stage profit lock',
    'description': '4-stage ratcheting stop. Stage 1: BE. Stage 2: +15% lock. '
                   'Stage 3: trailing. ATR-based initial stop.',
    'params': [
        {'key': 'atrDays',         'label': 'ATR lookback (days)',   'default': 14,  'min': 3, 'max': 30,  'step': 1},
        {'key': 'initialStopPct',  'label': 'Max initial stop (%)',  'default': 25,  'min': 5, 'max': 80,  'step': 5},
        {'key': 'stage1Pct',       'label': 'Stage 1 trigger (%)',   'default': 25,  'min': 5, 'max': 200, 'step': 5,
         'hint': 'Move stop to break-even at this profit %'},
        {'key': 'stage2Pct',       'label': 'Stage 2 trigger (%)',   'default': 50,  'min': 5, 'max': 300, 'step': 5,
         'hint': 'Move stop to lock-in level at this profit %'},
        {'key': 'stage3Pct',       'label': 'Stage 3 trigger (%)',   'default': 100, 'min': 5, 'max': 500, 'step': 5,
         'hint': 'Activate trailing stop at this profit %'},
        {'key': 'stage2LockPct',   'label': 'Stage 2 lock-in (%)',   'default': 15,  'min': 0, 'max': 100, 'step': 1,
         'hint': 'Stop moves to entry × (1 + this %) at stage 2'},
        {'key': 'trailGapPct',     'label': 'Stage 3 trail gap (%)', 'default': 15,  'min': 1, 'max': 50,  'step': 1},
        {'key': 'eodTime',         'label': 'EOD exit (CST)',        'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if not (params['stage1Pct'] < params['stage2Pct'] < params['stage3Pct']):
        return 'Stages must be ordered: stage1 < stage2 < stage3'
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
    stage1_target   = entry_price * (1 + params['stage1Pct'] / 100)
    stage2_target   = entry_price * (1 + params['stage2Pct'] / 100)
    stage3_target   = entry_price * (1 + params['stage3Pct'] / 100)
    stage2_lock     = entry_price * (1 + params['stage2LockPct'] / 100)
    trail_gap       = params['trailGapPct'] / 100
    eod_time        = params.get('eodTime', '15:45')

    contract = params.get('_contract', {})
    cfg      = params.get('_config', {})

    # ── ATR ──
    atr = None
    if contract and cfg:
        daily = fetch_daily_bars(contract.get('occ', ''), contract.get('entryDate', ''), atr_days, cfg)
        atr = _compute_atr(daily)
    if atr is None:
        atr = entry_price * initial_stop

    max_gap    = entry_price * initial_stop
    gap        = min(atr, max_gap)
    stop_price = entry_price - gap

    stage           = 0
    high_water_mark = entry_price
    trace           = []
    extras          = {}

    # Pre-computed level constants for visualization
    hard_stop_initial = stop_price       # ATR-based starting stop
    level_be          = entry_price      # stage 1 target
    level_lock        = stage2_lock      # stage 2 target

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        was_stage = stage

        # ── Stage transitions (no stop check on transition bar) ──
        if stage < 1 and bar_high >= stage1_target:
            stage      = 1
            stop_price = max(stop_price, entry_price)
        if stage < 2 and bar_high >= stage2_target:
            stage      = 2
            stop_price = max(stop_price, stage2_lock)
        if stage < 3 and bar_high >= stage3_target:
            stage      = 3
            new_trail  = high_water_mark * (1 - trail_gap)
            stop_price = max(stop_price, new_trail)

        # ── Trail in stage 3 (only if was already in stage 3) ──
        if was_stage == 3 and stage == 3:
            new_trail = high_water_mark * (1 - trail_gap)
            if new_trail > stop_price:
                stop_price = new_trail

        trace.append({'time': bar['time'], 'stopPrice': stop_price})
        # Extras: show the staircase of stage targets + initial hard stop
        append_trace(extras, 'Initial ATR stop', bar, hard_stop_initial)
        append_trace(extras, 'Stage 1 (BE)',     bar, level_be)
        append_trace(extras, 'Stage 2 (lock)',   bar, level_lock)
        append_trace(extras, 'Stage 3 target',   bar, stage3_target)

        # ── Stop check (skip if stage just advanced this bar) ──
        if stage == was_stage:
            reason = 'hard_stop' if stage == 0 else 'trailing_stop'

            if bar_open <= stop_price:
                return {'exitBar': bar, 'exitReason': reason, 'stopPrice': bar_open,
                        'highWaterMark': high_water_mark, 'stage': stage,
                        'atr': round(atr, 4), 'stopTrace': trace, 'extraTraces': extras}

            if bar_low <= stop_price:
                return {'exitBar': bar, 'exitReason': reason, 'stopPrice': stop_price,
                        'highWaterMark': high_water_mark, 'stage': stage,
                        'atr': round(atr, 4), 'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price,
                    'highWaterMark': high_water_mark, 'stage': stage,
                    'atr': round(atr, 4), 'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price,
            'highWaterMark': high_water_mark, 'stage': stage,
            'atr': round(atr, 4), 'stopTrace': trace, 'extraTraces': extras}
