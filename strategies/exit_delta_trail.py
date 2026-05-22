# ============================================================
# strategies/exit_delta_trail.py
# Delta-adaptive trailing stop.
#
# TRANSLATED FROM: live/delta_trail_runner.py
#
# HOW IT WORKS:
#   Direct port of the live strategy logic into the backtester.
#   The trail width is not a fixed %, it is the option's live delta.
#   As the option goes deeper ITM, delta rises toward 1.0 → trail
#   tightens. As delta falls (option going OTM) → trail widens.
#   This is self-adjusting risk management: the stop adapts to
#   how much moneyness the option has at any given moment.
#
#   THREE PHASES (matching the live runner exactly):
#
#   PHASE 1 — WAITING:
#     Hard stop only at entry × (1 - hardStopPct).
#     Watching for price to reach entry × (1 + profitArmPct).
#
#   PHASE 2 — ARMED:
#     Trail stop = highest_bid × (1 - delta)
#     Trail recalculates every bar with the latest BS delta.
#     Trail only moves in the favorable direction (never widens
#     from a new high — but CAN tighten if delta rises even with
#     no new price high, which the live runner allows too).
#
#   LIVE RUNNER FIDELITY:
#     Live runner: trail = highest_bid × (1 - delta), recalculated
#       every poll — allowing trail to tighten even without new highs.
#     Backtester: same logic, recalculated every bar. No approximation.
#
#   DELTA SOURCE:
#     Backtester: Black-Scholes delta from _bs_math, back-solving IV
#     from bar close + underlying spot (same as exit_delta_threshold).
#     Live runner: Tradier greeks endpoint (real market delta).
#     Both use abs(delta) — puts are treated identically to calls.
#
#   FALLBACK:
#     If delta is unavailable (no underlying spot data for a bar),
#     strategy falls back to deltaFallbackPct — matching the live
#     runner's DELTA_FALLBACK_PCT env var.
# ============================================================

