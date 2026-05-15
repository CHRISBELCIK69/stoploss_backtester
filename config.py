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
        'apiKey': os.environ.get('POLYGON_API_KEY', 'YOUR_POLYGON_API_KEY_HERE'),
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
    },

}
