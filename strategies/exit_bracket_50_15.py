# ============================================================
# strategies/exit_bracket_50_15.py
# Bracket variant — TP 50% / SL 15%  (R:R 3.3:1)
# Thin wrapper around exit_bracket.py with hardcoded TP/SL.
# ============================================================

from strategies.exit_bracket import execute as _base_execute

TP_PCT = 50
SL_PCT = 15

META = {
    'enabled':     True,
    'id':          'bracket_50_15',
    'name':        f'Bracket TP{TP_PCT}/SL{SL_PCT}',
    'description': f'Take profit at +{TP_PCT}%, stop loss at -{SL_PCT}%. '
                   f'Reward:Risk = {TP_PCT/SL_PCT:.1f}:1.',
    'params': [
        {'key': 'eodTime', 'label': 'EOD exit (CST)', 'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    return None


def execute(bars, entry_idx, entry_price, params):
    # Inject the variant's fixed TP/SL before delegating to the base implementation.
    p = {**params, 'takeProfitPct': TP_PCT, 'stopLossPct': SL_PCT}
    return _base_execute(bars, entry_idx, entry_price, p)
