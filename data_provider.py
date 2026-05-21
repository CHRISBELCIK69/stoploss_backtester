# ============================================================
# data_provider.py
# All market-data fetching and contract parsing. Polygon only.
#
# Bars are normalised to:
#   { 'time': 'YYYY-MM-DD HH:MM', 'open', 'high', 'low', 'close', 'volume', 'vwap' }
# ============================================================

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import requests


# ─────────────────────────────────────────────
# OCC symbol builder
# ─────────────────────────────────────────────

def build_occ(symbol, strike, option_type, expiry):
    """
    Build an OCC option symbol from human-readable parts.

    OCC format:  SYMBOL + YYMMDD + C/P + 8-digit strike (strike × 1000, zero-padded)

    Examples:
      build_occ('SPY', 694, 'C', '2026-01-13') → 'SPY260113C00694000'
      build_occ('QQQ', 480.5, 'P', '2026-02-19') → 'QQQ260219P00480500'
    """
    year, month, day = expiry.split('-')
    yy = year[2:]
    strike_int = str(math.floor(round(float(strike) * 1000))).zfill(8)
    return f"{symbol.upper()}{yy}{month}{day}{option_type.upper()}{strike_int}"


# ─────────────────────────────────────────────
# Contract list parser
# ─────────────────────────────────────────────

def parse_occ(occ):
    """
    Parse an OCC option symbol back into its components.

    OCC format:  SYMBOL(1-6 chars) + YYMMDD + C/P + 8-digit strike
    The last 15 characters are always: 6-digit date + 1-char type + 8-digit strike.

    Example:
      parse_occ('SPY260113C00694000')
      → { 'symbol': 'SPY', 'expiry': '2026-01-13', 'type': 'C', 'strike': 694.0, 'occ': 'SPY260113C00694000' }
    """
    if len(occ) < 16:
        raise ValueError(f"Invalid OCC symbol '{occ}' — too short")

    symbol     = occ[:-15]
    date_part  = occ[-15:-9]     # YYMMDD
    option_type = occ[-9]        # C or P
    strike_raw = occ[-8:]        # 8 digits, strike × 1000

    if option_type not in ('C', 'P'):
        raise ValueError(f"Invalid OCC symbol '{occ}' — expected C or P, got '{option_type}'")

    yy, mm, dd = date_part[:2], date_part[2:4], date_part[4:6]
    expiry     = f"20{yy}-{mm}-{dd}"
    strike     = int(strike_raw) / 1000

    return {
        'symbol': symbol.upper(),
        'expiry': expiry,
        'type':   option_type,
        'strike': strike,
        'occ':    occ.upper(),
    }


def parse_contracts(raw_text):
    """
    Parse a multi-line contract string.

    Line formats (comma-separated):
      OCC_TICKER, ENTRY_TIME                      (0DTE — entry date = expiry)
      OCC_TICKER, ENTRY_TIME, ENTRY_DATE           (swing — explicit entry date)

    ENTRY_TIME: HH:MM (24h)
    ENTRY_DATE: YYYY-MM-DD

    Lines starting with '#' are treated as comments and ignored.

    Returns:
      { 'contracts': [...], 'errors': [...] }
    """
    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip() and not l.strip().startswith('#')]
    contracts = []
    errors = []

    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split(',')]

        if len(parts) < 2:
            errors.append(f"Line {i + 1}: need at least 2 fields — OCC_TICKER, ENTRY_TIME")
            continue

        occ_raw    = parts[0]
        entry_time = parts[1]
        entry_date = parts[2] if len(parts) >= 3 else None

        try:
            parsed = parse_occ(occ_raw)
        except ValueError as e:
            errors.append(f"Line {i + 1}: {e}")
            continue

        contracts.append({
            'symbol':    parsed['symbol'],
            'strike':    parsed['strike'],
            'type':      parsed['type'],
            'expiry':    parsed['expiry'],
            'entryDate': entry_date or parsed['expiry'],  # 0DTE default if no date given
            'entryTime': entry_time,
            'occ':       parsed['occ'],
        })

    return {'contracts': contracts, 'errors': errors}


# ─────────────────────────────────────────────
# Provider router
# ─────────────────────────────────────────────

def fetch_bars(occ, start_date, end_date, cfg):
    """
    Fetch 1-minute OHLCV bars for an option contract from Polygon.

    Returns bars normalised to:
      [{ 'time': 'YYYY-MM-DD HH:MM', 'open', 'high', 'low', 'close', 'volume', 'vwap' }, ...]
    """
    api_key = cfg.get('polygon', {}).get('apiKey', '').strip()
    if not api_key or api_key == 'YOUR_POLYGON_API_KEY_HERE':
        raise ValueError('Open config.py and set polygon.apiKey')
    return fetch_time_sales_polygon(occ, start_date, end_date, api_key)


