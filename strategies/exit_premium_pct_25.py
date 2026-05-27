# strategies/exit_premium_pct_25.py
# Family 1 — Premium % stop variant: 25%
# Thin wrapper — imports logic from exit_premium_pct.py
from strategies.exit_premium_pct import validate, execute

META = {
    'enabled':    True,
    'id':         'premium_pct_25',
    'name':       'Premium stop 25%',
    'description':'Exit when premium falls 25% below entry price.',
    'params': [
        {'key': 'stopPct', 'label': 'Stop loss (%)',   'default': 25,    'min': 1,  'max': 100, 'step': 5},
        {'key': 'eodTime', 'label': 'EOD exit (CST)',  'default': '15:45',  'type': 'time'},
    ],
}
