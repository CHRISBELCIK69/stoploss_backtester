# strategies/exit_dte_5.py
# Family 4 — DTE threshold variant: 5 DTE
# Thin wrapper — imports logic from exit_dte_threshold.py
from strategies.exit_dte_threshold import validate, execute

META = {
    'enabled':    True,
    'id':         'dte_5',
    'name':       'DTE 5 exit',
    'description':'Exit at EOD when 5 or fewer calendar days remain to expiry.',
    'params': [
        {'key': 'dteDays',     'label': 'Exit at DTE <=',         'default': 5,    'min': 0,  'max': 60,  'step': 1},
        {'key': 'exitAtOpen',  'label': 'Exit at open on DTE day', 'default': False,    'type': 'boolean'},
        {'key': 'hardStopPct', 'label': 'Hard stop (%)',           'default': 50,       'min': 5,  'max': 100, 'step': 5},
        {'key': 'eodTime',     'label': 'EOD exit (CST)',          'default': '15:45',  'type': 'time'},
    ],
}
