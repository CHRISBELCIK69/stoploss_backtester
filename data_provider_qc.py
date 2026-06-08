# ============================================================
# data_provider_qc.py
# QuantConnect-backed implementation of the data_provider surface.
#
# QuantConnect-backed data provider. Exposes the same function
# signatures as the prior provider so main.py and strategies need
# no edits.
#
# Public surface (same as old data_provider):
#   parse_contracts(raw_text)        — pure text, unchanged
#   parse_occ(occ)                   — pure text, unchanged
#   build_occ(symbol, K, type, exp)  — pure text, unchanged
#   fetch_bars(occ, s, e, cfg)       — option minute bars + IV + greeks
#   fetch_underlying_bars(ticker,…)  — equity minute bars
#   fetch_daily_bars(occ, end, n, cfg) — daily option bars
#
# Bars returned by fetch_bars now carry pre-populated greek fields:
#     bar = {
#       'time':   'YYYY-MM-DD HH:MM',
#       'open', 'high', 'low', 'close', 'volume', 'vwap',
#       'iv':     0.4523,    # QC's pre-computed IV  (decimal, not %)
#       'delta':  0.4421,
#       'gamma':  0.0312,
#       'theta': -0.0187,
#       'vega':   0.0834,
#       'synthetic': False,
#     }
# When 'iv' is present, _bs_math.enrich_bars_with_greeks skips the
# Newton-Raphson IV solve and uses QC's value directly.
# ============================================================

import io
import re
import zipfile
import csv
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional, Tuple

from qc_client import QCClient, QCFileMissing, QCAuthError
from cache_qc  import get_cache


# ─────────────────────────────────────────────────────────────
# OCC parsing
# ─────────────────────────────────────────────────────────────

OCC_RE = re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')


def build_occ(symbol: str, strike: float, option_type: str, expiry: str) -> str:
    """
    Build a standard OCC ticker string.
      symbol    'SPY'
      strike    467.5
      option_type 'C' or 'P'
      expiry    'YYYY-MM-DD' or 'YYMMDD'
    """
    sym = symbol.upper().strip()
    typ = option_type.upper().strip()
    if typ not in ('C', 'P'):
        raise ValueError(f'option_type must be C or P, got {option_type}')
    if '-' in expiry:
        exp_yymmdd = datetime.strptime(expiry, '%Y-%m-%d').strftime('%y%m%d')
    else:
        exp_yymmdd = expiry
    strike_int = int(round(float(strike) * 1000))
    return f'{sym}{exp_yymmdd}{typ}{strike_int:08d}'


def parse_occ(occ: str) -> Dict:
    """Parse an OCC ticker into its constituent parts."""
    m = OCC_RE.match(occ.strip().upper())
    if not m:
        raise ValueError(f'Bad OCC: {occ!r}')
    symbol, yymmdd, typ, strike_str = m.groups()
    expiry = '20' + yymmdd[:2] + '-' + yymmdd[2:4] + '-' + yymmdd[4:6]
    strike = int(strike_str) / 1000.0
    return {
        'symbol':  symbol,
        'expiry':  expiry,
        'type':    typ,
        'strike':  strike,
    }


def parse_contracts(raw_text: str) -> List[Dict]:
    """
    Parse a multi-line paste of contracts. Each line:
      OCC,YYYY-MM-DD,HH:MM       — entry date + time
      OCC                         — defaults to entry date = expiry, 09:30
    Lines starting with # are comments. Blank lines ignored.
    Returns {'contracts': [...], 'errors': [...]}.
    """
    contracts = []
    errors = []
    for raw in (raw_text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(',')]
        occ = parts[0]
        try:
            meta = parse_occ(occ)
        except ValueError as e:
            errors.append(f'Bad line {raw!r}: {e}')
            continue
        entry_date = parts[1] if len(parts) > 1 and parts[1] else meta['expiry']
        entry_time = parts[2] if len(parts) > 2 and parts[2] else '09:30'
        contracts.append({
            'occ':       occ,
            'symbol':    meta['symbol'],
            'expiry':    meta['expiry'],
            'type':      meta['type'],
            'strike':    meta['strike'],
            'entryDate': entry_date,
            'entryTime': entry_time,
        })
    return {'contracts': contracts, 'errors': errors}


# ─────────────────────────────────────────────────────────────
# QC client singleton
# ─────────────────────────────────────────────────────────────

_qc: Optional[QCClient] = None


def _client() -> QCClient:
    global _qc
    if _qc is None:
        _qc = QCClient()
    return _qc


def _fetch_zip(file_path: str) -> Optional[bytes]:
    """Fetch a Lean data ZIP, hitting the cache first. None if missing."""
    cache = get_cache()
    cached = cache.get(file_path)
    if cached is not None:
        return cached
    try:
        data = _client().download_file(file_path)
    except QCFileMissing:
        return None
    cache.put(file_path, data)
    return data


# ─────────────────────────────────────────────────────────────
# Date helpers
# ─────────────────────────────────────────────────────────────

ET = ZoneInfo('America/New_York')


