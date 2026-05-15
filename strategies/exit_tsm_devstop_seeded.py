# ============================================================
# strategies/exit_tsm_devstop_seeded.py
# DevStop (Kase) with rolling prev-day / today blend.
#
# HOW IT WORKS:
#   The DTR window is always exactly `period` bars (default 40).
#   It starts filled entirely with previous day bars.
#   As each new today bar arrives, it pushes one prev day bar out.
#
#     9:30 entry  → [40 prev day bars]
#     9:31        → [39 prev day | 1 today]
#     9:32        → [38 prev day | 2 today]
#     ...
#     bar 40      → [0 prev day  | 40 today]  ← fully today
#
#   Stop is valid from bar 1 instead of waiting for warmup.
#   DTR and stop recalculate on every bar as the window rolls.
#
# MATH:
#   DTR     = max(high - low[-2], |high - close[-2]|, |low - close[-2]|)
#   avg_DTR = mean(DTR window)
#   sd      = stddev(DTR window)
#   stop    = entry - min(avg_DTR + 1.0 × sd, entry × max_stop_pct)
# ============================================================

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

from backtest_engine import to_minutes, should_eod_exit


META = {
    'enabled':     True,
    'id':          'tsm_devstop_seeded',
    'name':        'TSM DevStop seeded (prev/today blend)',
    'description': 'Stop ready at bar 1 — window seeded from prev-day 1-min bars, '
                   'rolls into today data bar by bar. DevStop1 with % cap.',
    'params': [
        {'key': 'period',      'label': 'Window size (bars)',    'default': 40,   'min': 10,   'max': 80,  'step': 5,
         'hint': 'Total bars in the rolling DTR window. Previous day fills it at open.'},
        {'key': 'maxStopPct',  'label': 'Max stop distance (%)', 'default': 30,   'min': 5,    'max': 100, 'step': 5,
         'hint': 'Caps stop distance as % of entry. Protects cheap contracts.'},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',        'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['maxStopPct'] <= 0:
        return 'Max stop % must be greater than 0'
    if params['period'] < 3:
        return 'Window must be at least 3 bars'
    return None


# ─────────────────────────────────────────────
# DTR helpers
# ─────────────────────────────────────────────

def _calc_dtr(bars, i):
    """2-bar true range at index i. Requires i >= 2."""
    if i < 2:
        return None
    high    = float(bars[i]['high'])
    low     = float(bars[i]['low'])
    close_2 = float(bars[i - 2]['close'])
    return max(
        high - low,
        abs(high - close_2),
        abs(low  - close_2),
    )


def _calc_stop(dtr_window, entry, max_stop_pct):
    """Returns (stop_price, avg_dtr, sd) or (None, None, None) if not enough data."""
    n = len(dtr_window)
    if n < 3:
        return None, None, None
    avg_dtr = sum(dtr_window) / n
    var     = sum((x - avg_dtr) ** 2 for x in dtr_window) / (n - 1)
    sd      = var ** 0.5
    gap     = min(avg_dtr + 1.0 * sd, entry * max_stop_pct)
    return entry - gap, avg_dtr, sd


# ─────────────────────────────────────────────
# Previous-day bar fetch
# ─────────────────────────────────────────────

def _fetch_prev_day_bars(occ, n_bars, entry_date, api_key):
    """
    Fetch the last `n_bars` 1-min bars from the most recent
    completed trading day BEFORE entry_date.
    Skips weekends/holidays automatically (tries back up to 5 days).
    """
    eastern = ZoneInfo('America/New_York')
    try:
        entry_dt = datetime.strptime(entry_date, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        entry_dt = datetime.now(tz=eastern).date()

    for days_back in range(1, 6):
        date_str = (entry_dt - timedelta(days=days_back)).strftime('%Y-%m-%d')
        try:
            resp = requests.get(
                f'https://api.polygon.io/v2/aggs/ticker/O:{occ}/range/1/minute/{date_str}/{date_str}',
                params={'adjusted': 'false', 'sort': 'desc', 'limit': n_bars, 'apiKey': api_key},
                timeout=15,
            )
        except Exception:
            continue
        if not resp.ok:
            continue
        results = resp.json().get('results')
        if not results:
            continue

        results = list(reversed(results))
        return [
            {
                'time':  datetime.fromtimestamp(r['t'] / 1000, tz=timezone.utc)
                                  .astimezone(eastern)
                                  .strftime('%Y-%m-%d %H:%M'),
                'open':  r['o'], 'high': r['h'],
                'low':   r['l'], 'close': r['c'],
            }
            for r in results
        ], date_str

    return [], None


# ─────────────────────────────────────────────
# Execute
# ─────────────────────────────────────────────

def execute(bars, entry_idx, entry_price, params):
    period         = int(params['period'])
    max_stop_pct   = params['maxStopPct'] / 100   # UI passes 30 → 0.30
    eod_time       = params.get('eodTime', '15:45')
    eod_mins       = to_minutes(eod_time)

    contract = params.get('_contract', {})
    cfg      = params.get('_config', {})
    occ      = contract.get('occ', '')
    entry_date = contract.get('entryDate', '')
    api_key  = cfg.get('polygon', {}).get('apiKey', '').strip()

    # ── Seed DTR window from previous day ──
    prev_dtr = []
    prev_date = None
    if occ and api_key and api_key != 'YOUR_POLYGON_API_KEY_HERE':
        prev_bars, prev_date = _fetch_prev_day_bars(occ, period + 2, entry_date, api_key)
        for i in range(2, len(prev_bars)):
            d = _calc_dtr(prev_bars, i)
            if d is not None:
                prev_dtr.append(d)

    dtr_window = list(prev_dtr[-period:])

    # Compute initial stop from prev-day seed
    stop, avg_dtr, sd = _calc_stop(dtr_window, entry_price, max_stop_pct)

    # Fallback if no prev-day data
    if stop is None:
        stop = entry_price * (1 - max_stop_pct)

    high_water_mark = entry_price
    today_bars      = list(bars[:entry_idx + 1])
    trace           = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_high = float(bar['high'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_high > high_water_mark:
            high_water_mark = bar_high

        today_bars.append(bar)

        # Push new DTR into window, pop oldest
        if len(today_bars) >= 3:
            new_dtr = _calc_dtr(today_bars, len(today_bars) - 1)
            if new_dtr is not None:
                dtr_window.append(new_dtr)
                if len(dtr_window) > period:
                    dtr_window.pop(0)

                new_stop, avg_dtr, sd = _calc_stop(dtr_window, entry_price, max_stop_pct)
                if new_stop is not None:
                    stop = new_stop

        trace.append({'time': bar['time'], 'stopPrice': stop})

        today_count_in_window = min(len(today_bars) - 1 - entry_idx, period)
        extras = {
            'avgDtr':            round(avg_dtr, 4) if avg_dtr is not None else None,
            'dtrStddev':         round(sd, 4) if sd is not None else None,
            'prevDaySeedDate':   prev_date,
            'todayBarsInWindow': today_count_in_window,
        }

        # Gap-through
        if bar_open <= stop:
            return {
                'exitBar':       bar,
                'exitReason':    'hard_stop',
                'stopPrice':     bar_open,
                'highWaterMark': high_water_mark,
                'stopTrace':     trace,
                **extras,
            }

        # Intrabar low
        if bar_low <= stop:
            return {
                'exitBar':       bar,
                'exitReason':    'hard_stop',
                'stopPrice':     stop,
                'highWaterMark': high_water_mark,
                'stopTrace':     trace,
                **extras,
            }

        # EOD
        if should_eod_exit(bar, params):
            return {
                'exitBar':       bar,
                'exitReason':    'eod',
                'stopPrice':     stop,
                'highWaterMark': high_water_mark,
                'stopTrace':     trace,
                **extras,
            }

    # Held to last bar
    return {
        'exitBar':       bars[-1],
        'exitReason':    'expiry',
        'stopPrice':     stop,
        'highWaterMark': high_water_mark,
        'stopTrace':     trace,
        'avgDtr':        round(avg_dtr, 4) if avg_dtr is not None else None,
        'dtrStddev':     round(sd, 4) if sd is not None else None,
        'prevDaySeedDate': prev_date,
        'todayBarsInWindow': min(len(today_bars) - 1 - entry_idx, period),
    }