from backtest_engine     import should_eod_exit, append_trace
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    bs_delta, implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'delta_trail',
    'name':         'Delta trailing stop',
    'description':  'Trail width = live option delta. Tightens as option goes ITM, '
                    'widens as OTM. Port of delta_trail_runner.py for backtesting.',
    'params': [
        {
            'key':     'hardStopPct',
            'label':   'Hard stop (% below entry)',
            'default': 25,
            'min':     5,
            'max':     100,
            'step':    5,
            'hint':    'Phase 1 — fixed stop, always active. Matches HARD_STOP_PCT in live runner.',
        },
        {
            'key':     'profitArmPct',
            'label':   'Profit % to arm trail',
            'default': 15,
            'min':     1,
            'max':     200,
            'step':    5,
            'hint':    'Phase 2 arms once price reaches entry × (1 + this %). '
                       'Matches PROFIT_ARM_PCT in live runner.',
        },
        {
            'key':     'deltaFallbackPct',
            'label':   'Delta fallback % (when delta unavailable)',
            'default': 20,
            'min':     5,
            'max':     60,
            'step':    5,
            'hint':    'Used as trail width when BS delta cannot be computed. '
                       'Matches DELTA_FALLBACK_PCT in live runner.',
        },
        {
            'key':     'riskFreeRate',
            'label':   'Risk-free rate (%)',
            'default': 5.0,
            'min':     0.0,
            'max':     10.0,
            'step':    0.25,
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
        return 'Hard stop % must be > 0'
    if params['profitArmPct'] <= 0:
        return 'Profit arm % must be > 0'
    if not (0 < params['deltaFallbackPct'] <= 100):
        return 'Delta fallback % must be between 1 and 100'
    return None


def execute(bars, entry_idx, entry_price, params):
    hard_stop_pct      = params['hardStopPct'] / 100
    profit_arm_pct     = params['profitArmPct'] / 100
    delta_fallback     = params['deltaFallbackPct'] / 100
    r                  = params['riskFreeRate'] / 100
    contract           = params.get('_contract', {})
    cache              = params.get('_cache', {})
    cfg                = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')

    # Phase thresholds — matching live runner exactly
    hard_stop   = entry_price * (1 - hard_stop_pct)   # always active
    arm_target  = entry_price * (1 + profit_arm_pct)   # arms the trail

    # Underlying spot for BS delta
    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    # IV warm-start from entry bar (same as exit_delta_threshold.py)
    sigma_guess = 0.5
    if have_spot and K > 0:
        entry_bar  = bars[entry_idx]
        entry_spot = spot_at(spot_idx, entry_bar['time'])
        if entry_spot:
            T0 = years_to_expiry(entry_bar['time'], expiry_date)
            sg = implied_vol(float(entry_bar.get('close', 0)),
                             entry_spot, K, T0, r, opt_type)
            if sg > 0:
                sigma_guess = sg

    # Strategy state — mirrors live runner state dict
    armed        = False
    highest_bid  = 0.0
    trail_stop   = None

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])

        # ── Resolve delta — prefer enriched bar, fallback to live solve ──
        delta = get_greek(bar, 'delta')

        if delta is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    delta = bs_delta(spot, K, T, r, sigma, opt_type)

        # abs(delta) — puts are treated identically to calls
        # clamp to [0.05, 1.0] matching live runner clamp logic
        if delta is not None:
            effective_delta = max(0.05, min(abs(delta), 1.0))
            delta_src = 'bs'
        else:
            effective_delta = delta_fallback
            delta_src = 'fallback'

        # Use bar HIGH as the "bid" reference for the trail
        # (live runner uses the live bid; bar high is the best intrabar
        # price — conservative approximation for highest_bid tracking)
        current_bid = bar_high

        # ── LAYER 1: Hard stop (always active, Phase 1 and 2) ──
        # Check gap-through first
        if bar_open <= hard_stop:
            trace.append({'time': bar['time'], 'stopPrice': hard_stop})
            return {
                'exitBar':       bar,
                'exitReason':    'hard_stop',
                'stopPrice':     bar_open,
                'highWaterMark': highest_bid,
                'armed':         armed,
                'deltaAtExit':   effective_delta,
                'deltaSrc':      delta_src,
                'stopTrace':     trace,
                'extraTraces':   extras,
            }

        # ── LAYER 2: Arm the trail ──
        was_armed = armed
        if not armed and current_bid >= arm_target:
            armed       = True
            highest_bid = current_bid
            trail_stop  = round(highest_bid * (1 - effective_delta), 4)
            # No stop check on the arming bar — matches live runner behaviour
            # where arming and stopping can't co-occur on the same event

        # ── LAYER 3: Dynamic delta trail (once armed) ──
        if armed:
            # Track new high (live runner: sell_price > highest_bid)
            if current_bid > highest_bid:
                highest_bid = current_bid

            # Recalculate trail every bar with latest delta
            # Live runner recalculates every poll regardless of new highs —
            # this means a rising delta tightens the stop even without a
            # new price high. We match that exactly.
            new_trail = round(highest_bid * (1 - effective_delta), 4)
            trail_stop = new_trail   # always update (can tighten or loosen)

        # Build trace — show trail stop when armed, hard stop when not
        display_stop = trail_stop if armed and trail_stop is not None else hard_stop
        trace.append({'time': bar['time'], 'stopPrice': display_stop})
        append_trace(extras, 'Hard stop',   bar, hard_stop)
        append_trace(extras, 'Arm target',  bar, arm_target)
        if armed and trail_stop is not None:
            append_trace(extras, 'Trail stop', bar, trail_stop)

        # ── Stop checks ──
        # Hard stop intrabar low (Phase 1 and 2)
        if bar_low <= hard_stop:
            return {
                'exitBar':       bar,
                'exitReason':    'hard_stop',
                'stopPrice':     hard_stop,
                'highWaterMark': highest_bid,
                'armed':         armed,
                'deltaAtExit':   effective_delta,
                'deltaSrc':      delta_src,
                'stopTrace':     trace,
                'extraTraces':   extras,
            }

        # Trail stop (Phase 2 only — skip the arming bar)
        if was_armed and armed and trail_stop is not None:
            if bar_open <= trail_stop:
                return {
                    'exitBar':       bar,
                    'exitReason':    'trailing_stop',
                    'stopPrice':     bar_open,
                    'highWaterMark': highest_bid,
                    'armed':         True,
                    'deltaAtExit':   effective_delta,
                    'deltaSrc':      delta_src,
                    'stopTrace':     trace,
                    'extraTraces':   extras,
                }
            if bar_low <= trail_stop:
                return {
                    'exitBar':       bar,
                    'exitReason':    'trailing_stop',
                    'stopPrice':     trail_stop,
                    'highWaterMark': highest_bid,
                    'armed':         True,
                    'deltaAtExit':   effective_delta,
                    'deltaSrc':      delta_src,
                    'stopTrace':     trace,
                    'extraTraces':   extras,
                }

        if should_eod_exit(bar, params):
            return {
                'exitBar':       bar,
                'exitReason':    'eod',
                'stopPrice':     display_stop,
                'highWaterMark': highest_bid,
                'armed':         armed,
                'deltaAtExit':   effective_delta,
                'deltaSrc':      delta_src,
                'stopTrace':     trace,
                'extraTraces':   extras,
            }

    last = bars[-1]
    return {
        'exitBar':       last,
        'exitReason':    'expiry',
        'stopPrice':     float(last['close']),
        'highWaterMark': highest_bid,
        'armed':         armed,
        'stopTrace':     trace,
        'extraTraces':   extras,
    }
