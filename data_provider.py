# ============================================================
# data_provider.py
# QuantConnect-backed implementation of the data fetching surface.
#
# All fetches go through data_provider_qc, which authenticates against
# the QC Data API and returns bars enriched with QC's pre-computed IV + greeks.
#
# This module exists as a thin compatibility shim so the 22 strategy
# files and main.py that already `from data_provider import ...`
# continue to work without edits. New code should import from
# data_provider_qc directly.
#
# To set up locally / on Railway:
#   QC_USER_ID         — your QC numeric user ID
#   QC_API_TOKEN       — your QC API token
#   QC_DATA_CACHE_DIR  — /data/cache (mount a persistent volume here on Railway)
#   QC_CACHE_MAX_GB    — 10
# ============================================================

from data_provider_qc import (
    # OCC parsing
    OCC_RE,
    build_occ,
    parse_occ,
    parse_contracts,

    # Data fetch
    fetch_bars,
    fetch_underlying_bars,
    fetch_daily_bars,

    # Internals re-exported for any caller that uses them
    _pad_minute_gaps,
)
