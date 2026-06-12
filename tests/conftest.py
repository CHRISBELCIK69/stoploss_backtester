"""
conftest.py — shared fixtures for the backtester test suite.

All fixtures use synthetic bar data so tests never make real network
calls unless explicitly opted into via the --integration flag (which
also requires QC_USER_ID and QC_API_TOKEN in the environment).

Run unit/strategy tests only (default, no credentials needed):
    pytest tests/

Run everything including integration tests:
    QC_USER_ID=xxx QC_API_TOKEN=xxx pytest tests/ --integration
"""

import os
import io
import csv
import zipfile
import pytest


# ─────────────────────────────────────────────────────────────
# CLI flag
# ─────────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests that call the real QC API (requires credentials).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: marks tests that call the real QC API"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration"):
        skip = pytest.mark.skip(reason="pass --integration to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


# ─────────────────────────────────────────────────────────────
# Bar builders
# ─────────────────────────────────────────────────────────────

def _make_bar(time_str, open_, high, low, close, volume=100, vwap=None, synthetic=False):
    return {
        "time":      time_str,
        "open":      open_,
        "high":      high,
        "low":       low,
        "close":     close,
        "volume":    volume,
        "vwap":      vwap or close,
        "synthetic": synthetic,
    }


@pytest.fixture
def flat_bars():
    """40 one-minute bars at a constant price of $2.00 — no moves."""
    bars = []
    for i in range(40):
        t = f"2026-01-12 {9 + (i // 60):02d}:{(30 + i) % 60:02d}"
        bars.append(_make_bar(t, 2.00, 2.05, 1.95, 2.00))
    return bars


@pytest.fixture
def rising_bars():
    """
    40 bars starting at $1.00, each bar's close rises $0.10.
    Range: $1.00 → $4.90. Good for testing trail activation.
    """
    bars = []
    for i in range(40):
        minute = 30 + i
        t = f"2026-01-12 {9 + minute // 60:02d}:{minute % 60:02d}"
        c = round(1.00 + i * 0.10, 2)
        bars.append(_make_bar(t, c - 0.02, c + 0.05, c - 0.05, c))
    return bars


@pytest.fixture
def falling_bars():
    """
    40 bars starting at $4.00, each bar's close falls $0.10.
    Range: $4.00 → $0.10. Good for testing hard-stop fires.
    """
    bars = []
    for i in range(40):
        minute = 30 + i
        t = f"2026-01-12 {9 + minute // 60:02d}:{minute % 60:02d}"
        c = round(4.00 - i * 0.10, 2)
        bars.append(_make_bar(t, c + 0.02, c + 0.05, max(0.01, c - 0.05), c))
    return bars


@pytest.fixture
def spike_then_crash_bars():
    """
    20 bars rise to $5.00 (from $2.00), then 20 bars crash back to $1.00.
    Tests strategies that should lock in profits on the spike.
    """
    bars = []
    for i in range(20):
        minute = 30 + i
        t = f"2026-01-12 {9 + minute // 60:02d}:{minute % 60:02d}"
        c = round(2.00 + i * 0.15, 2)
        bars.append(_make_bar(t, c - 0.02, c + 0.05, c - 0.05, c))
    for i in range(20):
        minute = 50 + i
        t = f"2026-01-12 {9 + minute // 60:02d}:{minute % 60:02d}"
        c = round(5.00 - i * 0.20, 2)
        bars.append(_make_bar(t, c + 0.02, c + 0.05, max(0.01, c - 0.05), c))
    return bars


@pytest.fixture
def eod_bars():
    """
    30 bars ending at 15:50 — tests that EOD exits fire at the right time.
    Bars hold a constant $2.00; stop should NOT fire before 15:45.
    """
    bars = []
    for i in range(30):
        h = 15
        m = 20 + i
        t = f"2026-01-12 {h:02d}:{m:02d}"
        bars.append(_make_bar(t, 2.00, 2.05, 1.95, 2.00))
    return bars


@pytest.fixture
def base_contract():
    return {
        "occ":       "SPY260112C00690000",
        "symbol":    "SPY",
        "strike":    690.0,
        "type":      "C",
        "expiry":    "2026-01-12",
        "entryDate": "2026-01-12",
        "entryTime": "09:30",
    }


@pytest.fixture
def base_config():
    return {
        "defaults": {
            "stopLossPct":   100,
            "eodTime":       "15:45",
            "qty":           1,
            "eodMode":       "daily",
            "riskFreeRate":  0.0525,
            "historicalVol": 0.20,
        }
    }


# ─────────────────────────────────────────────────────────────
# Synthetic ZIP helpers (for data_provider_qc tests)
# ─────────────────────────────────────────────────────────────

def _csv_bytes(rows: list) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode()


def make_option_trade_zip(symbol: str, date_str: str,
                          strike: float, expiry_yyyymmdd: str,
                          option_type: str = "C",
                          num_rows: int = 10) -> bytes:
    """
    Build a Lean-format option trade ZIP in memory.
    CSV columns: ms, open, high, low, close, volume  (prices scaled ×10000)
    """
    scale = 10000
    rows = []
    for i in range(num_rows):
        ms = (9 * 3600 + (30 + i) * 60) * 1000
        price = int(2.00 * scale) + i * 100
        rows.append([ms, price, price + 500, price - 500, price, 10])

    strike_lean = str(int(round(strike * 10000)))
    typ = "call" if option_type == "C" else "put"
    csv_name = f"{date_str}_{symbol.lower()}_minute_trade_{typ}_{strike_lean}_{expiry_yyyymmdd}.csv"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, _csv_bytes(rows).decode())
    return buf.getvalue()


def make_iv_zip(symbol: str, date_str: str,
                strike: float, expiry_yyyymmdd: str,
                option_type: str = "C",
                num_rows: int = 10) -> bytes:
    """Build a Lean-format IV ZIP with columns: ms, iv, delta, gamma, vega, theta, rho"""
    rows = []
    for i in range(num_rows):
        ms = (9 * 3600 + (30 + i) * 60) * 1000
        rows.append([ms, 0.35, 0.45, 0.02, 0.08, -0.05, 0.01])

    strike_lean = str(int(round(strike * 10000)))
    typ = "call" if option_type == "C" else "put"
    csv_name = f"{date_str}_{symbol.lower()}_minute_iv_{typ}_{strike_lean}_{expiry_yyyymmdd}.csv"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, _csv_bytes(rows).decode())
    return buf.getvalue()
