# strategies/exit_premium_pct_50.py
# Family 1 — Premium % stop variant: 50%
# Thin wrapper — imports logic from exit_premium_pct.py
from strategies.exit_premium_pct import validate, execute

META = {
    'enabled':    True,
    'id':         'premium_pct_50',
    'name':       'Premium stop 50%',
    'description':'Exit when premium falls 50% below entry price.',
    'params': [
        {'key': 'stopPct', 'label': 'Stop loss (%)',   'default': 50,    'min': 1,  'max': 100, 'step': 5},
        {'key': 'eodTime', 'label': 'EOD exit (CST)',  'default': '15:45',  'type': 'time'},
    ],
}
