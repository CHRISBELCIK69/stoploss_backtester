# ============================================================
# strategies/exit_partial_exit_scale.py
# Three-tranche partial exit strategy.
#
# HOW IT WORKS:
#   Splits the position into 3 equal tranches with different exit rules:
#     T1 (1/3): exits at ATR-based hard stop (cut loss fast)
#     T2 (1/3): exits at break-even stop (free trade)
#     T3 (1/3): runs with ATR trail + profit floor (let it run)
#
#   The reported entry/exit prices represent the volume-weighted
#   average across all 3 tranches. The exit bar is the LAST tranche
#   to exit. Stop trace shows tranche 3's trail (the live runner).
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace
from data_provider  import fetch_daily_bars

META = {
    'enabled':     True,
    'id':          'partial_exit_scale',
    'name':        'Partial exit (3 tranches)',
    'description': '3 tranches: T1 hard stop, T2 break-even, T3 ATR trail with floor. '
                   'Reports weighted average exit across all tranches.',
    'params': [
        {'key': 'atrDays',         'label': 'ATR lookback (days)',  'default': 14,  'min': 3, 'max': 30,  'step': 1},
        {'key': 'initialStopPct',  'label': 'Max initial stop (%)', 'default': 25,  'min': 5, 'max': 80,  'step': 5},
        {'key': 'trailGapPct',     'label': 'T3 trail gap (%)',     'default': 15,  'min': 1, 'max': 50,  'step': 1},
        {'key': 'floorPct',        'label': 'T3 floor (%)',         'default': 10,  'min': 0, 'max': 100, 'step': 1,
         'hint': 'Tranche 3 stop never drops below entry × (1 + this %)'},
        {'key': 'eodTime',         'label': 'EOD exit (CST)',       'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['initialStopPct'] <= 0:
        return 'Initial stop % must be greater than 0'
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
    atr_days     = int(params['atrDays'])
    initial_stop = params['initialStopPct'] / 100
    trail_gap    = params['trailGapPct'] / 100
    floor_pct    = params['floorPct'] / 100
    eod_time     = params.get('eodTime', '15:45')

    contract = params.get('_contract', {})
    cfg      = params.get('_config', {})

    # ── ATR ──
    atr = None
    if contract and cfg:
        daily = fetch_daily_bars(contract.get('occ', ''), contract.get('entryDate', ''), atr_days, cfg)
        atr = _compute_atr(daily)
    if atr is None:
        atr = entry_price * initial_stop

    max_gap   = entry_price * initial_stop
    gap       = min(atr, max_gap)
    hard_stop = entry_price - gap
    floor_stop = entry_price * (1 + floor_pct)

    # Tranche state: each is dict { exit_price, exit_bar }
    t1 = {'exit_price': None, 'exit_bar': None}
    t2 = {'exit_price': None, 'exit_bar': None}
    t3 = {'exit_price': None, 'exit_bar': None}

    high_water_mark = entry_price
    trail_stop      = max(hard_stop, floor_stop)
    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        # ── Update T3 trail ──
        if t3['exit_price'] is None:
            raw_trail  = high_water_mark * (1 - trail_gap)
            trail_stop = max(raw_trail, floor_stop)

        trace.append({'time': bar['time'], 'stopPrice': trail_stop})
        # Extras: show all three tranche exit levels
        append_trace(extras, 'T1 hard stop',   bar, hard_stop)
        append_trace(extras, 'T2 break-even',  bar, entry_price)
        append_trace(extras, 'T3 floor',       bar, floor_stop)

        # ── T1: hard stop ──
        if t1['exit_price'] is None:
            if bar_open <= hard_stop:
                t1['exit_price'] = bar_open;  t1['exit_bar'] = bar
            elif bar_low <= hard_stop:
                t1['exit_price'] = hard_stop; t1['exit_bar'] = bar

        # ── T2: break-even stop ──
        if t2['exit_price'] is None:
            if bar_open <= entry_price:
                t2['exit_price'] = bar_open;     t2['exit_bar'] = bar
            elif bar_low <= entry_price:
                t2['exit_price'] = entry_price;  t2['exit_bar'] = bar

        # ── T3: trail (only check stop if trail isn't being raised this bar) ──
        if t3['exit_price'] is None:
            if bar_open <= trail_stop:
                t3['exit_price'] = bar_open;    t3['exit_bar'] = bar
            elif bar_low <= trail_stop:
                t3['exit_price'] = trail_stop;  t3['exit_bar'] = bar

        # ── EOD: close any open tranches ──
        if should_eod_exit(bar, params):
            for tr in (t1, t2, t3):
                if tr['exit_price'] is None:
                    tr['exit_price'] = float(bar['close'])
                    tr['exit_bar']   = bar
            break

        if t1['exit_price'] is not None and t2['exit_price'] is not None and t3['exit_price'] is not None:
            break

    # If we ran out of bars, close anything still open at last bar
    last_bar = bars[-1]
    for tr in (t1, t2, t3):
        if tr['exit_price'] is None:
            tr['exit_price'] = float(last_bar['close'])
            tr['exit_bar']   = last_bar

    # ── Weighted average exit (1/3 each) ──
    avg_exit = (t1['exit_price'] + t2['exit_price'] + t3['exit_price']) / 3

    # Last tranche to exit becomes the reported exit bar
    exit_bars = [t1['exit_bar'], t2['exit_bar'], t3['exit_bar']]
    last_exit_bar = max(exit_bars, key=lambda b: bars.index(b))

    # Determine reason from t3 (the runner) - if it gave back to floor it's a stop
    if t3['exit_price'] <= floor_stop * 1.01:
        reason = 'trailing_stop'
    elif last_exit_bar is t3['exit_bar'] and bars.index(last_exit_bar) >= len(bars) - 5:
        reason = 'eod'
    else:
        reason = 'trailing_stop'

    return {
        'exitBar':       last_exit_bar,
        'exitReason':    reason,
        'stopPrice':     avg_exit,   # so engine reports avg_exit as fill
        'highWaterMark': high_water_mark,
        't1Exit':        round(t1['exit_price'], 4),
        't2Exit':        round(t2['exit_price'], 4),
        't3Exit':        round(t3['exit_price'], 4),
        'atr':           round(atr, 4),
        'stopTrace':     trace,
        'extraTraces':   extras,
    }
