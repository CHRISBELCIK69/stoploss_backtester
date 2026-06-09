#!/usr/bin/env python3
# ============================================================
# main.py
# Flask web server — fetches bars once per contract, runs every
# strategy, returns results + price bars + stop traces.
# ============================================================

import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

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

# Workers for concurrent QC data fetches.
FETCH_WORKERS = 10

app = Flask(__name__, static_folder=None)


@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    if isinstance(e, HTTPException):
        return e  # let Flask handle 404/405/etc normally
    tb = traceback.format_exc()
    print(f'[ERROR] Unhandled exception: {e}\n{tb}', file=sys.stderr, flush=True)
    return jsonify({'error': str(e), 'traceback': tb}), 500


@app.route('/api/qc_test')
def qc_test():
    """
    Diagnostic endpoint — visit /api/qc_test in your browser to verify
    QC credentials and data access without running a full backtest.
    Tests auth + one known path and returns the raw QC response.
    """
    from qc_client import QCClient, QCFileMissing, QCAuthError
    results = {}
    try:
        client = QCClient()
        results['user_id'] = client.user_id
        results['base_url'] = client.base_url

        # Test 1: auth ping via /api/v2/authenticate
        raw = client._post_raw('/api/v2/authenticate', {})
        results['auth_status'] = raw.status_code
        try:
            results['auth_body'] = raw.json()
        except Exception:
            results['auth_body'] = raw.text[:300]

        # Test 2: try a real data link for a well-known SPY file
        test_path = 'option/usa/minute/spy/20240102_trade.zip'
        raw2 = client._post_raw('/api/v2/data/links/read', {'filePath': test_path})
        results['data_link_status'] = raw2.status_code
        try:
            results['data_link_body'] = raw2.json()
        except Exception:
            results['data_link_body'] = raw2.text[:300]

    except QCAuthError as e:
        results['error'] = f'Auth error: {e}'
    except Exception as e:
        results['error'] = str(e)

    return jsonify(results)


