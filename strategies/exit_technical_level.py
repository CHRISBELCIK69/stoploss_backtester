# ============================================================
# strategies/exit_technical_level.py
# Family 2 — Underlying Price Stops
# Variant: Move beyond key technical levels
#
# HOW IT WORKS:
#   You define one or more price levels (support / resistance /
#   pivot points) as a comma-separated list. If the option
#   premium breaches the stop level that corresponds to any of
#   those technical zones, you exit.
#
#   TWO MODES:
#
#   MODE 'option_proxy':
#     You supply the technical levels as OPTION PREMIUM values
#     directly (e.g. "1.20, 0.85"). The stop fires when the
#     option premium bar low falls through any level.
#     Use this if you've mapped key levels onto the option's
#     price chart (e.g. prior day option lows, VWAP-anchored
#     option levels).
#
#   MODE 'percentage_proxy':
#     You supply the levels as % drop thresholds from entry
#     (e.g. "15, 30, 50"). The stop fires at the nearest level.
#     This mode approximates "exit if underlying drops through
#     a support level" by converting it to a % option stop.
#     Use this when you know the underlying levels but only have
#     option bars.
#
#   Multiple levels create a tiered exit — the first level
#   breached fires the stop. All levels visible as extra traces.
#
#   A hard stop at hardStopPct remains active as a floor in
#   both modes.
# ============================================================

from backtest_engine import to_minutes, should_eod_exit, append_trace

META = {
    'enabled':     True,
    'id':          'technical_level_stop',
    'name':        'Technical level stop',
    'description': 'Exit when option breaches user-defined price levels (support/resistance/pivots). '
                   'Tiered — first level hit fires the stop.',
    'params': [
        {
            'key':     'levels',
            'label':   'Stop levels (comma-separated)',
            'default': '',
            'hint':    'option_proxy mode: option prices e.g. "1.20, 0.85". '
                       'percentage_proxy mode: % drops from entry e.g. "15, 30, 50"',
        },
        {
            'key':     'levelMode',
            'label':   'Level mode',
            'default': 'percentage_proxy',
            'hint':    'option_proxy = levels are option $ prices. percentage_proxy = levels are % drops from entry.',
        },
        {
            'key':     'hardStopPct',
            'label':   'Hard stop floor (%)',
            'default': 75,
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
    raw = params.get('levels', '').strip()
    if raw:
        for part in raw.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                float(part)
            except ValueError:
                return f"Invalid level value '{part}' — use numbers separated by commas"
    return None


def _parse_levels(raw, mode, entry_price):
    """
    Parse the levels string into a sorted list of option price stops
    (highest to lowest — first breach wins).
    """
    stops = []
    for part in (raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            val = float(part)
        except ValueError:
            continue
        if mode == 'percentage_proxy':
            # Convert % drop to option price
            stop = entry_price * (1 - val / 100)
        else:
            stop = val
        if 0 < stop < entry_price:
            stops.append(round(stop, 4))
    # Sort descending — first breach = nearest to entry
    return sorted(set(stops), reverse=True)


def execute(bars, entry_idx, entry_price, params):
    hard_stop_pct = params['hardStopPct'] / 100
    hard_stop     = entry_price * (1 - hard_stop_pct)
    level_mode    = params.get('levelMode', 'percentage_proxy')

    levels = _parse_levels(params.get('levels', ''), level_mode, entry_price)

    # If no levels defined, fall back to hard stop only
    if not levels:
        levels = [hard_stop]

    # Effective stop is always max(lowest_unbreached_level, hard_stop)
    active_level_idx = 0  # start at the highest (nearest) level
    stop_price = max(levels[active_level_idx] if levels else hard_stop, hard_stop)

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_open = float(bar['open'])
        bar_low  = float(bar['low'])

        trace.append({'time': bar['time'], 'stopPrice': stop_price})

        # Show all levels as extra traces
        for j, lvl in enumerate(levels):
            append_trace(extras, f'Level {j + 1} (${lvl:.2f})', bar, lvl)
        append_trace(extras, 'Hard stop floor', bar, hard_stop)

        if bar_open <= stop_price:
            return {
                'exitBar':         bar,
                'exitReason':      'hard_stop',
                'stopPrice':       bar_open,
                'levelBreached':   stop_price,
                'allLevels':       levels,
                'stopTrace':       trace,
                'extraTraces':     extras,
            }
        if bar_low <= stop_price:
            return {
                'exitBar':         bar,
                'exitReason':      'hard_stop',
                'stopPrice':       stop_price,
                'levelBreached':   stop_price,
                'allLevels':       levels,
                'stopTrace':       trace,
                'extraTraces':     extras,
            }

        if should_eod_exit(bar, params):
            return {
                'exitBar':    bar,
                'exitReason': 'eod',
                'stopPrice':  stop_price,
                'stopTrace':  trace,
                'extraTraces': extras,
            }

    return {
        'exitBar':    bars[-1],
        'exitReason': 'expiry',
        'stopPrice':  stop_price,
        'stopTrace':  trace,
        'extraTraces': extras,
    }
