import re
import os
import requests
import time
import threading
import logging
from datetime import datetime, timedelta

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================
ACCESS_TOKEN  = os.environ.get("ACCESS_TOKEN")
ACCOUNT_ID    = os.environ.get("ACCOUNT_ID")
API_BASE_URL  = os.environ.get("API_BASE_URL", "https://sandbox.tradier.com/v1")

PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() == "true"

# ============================================
# BOLLINGER VARIABLES — tune in Railway
# ============================================
MA_PERIOD   = int(os.environ.get("MA_PERIOD",   "20"))
WARMUP_BARS = int(os.environ.get("WARMUP_BARS", "10"))
HARD_STOP   = float(os.environ.get("HARD_STOP", "0.25"))

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json"
})

active_positions = set()
sold_positions   = set()
positions_lock   = threading.Lock()


# ============================================
# TRADIER HELPERS
# ============================================
def get_positions():
    resp = session.get(f"{API_BASE_URL}/accounts/{ACCOUNT_ID}/positions")
    resp.raise_for_status()
    data = resp.json().get("positions", {})
    if not data or data == "null" or isinstance(data, str):
        return []
    positions = data.get("position", [])
    if isinstance(positions, dict):
        positions = [positions]
    return positions or []


def get_quote(option_symbol):
    resp = session.get(
        f"{API_BASE_URL}/markets/quotes",
        params={"symbols": option_symbol, "greeks": "false"}
    )
    resp.raise_for_status()
    quote = resp.json().get("quotes", {}).get("quote", {})
    last  = float(quote.get("last") or 0)
    ask   = float(quote.get("ask")  or 0)
    bid   = float(quote.get("bid")  or 0)
    return last or ask or bid or 0


def get_intraday_bars(option_symbol):
    resp = session.get(
        f"{API_BASE_URL}/markets/timesales",
        params={
            "symbol":         option_symbol,
            "interval":       "1min",
            "start":          datetime.now().strftime("%Y-%m-%d 09:30"),
            "end":            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "session_filter": "open"
        }
    )
    resp.raise_for_status()
    series = resp.json().get("series", {})
    if not series or series == "null":
        return []
    data = series.get("data", [])
    return data if isinstance(data, list) else [data]


def get_bar_low(option_symbol):
    bars = get_intraday_bars(option_symbol)
    if not bars:
        return 0
    return float(bars[-1].get("low", 0))


def sell_asset(option_symbol, underlying, quantity):
    if PAPER_TRADING:
        log.info(f"[PAPER] SELL skipped — {option_symbol}  qty={quantity}")
        return

    try:
        resp = session.post(
            f"{API_BASE_URL}/accounts/{ACCOUNT_ID}/orders",
            data={
                "class":         "option",
                "symbol":        underlying,
                "option_symbol": option_symbol,
                "side":          "sell_to_close",
                "quantity":      str(int(quantity)),
                "type":          "market",
                "duration":      "day"
            }
        )
        resp.raise_for_status()
        result = resp.json().get("order", {})
        log.info(f"SOLD — ID: {result.get('id')}  Status: {result.get('status')}")
    except Exception as e:
        log.info(f"[{option_symbol}] Sell failed: {e}")


# ============================================
# MA CALCULATION
# ============================================
def compute_ma(option_symbol):
    bars = get_intraday_bars(option_symbol)
    if not bars:
        return None
    closes       = [float(b["close"]) for b in bars]
    window_start = max(0, len(closes) - MA_PERIOD)
    window       = closes[window_start:]
    return round(sum(window) / len(window), 4)


