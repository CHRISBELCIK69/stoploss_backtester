# strategies/exit_delta_040.py
# Family 3 — Delta threshold variant: 0.4
# Thin wrapper — imports logic from exit_delta_threshold.py
from strategies.exit_delta_threshold import validate, execute

META = {
    'enabled':      True,
    'needs_greeks': True,
    'id':           'delta_040',
    'name':         'Delta 0.40 exit',
    'description':  'Exit when |delta| reaches 0.40. Standard delta management level.',
    'params': [
        {'key': 'deltaThreshold', 'label': 'Delta threshold',    'default': 0.4, 'min': 0.05, 'max': 0.95, 'step': 0.05},
        {'key': 'hardStopPct',    'label': 'Hard stop (%)',       'default': 25,          'min': 5,    'max': 100,  'step': 5},
        {'key': 'riskFreeRate',   'label': 'Risk-free rate (%)',  'default': 5.0,         'min': 0.0,  'max': 10.0, 'step': 0.25},
        {'key': 'eodTime',        'label': 'EOD exit (CST)',      'default': '15:45',     'type': 'time'},
    ],
}