@app.route('/')
def index():
    return send_from_directory('.', 'backtester.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


@app.route('/api/strategies')
def list_strategies():
    return jsonify(STRATEGY_LIST)


@app.route('/api/strategy_source/<strategy_id>')
def get_strategy_source(strategy_id):
    """
    Return the source code + metadata for a registered strategy so the
    in-browser code-fit editor can show the user what's running, with
    a brief logic rundown extracted from the file's leading comment block.
    """
    import inspect
    strategy = STRATEGY_MAP.get(strategy_id)
    if not strategy:
        return jsonify({'error': f'Unknown strategy: {strategy_id}'}), 404
    try:
        source = inspect.getsource(strategy)
    except (OSError, TypeError) as e:
        return jsonify({'error': f'Could not read source: {e}'}), 500

    # Extract the leading file-header comment block — every line that
    # starts with '#' (or is blank between # lines) before the first
    # statement. This is where every strategy keeps its HOW IT WORKS block.
    header_lines = []
    seen_hash = False
    for ln in source.split('\n'):
        stripped = ln.lstrip()
        if stripped.startswith('#'):
            header_lines.append(ln)
            seen_hash = True
        elif stripped == '' and seen_hash:
            header_lines.append(ln)
        elif stripped == '':
            continue          # leading blank lines before any '#'
        else:
            break
    header_comment = '\n'.join(header_lines).rstrip()

    return jsonify({
        'id':             strategy.META['id'],
        'name':           strategy.META['name'],
        'description':    strategy.META.get('description', ''),
        'needs_greeks':   bool(strategy.META.get('needs_greeks', False)),
        'source':         source,
        'header_comment': header_comment,
    })


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


@app.route('/api/run_custom_strategy', methods=['POST'])
def run_custom_strategy():
    """
    Run an ad-hoc Python strategy module (typed into the in-browser editor)
    against ONE contract. The request supplies the full module source code
    plus the contract identifier; we exec the code in an isolated namespace
    and treat the resulting META/validate/execute symbols as a strategy.

    Trust model: this runs locally against the user's own code. No sandbox.
    Same trust as editing a file under strategies/ and running it directly.

    Request body:
        {
          "occ":         "SPY260112C00690000",
          "entryDate":   "2026-01-12",
          "expiry":      "2026-01-12",
          "entryTime":   "09:30",
          "symbol":      "SPY",        (optional, derived from OCC)
          "type":        "C",          (optional)
          "strike":      690,          (optional)
          "code":        "<full Python module source>",
          "params":      { ... overrides for META defaults ... },
          "qty":          1
        }

    Response: same shape as /api/run_one — { result: {...}, appliedParams: {...} }
    """
    body = request.get_json(force=True)
    code = body.get('code', '') or ''
    occ  = body.get('occ', '')
    qty  = int(body.get('qty', 1))

    if not code.strip():
        return jsonify({'error': 'Strategy code is empty'}), 400

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

    # Exec the user's code in an isolated namespace
    ns = {}
    try:
        exec(code, ns)
    except SyntaxError as e:
        return jsonify({'error': f'SyntaxError: {e.msg} at line {e.lineno}'}), 400
    except Exception as e:
        return jsonify({'error': f'Code error during import: {type(e).__name__}: {e}'}), 400

    META       = ns.get('META')
    validate   = ns.get('validate')
    execute_fn = ns.get('execute')
    if not isinstance(META, dict) or not callable(execute_fn):
        return jsonify({
            'error': 'Strategy must define a META dict and an execute(bars, entry_idx, entry_price, params) function'
        }), 400

    # Fetch bars
    try:
        bars = fetch_bars(contract['occ'], contract['entryDate'], contract['expiry'], CONFIG)
    except Exception as e:
        return jsonify({'error': f'Bar fetch failed: {e}'}), 500
    if not bars:
        return jsonify({'error': 'No bars returned for contract'}), 400

    # Enrich greeks if the user's META declares needs_greeks
    underlying_bars_map = {}
    if META.get('needs_greeks') and contract.get('symbol'):
        try:
            ub = fetch_underlying_bars(contract['symbol'], contract['entryDate'],
                                       contract['expiry'], CONFIG)
        except Exception:
            ub = []
        if ub:
            enrich_bars_with_greeks(bars, contract, CONFIG, ub)
            underlying_bars_map[contract['symbol']] = ub

    # Build params from META defaults, apply overrides
    params = {p['key']: p['default'] for p in META.get('params', [])}
    params.update(body.get('params', {}) or {})
    params['_contract'] = contract
    params['_config']   = CONFIG
    params['_cache']    = {'underlyingBars': underlying_bars_map}
    params.setdefault('eodMode', CONFIG.get('defaults', {}).get('eodMode', 'daily'))

    if callable(validate):
        try:
            err = validate(params)
        except Exception as e:
            return jsonify({'error': f'validate() raised: {type(e).__name__}: {e}'}), 400
        if err:
            return jsonify({'error': f'Param error: {err}'}), 400

    # Wrap as a duck-typed strategy module
    from types import SimpleNamespace
    strategy_module = SimpleNamespace(
        META=META,
        validate=validate or (lambda p: None),
        execute=execute_fn,
    )

    try:
        result = process_contract(contract, bars, strategy_module, params, qty)
    except Exception as e:
        import traceback
        tb = traceback.format_exc(limit=6)
        return jsonify({
            'error':     f'Strategy execution error: {type(e).__name__}: {e}',
            'traceback': tb,
        }), 500

    if not result:
        return jsonify({'error': 'Strategy did not return a result (entry bar not found?)'}), 500

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
            tb = traceback.format_exc()
            print(f'[ERROR] fetch_bars({c["occ"]}): {e}\n{tb}', file=sys.stderr, flush=True)
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
    # Cost when triggered: 1 QC fetch per unique underlying symbol
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
            log(f'{occ}: no bars returned for {contract["entryDate"]}→{contract["expiry"]} '
                f'— check Railway logs for 404 or no-match detail')
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
        # cache['underlyingBars'][symbol] to avoid a duplicate QC fetch.
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
