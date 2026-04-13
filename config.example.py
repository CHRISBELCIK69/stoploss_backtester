# ============================================================
# config.py
# Copy this file to config.py and fill in your Polygon API key.
# Never commit config.py — it's in .gitignore.
# ============================================================

CONFIG = {

    'polygon': {
        'apiKey': 'YOUR_POLYGON_API_KEY_HERE',
    },

    'defaults': {
        'stopLossPct': 100,
        'eodTime':    '15:45',
        'qty':         1,
        'strategy':   'fixed_stop',
    },

}
