# ============================================================
# strategies/exit_session_exit.py
# Family 4 — Time-Based Stops
# Variant: Session-based exit (exit if breached at open or close)
#
# HOW IT WORKS:
#   Monitors two specific session windows per day:
#     OPEN WINDOW  — first N minutes after market open (default 09:30–09:45)
#     CLOSE WINDOW — last N minutes before EOD exit (default 15:30–15:45)
#
#   MODE 'breach_open':
#     If the stop price is breached in the open window, exit immediately.
#     This catches gap-opens and early panic moves. Outside the open
#     window, the stop is checked normally.
#
#   MODE 'breach_close':
#     If the stop price is breached in the close window, exit immediately.
#     This catches late-day deterioration and ensures you don't hold
#     a damaged position into overnight / expiry.
#
#   MODE 'both':
#     Either window can trigger the exit.
#
#   Outside both windows, a standard hard stop is still active —
#   you're never unprotected.
#
#   This is useful for intraday options where open/close dynamics
#   dominate (0DTE SPY) and midday chop is irrelevant noise.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'session_exit',
    'name':        'Session window exit',
    'description': 'Tighten stop enforcement at open and/or close windows. '
                   'Immediately exits if stop breached in defined session periods.',
    'params': [
        {
            'key':     'sessionMode',
            'label':   'Session trigger',
            'default': 'both',
            'hint':    'breach_open, breach_close, or both',
        },
        {
            'key':     'openWindowEnd',
            'label':   'Open window end (CST)',
            'default': '09:45',
            'type':    'time',
            'hint':    'Open window = market open to this time',
        },
        {
            'key':     'closeWindowStart',
            'label':   'Close window start (CST)',
            'default': '15:30',
            'type':    'time',
            'hint':    'Close window = this time to EOD',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (%)',
            'default': 50,
            'min':     5,
            'max':     100,
            'step':    5,
            'hint':    'Stop level active in ALL windows including outside session windows',
        },
        {
            'key':     'sessionStopPct',
            'label':   'Session stop (% — tighter during windows)',
            'default': 25,
            'min':     1,
            'max':     100,
            'step':    5,
            'hint':    'Tighter stop applied ONLY during the open/close windows',
        },
        {
            'key':     'eodTime',
            'label':   'EOD exit (CST)',
            'default': '15:45',
            'type':    'time',
        },
    ],
}


def validate(params):
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be greater than 0'
    if params['sessionStopPct'] <= 0:
        return 'Session stop % must be greater than 0'
    if params['sessionStopPct'] > params['hardStopPct']:
        return 'Session stop % should be <= hard stop % (session stop is the tighter one)'
    return None


def execute(bars, entry_idx, entry_price, params):
    hard_stop_pct    = params['hardStopPct'] / 100
    session_stop_pct = params['sessionStopPct'] / 100
    session_mode     = params.get('sessionMode', 'both')
    open_window_end  = to_minutes(params.get('openWindowEnd',  '09:45'))
    close_win_start  = to_minutes(params.get('closeWindowStart', '15:30'))
    market_open      = to_minutes('09:30')

    hard_stop    = entry_price * (1 - hard_stop_pct)
    session_stop = entry_price * (1 - session_stop_pct)

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        in_open_window  = market_open <= bar_mins <= open_window_end
        in_close_window = bar_mins >= close_win_start

        # Determine effective stop this bar
        in_session_window = (
            (session_mode in ('breach_open', 'both')  and in_open_window) or
            (session_mode in ('breach_close', 'both') and in_close_window)
        )
        active_stop = session_stop if in_session_window else hard_stop

        trace.append({'time': bar['time'], 'stopPrice': active_stop})

        if bar_open <= active_stop:
            reason = 'hard_stop'
            return {
                'exitBar':        bar,
                'exitReason':     reason,
                'stopPrice':      bar_open,
                'inSessionWindow': in_session_window,
                'stopTrace':      trace,
            }
        if bar_low <= active_stop:
            return {
                'exitBar':        bar,
                'exitReason':     'hard_stop',
                'stopPrice':      active_stop,
                'inSessionWindow': in_session_window,
                'stopTrace':      trace,
            }

        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  active_stop,
                'stopTrace':  trace,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  hard_stop,
        'stopTrace':  trace,
    }
