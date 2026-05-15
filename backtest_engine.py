# ============================================================
# backtest_engine.py
# Pure backtesting logic — no I/O, no HTTP calls.
#
# Exports:
#   fill_price(bar)                                                    → float
#   to_minutes(time_str)                                               → int
#   find_entry_bar(bars, entry_date, entry_time_mins)                  → bar | None
#   process_contract(contract, bars, strategy_module, params, qty, log_fn) → result | None
# ============================================================


def fill_price(bar):
    """Get the fill price from a 1-minute bar. Uses close, falls back to 'last'."""
    return float(bar.get('close') or bar.get('last') or 0)


def to_minutes(time_str):
    """Convert 'HH:MM' to total minutes since midnight."""
    h, m = time_str.split(':')
    return int(h) * 60 + int(m)


def append_trace(traces_dict, name, bar, price):
    """
    Helper for strategies that want to emit multiple stop traces.
    Usage in a strategy:
        extras = {}
        ...
        append_trace(extras, 'Hard stop', bar, hard_stop)
        append_trace(extras, 'Floor',     bar, floor_stop)
        return {..., 'extraTraces': extras}
    """
    if price is None:
        return
    traces_dict.setdefault(name, []).append({
        'time': bar['time'],
        'stopPrice': price,
    })


def should_eod_exit(bar, params):
    """
    Check if the EOD exit clause should fire on this bar.

    Behavior controlled by params['eodMode']:
      'daily'   — exit at eodTime EVERY day (current/legacy behavior, default).
                  Correct for 0DTE contracts where entryDate == expiry.
      'expiry'  — only exit at eodTime on the contract's expiry day.
                  Holds positions overnight on multi-day swing trades.

    Reads params['eodTime'] (default '15:45') and, for 'expiry' mode,
    looks up the contract's expiry from params['_contract'].
    """
    eod_time = params.get('eodTime', '15:45')
    bar_mins = to_minutes(bar['time'][11:16])
    if bar_mins < to_minutes(eod_time):
        return False

    mode = params.get('eodMode', 'daily')
    if mode == 'expiry':
        expiry = (params.get('_contract') or {}).get('expiry', '')
        bar_date = bar['time'][:10]
        return bar_date == expiry
    return True


def find_entry_bar(bars, entry_date, entry_time_mins):
    """
    Find the first bar on entry_date at or after entry_time_mins.
    Returns None if no bar found.
    """
    for bar in bars:
        bar_date = bar['time'][:10]
        bar_mins = to_minutes(bar['time'][11:16])
        if bar_date == entry_date and bar_mins >= entry_time_mins:
            return bar
    return None


def process_contract(contract, bars, strategy_module, strategy_params, qty, log_fn=None):
    """
    Process a single contract through the full backtest cycle.

    Steps:
      1. Find the entry bar at or after the signal time.
      2. Delegate exit logic to the selected strategy module.
      3. Calculate P&L.
      4. Return a standardised result dict.

    Returns a result dict or None if the contract should be skipped.
    """
    log = log_fn or (lambda msg: None)

    log(f'\n-- {contract["occ"]}')
    log(f'   strategy: {strategy_module.META["name"]}')
    log(f'   entry target: {contract["entryDate"]} {contract["entryTime"]} CST')

    if not bars:
        log('   no bars returned -- skipping')
        return None

    log(f'   {len(bars)} bars loaded ({bars[0]["time"][:10]} to {bars[-1]["time"][:10]})')

    # ── 1. Find entry bar ──
    entry_time_mins = to_minutes(contract['entryTime'])
    entry_bar = find_entry_bar(bars, contract['entryDate'], entry_time_mins)

    if not entry_bar:
        log(f'   no bar found at/after {contract["entryTime"]} on {contract["entryDate"]} -- skipping')
        return None

    entry_price = fill_price(entry_bar)

    if entry_price == 0:
        log('   entry price is $0 -- no market data at this time, skipping')
        return None

    log(f'   entry bar: {entry_bar["time"]} | close=${entry_price:.2f}')

    # ── 2. Validate strategy params ──
    validation_error = strategy_module.validate(strategy_params)
    if validation_error:
        log(f'   strategy param error: {validation_error}')
        return None

    # ── 3. Delegate exit logic to strategy module ──
    entry_idx = bars.index(entry_bar)
    exit_result = strategy_module.execute(bars, entry_idx, entry_price, strategy_params)

    exit_bar    = exit_result['exitBar']
    exit_reason = exit_result['exitReason']

    # For stop exits: use the stop price as the fill.
    # Strategies already compute the correct fill:
    #   - Normal stop: filled at the stop level
    #   - Gap-through: filled at bar open (set as stopPrice by strategy)
    # For non-stop exits (eod/expiry): use the bar's close price.
    is_stop = 'stop' in exit_reason
    if is_stop and exit_result.get('stopPrice') is not None:
        exit_price = exit_result['stopPrice']
    else:
        exit_price = fill_price(exit_bar)

    # ── 4. Calculate P&L ──
    pnl     = (exit_price - entry_price) * 100 * qty
    ret_pct = (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

    log(f'   exit bar:  {exit_bar["time"]} | close=${exit_price:.2f} | reason={exit_reason}')
    log(f'   P&L:       ${pnl:.2f}  ({ret_pct:.1f}%)')

    result = {
        'occ':          contract['occ'],
        'type':         'Call' if contract['type'] == 'C' else 'Put',
        'entryTs':      entry_bar['time'],
        'exitTs':       exit_bar['time'],
        'entryPrice':   entry_price,
        'exitPrice':    exit_price,
        'pnl':          pnl,
        'retPct':       ret_pct,
        'exitReason':   exit_reason,
        'qty':          qty,
        'strategyId':   strategy_module.META['id'],
        'strategyName': strategy_module.META['name'],
    }

    # Pass through any extra diagnostic fields the strategy returned
    skip_keys = {'exitBar', 'exitReason', 'stopPrice'}
    for k, v in exit_result.items():
        if k not in skip_keys:
            result[k] = v

    return result