def fetch_underlying_bars(ticker, start_date, end_date, cfg):
    """
    Fetch 1-minute OHLC bars for the UNDERLYING stock (not the option).
    Used by delta-based strategies that need to compute Black-Scholes IV
    and delta from the spot price.

    Polygon endpoint:
      GET /v2/aggs/ticker/{ticker}/range/1/minute/{from}/{to}
        (no O: prefix — that's options only)

    Returns bars in the same shape as option bars (close/high/low/open/time).
    Time strings are in Eastern timezone to align with option bar timestamps.
    """
    api_key = cfg.get('polygon', {}).get('apiKey', '').strip()
    if not api_key or api_key == 'YOUR_POLYGON_API_KEY_HERE':
        return []

    params = urlencode({
        'adjusted': 'true',
        'sort':     'asc',
        'limit':    50000,
        'apiKey':   api_key,
    })
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker.upper()}/range/1/minute/{start_date}/{end_date}?{params}"

    try:
        resp = requests.get(url, timeout=30)
        if not resp.ok:
            return []
        data = resp.json()
        if data.get('status') in ('NOT_FOUND', 'ERROR'):
            return []
        results = data.get('results') or []
        if not results:
            return []
        return [
            {
                'time':   _ms_to_eastern(bar['t']),
                'open':   bar['o'],
                'high':   bar['h'],
                'low':    bar['l'],
                'close':  bar['c'],
                'volume': bar.get('v', 0),
                'vwap':   bar.get('vw', bar['c']),
            }
            for bar in results
        ]
    except Exception:
        return []


def fetch_daily_bars(occ, end_date, num_days, cfg):
    """
    Fetch daily OHLCV bars for ATR computation.
    Returns the last num_days of daily bars ending at end_date.
    Only supports Polygon (daily option history requires it).
    """
    api_key = cfg.get('polygon', {}).get('apiKey', '').strip()
    if not api_key or api_key == 'YOUR_POLYGON_API_KEY_HERE':
        return []

    # Go back extra days to account for weekends/holidays
    from datetime import datetime as dt, timedelta
    end_dt   = dt.strptime(end_date, '%Y-%m-%d')
    start_dt = end_dt - timedelta(days=num_days * 2)
    start    = start_dt.strftime('%Y-%m-%d')

    ticker = f"O:{occ}"
    params = urlencode({
        'adjusted': 'false',
        'sort':     'asc',
        'limit':    100,
        'apiKey':   api_key,
    })
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end_date}?{params}"

    try:
        resp = requests.get(url, timeout=30)
        if not resp.ok:
            return []
        data = resp.json()
        results = data.get('results') or []
        if not results:
            return []

        daily = [
            {
                'date':  _ms_to_eastern(bar['t'])[:10],
                'open':  bar['o'],
                'high':  bar['h'],
                'low':   bar['l'],
                'close': bar['c'],
            }
            for bar in results
        ]
        # Return only the last num_days
        return daily[-num_days:]
    except Exception:
        return []


# ─────────────────────────────────────────────
# Polygon / Massive implementation
# ─────────────────────────────────────────────

def fetch_time_sales_polygon(occ, start_date, end_date, api_key):
    """
    Fetch 1-minute aggregate bars from Polygon (Massive).

    Endpoint:
      GET https://api.polygon.io/v2/aggs/ticker/O:{occ}/range/1/minute/{from}/{to}
        ?adjusted=false&sort=asc&limit=50000&apiKey={key}

    Timestamps are returned in milliseconds UTC and converted to
    'YYYY-MM-DD HH:MM' in US/Eastern time.
    """
    ticker = f"O:{occ}"
    params = urlencode({
        'adjusted': 'false',
        'sort':     'asc',
        'limit':    50000,
        'apiKey':   api_key,
    })
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{start_date}/{end_date}?{params}"

    resp = requests.get(url, timeout=30)

    if not resp.ok:
        raise RuntimeError(f"Polygon HTTP {resp.status_code} for {occ}: {resp.text[:200]}")

    data = resp.json()

    if data.get('status') in ('NOT_FOUND', 'ERROR'):
        return []

    results = data.get('results') or []
    if not results:
        return []

    return [
        {
            'time':   _ms_to_eastern(bar['t']),
            'open':   bar['o'],
            'high':   bar['h'],
            'low':    bar['l'],
            'close':  bar['c'],
            'volume': bar['v'],
            'vwap':   bar.get('vw', bar['c']),
        }
        for bar in results
    ]


def _ms_to_eastern(ms):
    """Convert millisecond UTC timestamp to 'YYYY-MM-DD HH:MM' in US/Eastern."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ZoneInfo('America/New_York'))
    return dt.strftime('%Y-%m-%d %H:%M')