# ============================================
# BOLLINGER MA EXIT
# ============================================
def assign_stop(entry, option_symbol, underlying, quantity):
    hard_stop    = round(entry * (1 - HARD_STOP), 2)
    bars_elapsed = 0
    prev_close   = entry
    prev_ma      = entry

    log.info(f"[{option_symbol}] ── BOLLINGER START ──")
    log.info(f"[{option_symbol}] Entry={entry}  "
             f"Hard stop={hard_stop} ({HARD_STOP*100}% below entry)  "
             f"Warmup={WARMUP_BARS} bars  MA period={MA_PERIOD}")

    while True:
        try:
            current_price = get_quote(option_symbol)

            if current_price == 0:
                log.info(f"[{option_symbol}] Price unavailable — waiting")
                time.sleep(30)
                continue

            bars_elapsed += 1

            # ── Hard stop — always active ──
            if current_price <= hard_stop:
                log.info(f"[{option_symbol}] HARD STOP — "
                         f"price={current_price}  stop={hard_stop}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                    sold_positions.add(option_symbol)
                break

            # ── Compute MA from real intraday bars ──
            ma = compute_ma(option_symbol)

            if ma is None:
                log.info(f"[{option_symbol}] No intraday bars — waiting")
                prev_close = current_price
                time.sleep(5)
                continue

            # ── Phase 1: warmup ──
            if bars_elapsed <= WARMUP_BARS:
                log.info(f"[{option_symbol}] [WARMUP {bars_elapsed}/{WARMUP_BARS}]  "
                         f"Price={current_price}  MA={ma}  "
                         f"Display stop={hard_stop}  (MA exit not armed)")
                prev_close = current_price
                prev_ma    = ma
                time.sleep(5)
                continue

            # ── Phase 2: MA exit armed ──
            # Get actual bar low from last intraday bar
            bar_low = get_bar_low(option_symbol)

            # Primary: last bar low touches MA
            if bar_low > 0 and bar_low <= ma:
                fill = round(max(ma, bar_low), 2)
                log.info(f"[{option_symbol}] MA EXIT — LOW TOUCH  "
                         f"price={current_price}  MA={ma}  "
                         f"bar_low={bar_low}  fill={fill}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                    sold_positions.add(option_symbol)
                break

            # Secondary: deliberate MA cross
            if prev_close > prev_ma and current_price < ma:
                fill = round(current_price, 2)
                log.info(f"[{option_symbol}] MA EXIT — CROSS  "
                         f"prev_close={prev_close}  prev_ma={prev_ma}  "
                         f"price={current_price}  MA={ma}  fill={fill}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                    sold_positions.add(option_symbol)
                break

            log.info(f"[{option_symbol}] Price={current_price}  "
                     f"MA={ma}  Hard stop={hard_stop}  "
                     f"Gap to MA={round(current_price - ma, 2)}  "
                     f"Bar {bars_elapsed}")

            prev_close = current_price
            prev_ma    = ma

        except Exception as e:
            log.info(f"[{option_symbol}] Error: {e}")

        time.sleep(5)


# ============================================
# MONITOR POSITIONS
# ============================================
def monitor_positions():
    log.info(f"{'='*50}")
    log.info(f"BOLLINGER MA EXIT — {'PAPER MODE' if PAPER_TRADING else 'LIVE MODE'}")
    log.info(f"API          : {API_BASE_URL}")
    log.info(f"Hard stop    : {HARD_STOP*100}% below entry")
    log.info(f"MA period    : {MA_PERIOD} bars")
    log.info(f"Warmup       : {WARMUP_BARS} bars")
    log.info(f"{'='*50}")

    while True:
        try:
            positions = get_positions()
            if not positions:
                log.info(f"No open positions")

            for p in positions:
                symbol = p["symbol"]

                with positions_lock:
                    if symbol in active_positions or symbol in sold_positions:
                        continue

                cost_basis = p["cost_basis"]
                quantity   = p["quantity"]
                entry      = cost_basis / (100 * quantity)
                underlying = re.match(r'^([A-Z]+)', symbol).group(1)

                current   = get_quote(symbol)
                hard_stop = round(entry * (1 - HARD_STOP), 2)

                if current == 0:
                    log.info(f"[{symbol}] No price available — skipping")
                    continue

                if current <= hard_stop:
                    log.info(f"[{symbol}] Price {current} already at or below "
                             f"hard stop {hard_stop} — skipping")
                    continue

                log.info(f"[{symbol}] New position  "
                         f"entry={entry}  current={current}  "
                         f"hard_stop={hard_stop}")

                with positions_lock:
                    active_positions.add(symbol)

                t = threading.Thread(
                    target=assign_stop,
                    args=(entry, symbol, underlying, quantity),
                    daemon=True
                )
                t.start()
                log.info(f"[{symbol}] Bollinger monitor started")

        except Exception as e:
            log.info(f"Monitor error: {e}")

        time.sleep(5)


monitor_positions()