# ============================================================
# strategies/exit_calendar_event.py
# Family 4 — Time-Based Stops
# Variant: Calendar-based exit (exit before earnings / FOMC / CPI)
#
# HOW IT WORKS:
#   You paste in a list of event dates (one per line: YYYY-MM-DD).
#   The strategy exits the position before the market opens on
#   an event date — specifically at the EOD session BEFORE the event.
#
#   This protects against binary risk events where holding through
#   would expose you to gap risk that no stop can protect against.
#   Standard usage: paste upcoming earnings dates, FOMC dates, CPI
#   release dates.
#
#   Two modes:
#     'eod_before' — exit at EOD on the last session before the event.
#                    Default. E.g. event is 2026-01-15, exit 2026-01-14.
#     'open_of'    — exit at the OPEN on the event day itself.
#                    Catches gap-open if you slept through the prior EOD.
#
#   eventDates is a newline-separated string of YYYY-MM-DD dates.
#   If the position's bar date is the day before a listed event
#   (eod_before) or the event day itself (open_of), the exit fires.
#
#   A hard stop below entry stays active always.
# ============================================================

from datetime import datetime, timedelta
from backtest_engine import to_minutes, should_eod_exit

META = {
    'enabled':     True,
    'id':          'calendar_event_exit',
    'name':        'Calendar event exit',
    'description': 'Exit before a list of user-defined event dates '
                   '(earnings, FOMC, CPI). Hard stop active throughout.',
    'params': [
        {
            'key':     'eventDates',
            'label':   'Event dates (YYYY-MM-DD, one per line)',
            'default': '',
            'hint':    'Paste dates like earnings or FOMC. Exit fires at EOD the day before each event.',
        },
        {
            'key':     'exitMode',
            'label':   'Exit mode',
            'default': 'eod_before',
            'hint':    'eod_before = exit EOD before the event. open_of = exit at open on event day.',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (%)',
            'default': 50,
            'min':     5,
            'max':     100,
            'step':    5,
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
    raw = params.get('eventDates', '').strip()
    if raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                datetime.strptime(line, '%Y-%m-%d')
            except ValueError:
                return f"Invalid event date format: '{line}' — use YYYY-MM-DD"
    return None


def _parse_event_dates(raw):
    """Parse newline-separated YYYY-MM-DD strings into a set of date objects."""
    dates = set()
    for line in (raw or '').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            dates.add(datetime.strptime(line, '%Y-%m-%d').date())
        except ValueError:
            pass
    return dates


def execute(bars, entry_idx, entry_price, params):
    hard_stop_pct = params['hardStopPct'] / 100
    hard_stop     = entry_price * (1 - hard_stop_pct)
    exit_mode     = params.get('exitMode', 'eod_before')
    event_dates   = _parse_event_dates(params.get('eventDates', ''))

    trace = []

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])
        bar_date_str = bar['time'][:10]

        try:
            bar_date = datetime.strptime(bar_date_str, '%Y-%m-%d').date()
        except ValueError:
            bar_date = None

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})

        # Hard stop always active
        if bar_open <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  bar_open,
                'stopTrace':  trace,
            }
        if bar_low <= hard_stop:
            return {
                'exitBar':    bar,
                'exitReason': 'hard_stop',
                'stopPrice':  hard_stop,
                'stopTrace':  trace,
            }

        # Calendar event exit
        if bar_date and event_dates:
            tomorrow = bar_date + timedelta(days=1)

            if exit_mode == 'eod_before':
                # Exit at EOD today if tomorrow is an event day
                if tomorrow in event_dates and should_eod_exit(bar, params):
                    return {
                        'exitBar':    bar,
                        'exitReason': 'eod',
                        'stopPrice':  float(bar['close']),
                        'exitType':   'calendar_event',
                        'eventDate':  str(tomorrow),
                        'stopTrace':  trace,
                    }
            elif exit_mode == 'open_of':
                # Exit at open if today IS an event day
                if bar_date in event_dates:
                    return {
                        'exitBar':    bar,
                        'exitReason': 'eod',
                        'stopPrice':  bar_open,
                        'exitType':   'calendar_event_open',
                        'eventDate':  str(bar_date),
                        'stopTrace':  trace,
                    }

        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  float(bar['close']),
                'stopTrace':  trace,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  hard_stop,
        'stopTrace':  trace,
    }
