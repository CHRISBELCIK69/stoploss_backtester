# ============================================================
# strategies/exit_iv_crush.py
# Family 5 — Volatility-Based Stops
# IV crush threshold exit.
#
# HOW IT WORKS:
#   Records the implied volatility at entry (back-solved from bar close).
#   Exits when current IV has dropped by more than ivCrushPct% from
#   entry IV — meaning IV crush is actively destroying the option's
#   extrinsic value beyond what was priced in.
#
#   DISTINCT FROM iv_rank_exit:
#     iv_rank_exit tracks relative rank within a rolling window.
#     This strategy tracks ABSOLUTE drop from YOUR entry IV.
#     If you entered at IV=0.45, a 30% crush threshold exits at IV=0.315.
#     This directly answers: "has IV crushed me by more than X% of what
#     I paid for?"
#
#   TWO EXIT CONDITIONS (either fires):
#     1. IV crush: current_IV < entry_IV × (1 - ivCrushPct/100)
#     2. IV spike exit (optional): current_IV > entry_IV × (1 + ivSpikePct/100)
#        Sell into the vol expansion before it mean-reverts.
#
#   Hard stop always active as floor.
# ============================================================

from backtest_engine     import should_eod_exit, append_trace, append_diag
from data_provider       import fetch_underlying_bars
from strategies._bs_math import (
    implied_vol, years_to_expiry,
    build_underlying_index, spot_at, get_greek,
)

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'iv_crush_exit',
    'name':         'IV crush exit',
    'description':  'Exit when IV drops X% from entry IV (crush) or spikes X% above (expansion). '
                    'Tracks absolute IV change from YOUR entry price.',
    'params': [
        {'key': 'ivCrushPct',  'label': 'IV crush threshold (%)',
         'default': 30, 'min': 5, 'max': 90, 'step': 5,
         'hint': 'Exit when IV drops this % below entry IV. 30 = exit if IV falls 30% from entry.'},
        {'key': 'ivSpikePct',  'label': 'IV spike exit (% above entry, 0=off)',
         'default': 0, 'min': 0, 'max': 200, 'step': 10,
         'hint': 'Exit when IV spikes this % above entry IV. 0 = disabled.'},
        {'key': 'minBarsWarmup', 'label': 'Min bars before exit fires',
         'default': 5, 'min': 0, 'max': 30, 'step': 1},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5},
        {'key': 'riskFreeRate','label': 'Risk-free rate (%)',
         'default': 5.0, 'min': 0.0, 'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['ivCrushPct'] <= 0:
        return 'IV crush threshold must be > 0'
    if params['hardStopPct'] <= 0:
        return 'Hard stop % must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    crush_pct     = params['ivCrushPct'] / 100
    spike_pct     = params['ivSpikePct'] / 100
    warmup        = int(params['minBarsWarmup'])
    hard_stop_pct = params['hardStopPct'] / 100
    r             = params['riskFreeRate'] / 100
    contract      = params.get('_contract', {})
    cache         = params.get('_cache', {})
    cfg           = params.get('_config', {})

    K           = float(contract.get('strike', 0))
    opt_type    = contract.get('type', 'C')
    expiry_date = contract.get('expiry', '')
    symbol      = contract.get('symbol', '')
    hard_stop   = entry_price * (1 - hard_stop_pct)

    underlying_bars = cache.get('underlyingBars', {}).get(symbol)
    if underlying_bars is None and symbol:
        underlying_bars = fetch_underlying_bars(
            symbol, contract.get('entryDate', ''), expiry_date, cfg)
    spot_idx  = build_underlying_index(underlying_bars or [])
    have_spot = len(spot_idx) > 0

    # Solve entry IV
    entry_iv    = None
    sigma_guess = 0.5
    if have_spot and K > 0:
        eb = bars[entry_idx]
        es = spot_at(spot_idx, eb['time'])
        if es:
            T0 = years_to_expiry(eb['time'], expiry_date)
            sg = implied_vol(float(eb.get('close', 0)), es, K, T0, r, opt_type)
            if sg > 0:
                entry_iv    = sg
                sigma_guess = sg

    # Pre-compute thresholds from entry IV
    crush_floor = entry_iv * (1 - crush_pct) if entry_iv else None
    spike_ceil  = entry_iv * (1 + spike_pct) if (entry_iv and spike_pct > 0) else None

    trace  = []
    extras = {}
    diag   = {}

    for i in range(entry_idx + 1, len(bars)):
        bar        = bars[i]
        bar_open   = float(bar['open'])
        bar_close  = float(bar['close'])
        bar_low    = float(bar['low'])
        bars_since = i - entry_idx

        current_iv = get_greek(bar, 'iv')
        if current_iv is None and have_spot and K > 0:
            spot = spot_at(spot_idx, bar['time'])
            if spot:
                T = years_to_expiry(bar['time'], expiry_date)
                sigma = implied_vol(bar_close, spot, K, T, r, opt_type,
                                    initial_guess=sigma_guess)
                if sigma > 0:
                    sigma_guess = sigma
                    current_iv  = sigma

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)
        # NOTE: previously this also did
        #   append_trace(extras, 'IV crush floor', bar, crush_floor)
        # which put an IV decimal (~0.31) onto the *option-price* chart
        # at $0.31 — visually meaningless. The threshold belongs on the
        # IV panel below, where it can actually be compared to current_iv.
        # Diagnostic series — IV scaled ×100 so it lives on the same y-axis
        # as proxy-VIX (~15–50). entry_iv + crush_floor (+ spike_ceil if on)
        # are flat reference lines you can watch current_iv cross.
        if current_iv is not None:
            append_diag(diag, 'current_iv', bar, round(current_iv * 100, 2),
                        label='Current IV', unit='%', scaleHint='volatility')
        if entry_iv is not None:
            append_diag(diag, 'entry_iv', bar, round(entry_iv * 100, 2),
                        label='Entry IV', unit='%', scaleHint='volatility')
        if crush_floor is not None:
            append_diag(diag, 'crush_floor', bar, round(crush_floor * 100, 2),
                        label='Crush floor (exit ↓)', unit='%', scaleHint='volatility')
        if spike_ceil is not None:
            append_diag(diag, 'spike_ceil', bar, round(spike_ceil * 100, 2),
                        label='Spike ceiling (exit ↑)', unit='%', scaleHint='volatility')

        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'entryIV': entry_iv, 'ivAtExit': current_iv,
                    'stopTrace': trace, 'extraTraces': extras,
                    'diagnostics': diag}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'entryIV': entry_iv, 'ivAtExit': current_iv,
                    'stopTrace': trace, 'extraTraces': extras,
                    'diagnostics': diag}

        if bars_since >= warmup and current_iv is not None and entry_iv is not None:
            # IV crush
            if crush_floor and current_iv < crush_floor:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'iv_crush', 'entryIV': round(entry_iv, 4),
                        'ivAtExit': round(current_iv, 4),
                        'ivDropPct': round((entry_iv - current_iv) / entry_iv * 100, 1),
                        'stopTrace': trace, 'extraTraces': extras,
                        'diagnostics': diag}
            # IV spike exit
            if spike_ceil and current_iv > spike_ceil:
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_close,
                        'exitType': 'iv_spike', 'entryIV': round(entry_iv, 4),
                        'ivAtExit': round(current_iv, 4),
                        'ivRisePct': round((current_iv - entry_iv) / entry_iv * 100, 1),
                        'stopTrace': trace, 'extraTraces': extras,
                        'diagnostics': diag}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'entryIV': entry_iv, 'ivAtExit': current_iv,
                    'stopTrace': trace, 'extraTraces': extras,
                    'diagnostics': diag}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'entryIV': entry_iv, 'stopTrace': trace, 'extraTraces': extras,
            'diagnostics': diag}
