#!/usr/bin/env python3
# ============================================================
# main.py
# Flask web server — fetches bars once per contract, runs every
# strategy, returns results + price bars + stop traces.
# ============================================================

import json
from flask import Flask, request, jsonify, send_from_directory

from config              import CONFIG
from data_provider       import parse_contracts, fetch_bars
from backtest_engine     import process_contract
from strategies.registry import STRATEGY_LIST, STRATEGY_MAP

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


@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    body      = request.get_json(force=True)
    raw_text  = body.get('contracts', '')
    qty       = int(body.get('qty', 1))

    parsed = parse_contracts(raw_text)
    if parsed['errors']:
        return jsonify({'error': ' | '.join(parsed['errors'])}), 400
    if not parsed['contracts']:
        return jsonify({'error': 'No valid contracts found.'}), 400

    contracts = parsed['contracts']
    # One entry per contract with bars + all strategy results
    contract_results = []
    log_lines = []

    def log(msg):
        log_lines.append(msg)

    for contract in contracts:
        try:
            bars = fetch_bars(contract['occ'], contract['entryDate'], contract['expiry'], CONFIG)
        except Exception as e:
            log(f'ERROR fetching {contract["occ"]}: {e}')
            continue

        if not bars:
            log(f'{contract["occ"]}: no bars — skipping')
            continue

        log(f'\n-- {contract["occ"]}')
        log(f'   {len(bars)} bars loaded ({bars[0]["time"][:10]} to {bars[-1]["time"][:10]})')

        # Slim down bars for the frontend (just time + OHLC)
        bar_data = [
            {'time': b['time'], 'open': b['open'], 'high': b['high'], 'low': b['low'], 'close': b['close']}
            for b in bars
        ]

        strategy_results = []

        for strategy in STRATEGY_MAP.values():
            # Skip strategies that have been toggled off in their META
            if not strategy.META.get('enabled', True):
                continue
            params = {p['key']: p['default'] for p in strategy.META['params']}
            # Inject context for strategies that need external data (e.g. ATR)
            params['_contract'] = contract
            params['_config']   = CONFIG

            err = strategy.validate(params)
            if err:
                log(f'   {strategy.META["name"]}: param error — {err}')
                continue

            result = process_contract(contract, bars, strategy, params, qty, log_fn=log)
            if result:
                # Extract stop trace before it gets stripped
                stop_trace = result.pop('stopTrace', [])
                strategy_results.append({
                    **result,
                    'stopTrace': stop_trace,
                })

        contract_results.append({
            'occ':       contract['occ'],
            'entryDate': contract['entryDate'],
            'entryTime': contract['entryTime'],
            'bars':      bar_data,
            'strategies': strategy_results,
        })

    return jsonify({
        'contracts': contract_results,
        'log':       '\n'.join(log_lines),
    })


if __name__ == '__main__':
    print('Backtester running at http://localhost:8080')
    app.run(host='0.0.0.0', port=8080, debug=True)