def _date_range(start_yyyy_mm_dd: str, end_yyyy_mm_dd: str) -> List[str]:
    """Inclusive list of dates as YYYYMMDD strings (Lean's date format)."""
    s = datetime.strptime(start_yyyy_mm_dd, '%Y-%m-%d').date()
    e = datetime.strptime(end_yyyy_mm_dd,   '%Y-%m-%d').date()
    out = []
    d = s
    while d <= e:
        out.append(d.strftime('%Y%m%d'))
        d += timedelta(days=1)
    return out


def _lean_ms_to_eastern(date_str: str, ms_since_midnight: int) -> str:
    """
    Lean stores time as milliseconds since midnight US/Eastern on the
    file's date. Convert to 'YYYY-MM-DD HH:MM'.
    """
    base = datetime.strptime(date_str, '%Y%m%d').replace(tzinfo=ET)
    dt   = base + timedelta(milliseconds=int(ms_since_midnight))
    return dt.strftime('%Y-%m-%d %H:%M')


# ─────────────────────────────────────────────────────────────
# OCC → Lean filename matching inside the ZIP
# ─────────────────────────────────────────────────────────────

def _matches_occ(filename: str, occ_parts: Dict) -> bool:
    """
    Lean's intra-ZIP CSV names typically include the strike, type, and
    expiry. Format varies slightly across data types; we match on the
    pieces that must be present rather than the exact layout.

    Example name we expect to recognize:
      20260520_spy_minute_trade_call_467500_20260520.csv
    """
    fn  = filename.lower()
    typ = 'call' if occ_parts['type'] == 'C' else 'put'
    # Strike in Lean's format: integer cents × 10 (so 467.5 → 4675000)
    strike_lean = str(int(round(occ_parts['strike'] * 10000)))
    exp_yyyymmdd = occ_parts['expiry'].replace('-', '')
    return typ in fn and strike_lean in fn and exp_yyyymmdd in fn


