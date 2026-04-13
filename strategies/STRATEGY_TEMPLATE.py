# ============================================================
# strategies/STRATEGY_TEMPLATE.py
# Copy this file, rename it, and fill in the three exports.
# Then add it to strategies/registry.py — two lines total.
#
# CHECKLIST:
#   [ ] Rename this file to your_strategy_name.py
#   [ ] Fill in META['id'] (must be unique, snake_case)
#   [ ] Fill in META['name'] (shown in CLI --list-strategies)
#   [ ] Fill in META['description']
#   [ ] Define your params list (drives CLI param prompts)
#   [ ] Implement validate() — return an error string or None
#   [ ] Implement execute() — return { exitBar, exitReason, ...extras }
#   [ ] Add to strategies/registry.py (import + registry entry)
# ============================================================

from backtest_engine import to_minutes

META = {
    'id':          'your_strategy_id',       # unique snake_case identifier
    'name':        'Your strategy name',
    'description': 'What this strategy does and when to use it.',

    # Each param defines one CLI argument / interactive prompt.
    # Supported types: 'number' (default), 'time', 'boolean'
    'params': [
        {
            'key':     'myParam',
            'label':   'My param label',
            'default': 25,
            'min':     1,
            'max':     100,
            'step':    1,
            'hint':    'Tooltip text (optional)',
            # 'type': 'number'   # 'number' | 'time' | 'boolean'
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
    """
    Validate param combinations before running.
    Return a human-readable error string if invalid, or None if OK.
    """
    # if params['myParam'] <= 0:
    #     return 'My param must be greater than 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    """
    Execute the exit strategy bar by bar.

    Walk forward from entry_idx+1 through bars.
    Return as soon as an exit condition is met.
    If nothing triggers, return the last bar as 'expiry'.

    Exit reason conventions:
      'hard_stop'       — fixed stop loss hit
      'trailing_stop'   — trailing stop hit
      'break_even_stop' — stop at break-even hit
      'r_step_stop'     — R-multiple step stop hit
      'eod'             — reached EOD exit time
      'expiry'          — held to last available bar

    Returns a dict with at minimum: exitBar, exitReason
    Any extra keys (e.g. highWaterMark) are passed through to results automatically.
    """
    my_param  = params['myParam']
    eod_time  = params.get('eodTime', '15:45')

    stop_price = entry_price * (1 - my_param / 100)

    for i in range(entry_idx + 1, len(bars)):
        bar      = bars[i]
        bar_low  = float(bar['low'])
        bar_mins = to_minutes(bar['time'][11:16])

        if bar_low <= stop_price:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': stop_price}

        if bar_mins >= to_minutes(eod_time):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': stop_price}

    return {'exitBar': bars[-1], 'exitReason': 'expiry', 'stopPrice': stop_price}
