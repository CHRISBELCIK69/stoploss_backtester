#!/usr/bin/env python3
# ============================================================
# main.py
# Flask web server — fetches bars once per contract, runs every
# strategy, returns results + price bars + stop traces.
# ============================================================

import json
import time
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_from_directory

from config              import CONFIG
from data_provider       import parse_contracts, fetch_bars, fetch_daily_bars, fetch_underlying_bars
from backtest_engine     import process_contract
from strategies.registry import STRATEGY_LIST, STRATEGY_MAP
from strategies._bs_math import enrich_bars_with_greeks

# Maximum ATR lookback any strategy uses by default. Pre-fetch this many
# daily bars per contract so all ATR-using strategies can slice from the
# same cached list — eliminates 4-5 duplicate fetch_daily_bars calls
# per contract per backtest run.
MAX_ATR_DAYS = 30

# Workers for concurrent HTTP fetches against Polygon.
# Polygon allows enough parallelism that 10 is safe on most plans.
FETCH_WORKERS = 10

app = Flask(__name__, static_folder=None)


@app.route('/')
def index():
    return send_from_directory('.', 'backtester.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


@app.route('/api/strategies')
def list_strategies():
    return jsonify(STRATEGY_LIST)


@app.route('/api/run_one', methods=['POST'])
def run_one_strategy():
    """
    Run ONE strategy against ONE contract with custom param overrides.
    Used by the Bracket Lab slider UI for live tweaking.

    Request body:
        {
          "occ":          "SPY260112C00690000",
          "entryDate":    "2026-01-12",
          "expiry":       "2026-01-12",
          "entryTime":    "09:30",
          "symbol":       "SPY",            (optional, derived from OCC)
          "type":         "C",              (optional)
          "strike":       690,              (optional)
          "strategyId":   "bracket",
          "params":       { "takeProfitPct": 30, "stopLossPct": 12 },
          "qty":           1
        }

    Response:
        { "result": {...strategy result with stopTrace/extraTraces...} }
    """
    body = request.get_json(force=True)
    occ         = body.get('occ', '')
    strategy_id = body.get('strategyId', '')
    overrides   = body.get('params', {}) or {}
    qty         = int(body.get('qty', 1))

    if strategy_id not in STRATEGY_MAP:
        return jsonify({'error': f'Unknown strategy: {strategy_id}'}), 400
    strategy = STRATEGY_MAP[strategy_id]

    contract = {
        'occ':       occ,
        'symbol':    body.get('symbol', occ[:-15] if len(occ) >= 16 else occ),
        'strike':    body.get('strike', 0),
        'type':      body.get('type', 'C'),
        'expiry':    body.get('expiry', ''),
        'entryDate': body.get('entryDate', body.get('expiry', '')),
        'entryTime': body.get('entryTime', '09:30'),
    }
    if not occ or not contract['entryDate']:
        return jsonify({'error': 'occ and entryDate are required'}), 400

    try:
        bars = fetch_bars(contract['occ'], contract['entryDate'], contract['expiry'], CONFIG)
    except Exception as e:
        return jsonify({'error': f'Bar fetch failed: {e}'}), 500
    if not bars:
        return jsonify({'error': 'No bars returned for contract'}), 400

    # Build params: defaults + overrides + injected context
    params = {p['key']: p['default'] for p in strategy.META['params']}
    params.update(overrides)
    params['_contract'] = contract
    params['_config']   = CONFIG
    params.setdefault('eodMode', CONFIG.get('defaults', {}).get('eodMode', 'daily'))

    err = strategy.validate(params)
    if err:
        return jsonify({'error': f'Param error: {err}'}), 400

    result = process_contract(contract, bars, strategy, params, qty)
    if not result:
        return jsonify({'error': 'Strategy did not return a result'}), 500

    # Surface trace fields like the main /api/backtest endpoint does
    stop_trace   = result.pop('stopTrace', [])
    extra_traces = result.pop('extraTraces', {})
    return jsonify({
        'result': {
            **result,
            'stopTrace':   stop_trace,
            'extraTraces': extra_traces,
        },
        'appliedParams': {k: v for k, v in params.items() if not k.startswith('_')},
    })


@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    """
    Run all enabled strategies against the parsed contracts.

    Performance design:
      1. Parse contracts (cheap, in-memory).
      2. Fetch 1-min bars CONCURRENTLY for all contracts (10 workers).
      3. Fetch daily bars CONCURRENTLY for all contracts at MAX_ATR_DAYS
         (one fetch per contract, shared across every ATR-using strategy
         instead of 4-5 duplicate fetches each).
      4. Run strategies SEQUENTIALLY per contract (compute is fast — the
         API was always the bottleneck). Inject _cache into params so
         strategies can read pre-fetched daily bars.
      5. Return timing breakdown so the user can see where time went.
    """
    t_start  = time.time()
    timings  = {}

    body      = request.get_json(force=True)
    raw_text  = body.get('contracts', '')
    qty       = int(body.get('qty', 1))

    parsed = parse_contracts(raw_text)
    if parsed['errors']:
        return jsonify({'error': ' | '.join(parsed['errors'])}), 400
    if not parsed['contracts']:
        return jsonify({'error': 'No valid contracts found.'}), 400

    contracts = parsed['contracts']
    log_lines = []
    def log(msg):
        log_lines.append(msg)

    # ── PHASE 1: Concurrent 1-min bar fetch ────────────────────
    t0 = time.time()
    def _fetch_one(c):
        try:
            return c['occ'], fetch_bars(c['occ'], c['entryDate'], c['expiry'], CONFIG), None
        except Exception as e:
            return c['occ'], None, str(e)

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        fetch_results = list(pool.map(_fetch_one, contracts))
    timings['minBarsMs'] = round((time.time() - t0) * 1000)

    bars_by_occ  = {}
    errors_by_occ = {}
    for occ, bars, err in fetch_results:
        if err:
            errors_by_occ[occ] = err
        elif bars:
            bars_by_occ[occ] = bars

    # ── PHASE 2: Concurrent daily bar fetch (shared cache) ─────
    # Only contracts that successfully fetched 1-min data — no point
    # fetching daily for ones we can't backtest anyway.
    t0 = time.time()
    contracts_with_bars = [c for c in contracts if c['occ'] in bars_by_occ]

    def _fetch_daily_one(c):
        try:
            return c['occ'], fetch_daily_bars(c['occ'], c['entryDate'], MAX_ATR_DAYS, CONFIG)
        except Exception:
            return c['occ'], []

    daily_by_occ = {}
    if contracts_with_bars:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            for occ, daily in pool.map(_fetch_daily_one, contracts_with_bars):
                daily_by_occ[occ] = daily
    timings['dailyBarsMs'] = round((time.time() - t0) * 1000)

    # Pre-cache enabled-strategy list once (avoid re-checking META every contract)
    enabled_strategies = [s for s in STRATEGY_MAP.values() if s.META.get('enabled', True)]

    # ── PHASE 2b: Underlying bars + greeks enrichment ──────────
    # Only fired if any enabled strategy declares needs_greeks=True in
    # its META. Fetches underlying 1-min bars (once per unique symbol)
    # and writes bar['greeks'] = {delta, gamma, theta, vega, charm,
    # vanna, iv, T, dte, S_used} into every option bar.
    #
    # Cost when triggered: 1 Polygon call per unique underlying symbol
    # (e.g. SPY, QQQ) + ~10ms compute per contract. Zero cost when no
    # greeks-using strategy is selected.
    t0 = time.time()
    underlying_bars_by_symbol = {}
    needs_greeks = any(s.META.get('needs_greeks') for s in enabled_strategies)
    if needs_greeks and contracts_with_bars:
        # Unique (symbol, entryDate, expiry) — usually all contracts share
        # one symbol but different expiries can mean different fetch ranges.
        unique_keys = {}
        for c in contracts_with_bars:
            sym = c.get('symbol', '')
            if not sym:
                continue
            key = (sym, c['entryDate'], c['expiry'])
            unique_keys.setdefault(key, c)

        def _fetch_underlying_one(c):
            sym = c['symbol']
            try:
                ub = fetch_underlying_bars(sym, c['entryDate'], c['expiry'], CONFIG)
            except Exception:
                ub = []
            return (sym, c['entryDate'], c['expiry']), ub

        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            for key, ub in pool.map(_fetch_underlying_one, unique_keys.values()):
                underlying_bars_by_symbol[key] = ub

        # Enrich each contract's option bars in place
        for c in contracts_with_bars:
            sym = c.get('symbol', '')
            key = (sym, c['entryDate'], c['expiry'])
            ub  = underlying_bars_by_symbol.get(key, [])
            enrich_bars_with_greeks(bars_by_occ[c['occ']], c, CONFIG, ub)
    timings['greeksEnrichMs'] = round((time.time() - t0) * 1000)

    # ── PHASE 3: Run strategies per contract ───────────────────
    t0 = time.time()
    strategy_default_eod = CONFIG.get('defaults', {}).get('eodMode', 'daily')
    contract_results = []
    total_strategy_runs = 0

    for contract in contracts:
        occ = contract['occ']
        if occ in errors_by_occ:
            log(f'ERROR fetching {occ}: {errors_by_occ[occ]}')
            continue
        if occ not in bars_by_occ:
            log(f'{occ}: no bars — skipping')
            continue

        bars  = bars_by_occ[occ]
        daily = daily_by_occ.get(occ, [])

        log(f'\n-- {occ}')
        log(f'   {len(bars)} bars loaded ({bars[0]["time"][:10]} to {bars[-1]["time"][:10]})  '
            f'daily seed: {len(daily)} bars')

        # Slim down bars for the frontend (just time + OHLC)
        bar_data = [
            {'time': b['time'], 'open': b['open'], 'high': b['high'], 'low': b['low'], 'close': b['close']}
            for b in bars
        ]

        # The cache that every strategy on this contract shares.
        # Strategies look for `params['_cache']['dailyBars']` first
        # before falling back to their own fetch_daily_bars call.
        # Same pattern for underlying bars — exit_delta_threshold reads
        # cache['underlyingBars'][symbol] to avoid a duplicate Polygon fetch.
        cached_underlying = {}
        sym = contract.get('symbol', '')
        ub_key = (sym, contract['entryDate'], contract['expiry'])
        if ub_key in underlying_bars_by_symbol:
            cached_underlying[sym] = underlying_bars_by_symbol[ub_key]

        shared_cache = {
            'dailyBars':      daily,
            'underlyingBars': cached_underlying,
        }

        strategy_results = []
        for strategy in enabled_strategies:
            params = {p['key']: p['default'] for p in strategy.META['params']}
            params['_contract'] = contract
            params['_config']   = CONFIG
            params['_cache']    = shared_cache
            params.setdefault('eodMode', strategy_default_eod)

            err = strategy.validate(params)
            if err:
                log(f'   {strategy.META["name"]}: param error — {err}')
                continue

            total_strategy_runs += 1
            result = process_contract(contract, bars, strategy, params, qty, log_fn=log)
            if result:
                stop_trace   = result.pop('stopTrace', [])
                extra_traces = result.pop('extraTraces', {})
                strategy_results.append({
                    **result,
                    'stopTrace':   stop_trace,
                    'extraTraces': extra_traces,
                })

        contract_results.append({
            'occ':        occ,
            'entryDate':  contract['entryDate'],
            'entryTime':  contract['entryTime'],
            'bars':       bar_data,
            'strategies': strategy_results,
        })

    timings['strategyExecMs'] = round((time.time() - t0) * 1000)
    timings['totalMs']        = round((time.time() - t_start) * 1000)
    timings['contracts']      = len(contract_results)
    timings['strategyRuns']   = total_strategy_runs

    return jsonify({
        'contracts': contract_results,
        'log':       '\n'.join(log_lines),
        'timings':   timings,
    })


if __name__ == '__main__':
    print('Backtester running at http://localhost:8080')
    app.run(host='0.0.0.0', port=8080, debug=True)