def _parse_lean_csv(text: str, columns: List[str]) -> List[Dict]:
    """Generic Lean CSV parser. Returns list of dicts keyed by `columns`."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or len(row) < len(columns):
            continue
        out.append({c: row[i] for i, c in enumerate(columns)})
    return out


# Lean stores option prices scaled × 10000. Verify on first real fetch
# and adjust if needed.
PRICE_SCALE_OPTION = 10000.0
PRICE_SCALE_EQUITY = 10000.0


# ─────────────────────────────────────────────────────────────
# Public fetch functions
# ─────────────────────────────────────────────────────────────

def fetch_bars(occ: str, start_date: str, end_date: str, cfg=None) -> List[Dict]:
    """
    Fetch 1-minute OHLCV bars for an option contract from QC.

    Returns bars normalised to:
      [{ 'time': 'YYYY-MM-DD HH:MM', 'open', 'high', 'low', 'close',
         'volume', 'vwap',
         'iv', 'delta', 'gamma', 'theta', 'vega',  (when available)
         'synthetic': False }, ...]
    """
    occ_parts = parse_occ(occ)
    symbol    = occ_parts['symbol']

    bars_by_time: Dict[str, Dict] = {}

    for date_str in _date_range(start_date, end_date):
        # 1. Trade bars (OHLCV)
        zip_bytes = _fetch_zip(_client().option_trade_path(symbol, date_str))
        if zip_bytes:
            for row in _read_zip_csv(zip_bytes, occ_parts,
                                     ['ms', 'open', 'high', 'low', 'close', 'volume']):
                t = _lean_ms_to_eastern(date_str, int(row['ms']))
                bars_by_time[t] = {
                    'time':      t,
                    'open':      float(row['open'])  / PRICE_SCALE_OPTION,
                    'high':      float(row['high'])  / PRICE_SCALE_OPTION,
                    'low':       float(row['low'])   / PRICE_SCALE_OPTION,
                    'close':     float(row['close']) / PRICE_SCALE_OPTION,
                    'volume':    float(row['volume']),
                    'vwap':      float(row['close']) / PRICE_SCALE_OPTION,
                    'synthetic': False,
                }

        # 2. IV + greeks (Lean's "OptionUniverse"-style IV file)
        iv_zip = _fetch_zip(_client().option_iv_path(symbol, date_str))
        if iv_zip:
            # Column order per Lean's IV files: time, iv, delta, gamma, vega, theta, rho
            for row in _read_zip_csv(iv_zip, occ_parts,
                                     ['ms', 'iv', 'delta', 'gamma',
                                      'vega', 'theta', 'rho']):
                t = _lean_ms_to_eastern(date_str, int(row['ms']))
                bar = bars_by_time.get(t)
                if bar is None:
                    continue
                # Lean's IV is a decimal (e.g. 0.4523). Greek units match
                # the trader-friendly convention used elsewhere in this
                # codebase (delta in [-1,1], gamma per $1 spot, theta per
                # day, vega per 1% IV). If QC ships them annualized we'd
                # convert here.
                bar['iv']    = _safe_float(row['iv'])
                bar['delta'] = _safe_float(row['delta'])
                bar['gamma'] = _safe_float(row['gamma'])
                bar['theta'] = _safe_float(row['theta'])
                bar['vega']  = _safe_float(row['vega'])

    bars = [bars_by_time[t] for t in sorted(bars_by_time)]
    return _pad_minute_gaps(bars)


def fetch_underlying_bars(ticker: str, start_date: str, end_date: str, cfg=None) -> List[Dict]:
    """Fetch 1-minute OHLC bars for an underlying equity."""
    bars: List[Dict] = []
    for date_str in _date_range(start_date, end_date):
        zip_bytes = _fetch_zip(_client().equity_trade_path(ticker, date_str))
        if not zip_bytes:
            continue
        # Equity ZIPs contain a single CSV (no per-contract filter)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith('.csv'):
                    continue
                text = zf.read(name).decode('utf-8', errors='ignore')
                for row in _parse_lean_csv(text,
                                           ['ms', 'open', 'high', 'low', 'close', 'volume']):
                    t = _lean_ms_to_eastern(date_str, int(row['ms']))
                    bars.append({
                        'time':   t,
                        'open':   float(row['open'])  / PRICE_SCALE_EQUITY,
                        'high':   float(row['high'])  / PRICE_SCALE_EQUITY,
                        'low':    float(row['low'])   / PRICE_SCALE_EQUITY,
                        'close':  float(row['close']) / PRICE_SCALE_EQUITY,
                        'volume': float(row['volume']),
                    })
                break  # single CSV expected
    bars.sort(key=lambda b: b['time'])
    return bars


def fetch_daily_bars(occ: str, end_date: str, num_days: int, cfg=None) -> List[Dict]:
    """
    Fetch up to num_days of daily bars for an option contract, ending
    on end_date. Lean stores daily option data in a single ZIP per
    symbol containing all dates for each contract.
    """
    occ_parts = parse_occ(occ)
    symbol    = occ_parts['symbol']
    zip_bytes = _fetch_zip(_client().option_daily_path(symbol))
    if not zip_bytes:
        return []

    end_dt   = datetime.strptime(end_date, '%Y-%m-%d').date()
    start_dt = end_dt - timedelta(days=num_days * 2)  # buffer for non-trading days

    out: List[Dict] = []
    for row in _read_zip_csv(zip_bytes, occ_parts,
                             ['date', 'open', 'high', 'low', 'close', 'volume']):
        try:
            d = datetime.strptime(row['date'], '%Y%m%d').date()
        except ValueError:
            continue
        if d < start_dt or d > end_dt:
            continue
        out.append({
            'time':   d.strftime('%Y-%m-%d'),
            'open':   float(row['open'])  / PRICE_SCALE_OPTION,
            'high':   float(row['high'])  / PRICE_SCALE_OPTION,
            'low':    float(row['low'])   / PRICE_SCALE_OPTION,
            'close':  float(row['close']) / PRICE_SCALE_OPTION,
            'volume': float(row['volume']),
        })
    out.sort(key=lambda b: b['time'])
    return out[-num_days:]


# ─────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────

def _read_zip_csv(zip_bytes: bytes, occ_parts: Dict,
                  columns: List[str]) -> List[Dict]:
    """
    Open a Lean data ZIP, find the CSV for the requested OCC, parse it
    with the supplied column ordering. Returns [] if no match.
    """
    out = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith('.csv'):
                continue
            if not _matches_occ(name, occ_parts):
                continue
            text = zf.read(name).decode('utf-8', errors='ignore')
            out.extend(_parse_lean_csv(text, columns))
    return out


def _safe_float(x) -> Optional[float]:
    try:
        v = float(x)
        if v != v:  # NaN check
            return None
        return v
    except (TypeError, ValueError):
        return None


def _pad_minute_gaps(bars: List[Dict], max_gap_minutes: int = 30) -> List[Dict]:
    """
    Fill missing intraday minutes with carry-forward synthetic bars.
    Gaps > max_gap_minutes are NOT padded (overnight / lunch / halts).
    """
    if not bars:
        return bars
    out = [bars[0]]
    for cur in bars[1:]:
        prev = out[-1]
        try:
            t_prev = datetime.strptime(prev['time'], '%Y-%m-%d %H:%M')
            t_cur  = datetime.strptime(cur['time'],  '%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            out.append(cur)
            continue
        gap_min = int((t_cur - t_prev).total_seconds() // 60)
        if 1 < gap_min <= max_gap_minutes:
            carry = prev['close']
            for k in range(1, gap_min):
                t_fill = t_prev + timedelta(minutes=k)
                out.append({
                    'time':      t_fill.strftime('%Y-%m-%d %H:%M'),
                    'open':      carry, 'high': carry,
                    'low':       carry, 'close': carry,
                    'volume':    0,     'vwap':  carry,
                    'synthetic': True,
                    # Greeks carry forward too — the bar inherits the
                    # last real bar's iv/delta/etc. if they were set.
                    'iv':    prev.get('iv'),
                    'delta': prev.get('delta'),
                    'gamma': prev.get('gamma'),
                    'theta': prev.get('theta'),
                    'vega':  prev.get('vega'),
                })
        out.append(cur)
    return out
