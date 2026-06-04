# ============================================================
# config.py
# All runtime configuration. Credentials and per-deploy settings
# live in environment variables; this file just collects them.
# ============================================================

import os

CONFIG = {

    # ── QuantConnect Data API ────────────────────────────────
    # User ID + API token from quantconnect.com → Account → API.
    # Set these via Railway → Variables (or `export` locally).
    # The data fetch path lives in data_provider_qc.py; this section
    # is read by qc_client.py via os.environ directly (the QC client
    # is env-driven so it can be swapped without touching CONFIG).
    'qc': {
        'userId':       os.environ.get('QC_USER_ID', ''),
        'apiToken':     os.environ.get('QC_API_TOKEN', ''),
        'cacheDir':     os.environ.get('QC_DATA_CACHE_DIR', '/tmp/qc_cache'),
        'cacheMaxGb':   float(os.environ.get('QC_CACHE_MAX_GB', '10')),
    },


    # ── Defaults ─────────────────────────────────────────────
    # Pre-fills defaults for the backtester run.
    'defaults': {
        'stopLossPct': 100,
        'eodTime':    '15:45',
        'qty':         1,
        'strategy':   'fixed_stop',

        # EOD-exit behavior — applies to ALL strategies via the
        # should_eod_exit() helper in backtest_engine.py:
        #   'daily'  — exit at eodTime every day  (correct for 0DTE)
        #   'expiry' — only exit at eodTime on the contract's expiry day
        'eodMode': os.environ.get('EOD_MODE', 'daily'),

        # Black-Scholes greeks calculator fallbacks — read by
        # strategies/_bs_math.py enrich_bars_with_greeks().
        # QC supplies IV per-bar directly, so the Newton solver is
        # only used as a fallback when bar['iv'] is missing.
        'riskFreeRate':  float(os.environ.get('RISK_FREE_RATE',  '0.0525')),
        'historicalVol': float(os.environ.get('HISTORICAL_VOL',  '0.20')),
    },

}
