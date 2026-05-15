# ============================================================
# strategies/registry.py
# Central registry — imports every strategy module and exposes
# them as a dict keyed by strategy ID.
#
# TO ADD A NEW STRATEGY:
#   1. Create strategies/your_strategy.py following the standard
#      interface (META, validate, execute).
#   2. Add one import + one registry line below.
#   3. Done — main.py picks it up automatically.
# ============================================================

from strategies import (
    exit_fixed_stop,
    exit_trailing_pct,
    exit_trailing_pct_10,
    exit_trailing_pct_15,
    exit_trailing_pct_20,
    exit_trailing_pct_25,
    exit_trailing_pct_30,
    exit_trailing_dollar,
    exit_profit_lock,
    exit_break_even,
    exit_r_multiple,
    exit_time_decay,
    exit_atr_stop,
    exit_multi_stage_lock,
    exit_partial_exit_scale,
    exit_vwap_stop,
    exit_tsm_stddev,
    exit_tsm_atr_trigger,
    exit_tsm_devstop,
    exit_tsm_volspike,
    exit_tsm_bollinger,
    exit_tsm_devstop_seeded,
    exit_tsm_bollinger_bands,
    exit_tsm_bollinger_armed,
)

# Order controls display order in CLI --list-strategies
_STRATEGIES = [
    exit_fixed_stop,
    exit_trailing_pct,
    exit_trailing_pct_10,
    exit_trailing_pct_15,
    exit_trailing_pct_20,
    exit_trailing_pct_25,
    exit_trailing_pct_30,
    exit_trailing_dollar,
    exit_profit_lock,
    exit_break_even,
    exit_r_multiple,
    exit_time_decay,
    exit_atr_stop,
    exit_multi_stage_lock,
    exit_partial_exit_scale,
    exit_vwap_stop,
    exit_tsm_stddev,
    exit_tsm_atr_trigger,
    exit_tsm_devstop,
    exit_tsm_volspike,
    exit_tsm_bollinger,
    exit_tsm_devstop_seeded,
    exit_tsm_bollinger_bands,
    exit_tsm_bollinger_armed,
]

# Build lookup map: id → module
STRATEGY_MAP = {s.META['id']: s for s in _STRATEGIES}

# Ordered list of META dicts
STRATEGY_LIST = [s.META for s in _STRATEGIES]


def get_strategy(strategy_id):
    """Get a strategy module by ID. Raises KeyError if not registered."""
    s = STRATEGY_MAP.get(strategy_id)
    if not s:
        ids = ', '.join(STRATEGY_MAP.keys())
        raise KeyError(f"Unknown strategy '{strategy_id}' — available: {ids}")
    return s
