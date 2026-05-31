# ============================================================
# config.py
# Reads from environment variables FIRST (for prod deploys), then
# falls back to hardcoded values (for local dev).
# This file IS committed — credentials live in env vars only.
# ============================================================

import os

CONFIG = {

    # ── Polygon / Massive ────────────────────────────────────
    # Get your API key at: https://massive.com/dashboard/keys
    # In production, set POLYGON_API_KEY in your hosting platform's env vars
    # (Railway: Variables tab; Render: Environment Variables; etc.)

    'polygon': {
        # MUST be set via env var — no hardcoded fallback.
        # Local dev: `export POLYGON_API_KEY=...` before running.
        # Railway/Render: set POLYGON_API_KEY in the platform's Variables UI.
        # The previously-committed key has been rotated; do not re-add a fallback.
        'apiKey': os.environ.get('POLYGON_API_KEY', 'M4AWhflTA70bQJaj7p_uR4juaJ_Z6WO0'),
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

        # Black-Scholes greeks calculator settings — read by
        # strategies/_bs_math.py enrich_bars_with_greeks().
        # riskFreeRate:  annual risk-free rate (decimal). Track Fed funds.
        # historicalVol: fallback IV used when the Newton-Raphson IV
        #                solver fails or no bar close is available.
        'riskFreeRate':  float(os.environ.get('RISK_FREE_RATE',  '0.0525')),
        'historicalVol': float(os.environ.get('HISTORICAL_VOL',  '0.20')),
    },

}
