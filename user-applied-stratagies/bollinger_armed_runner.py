"""
bollinger_armed_runner.py
─────────────────────────
Live trading bot — Armed Bollinger MA exit.

Mirror of the backtest strategy `exit_tsm_bollinger_armed.py` adapted for
live Tradier polling. Same exit logic, but uses CLOSED BARS for MA
decisions and LIVE QUOTES only for the hard-stop check.

This separation prevents the "weird price action interaction" where a
flickering live quote would arm/unarm the MA exit or trigger false
crosses inside a single 1-minute bar.

Run as:
    ACCESS_TOKEN=xxx ACCOUNT_ID=xxx python3 bollinger_armed_runner.py

Tunable via env vars (defaults match backtest defaults):
    MA_PERIOD            — bar count for MA (default 20)
    WARMUP_BARS          — bars after entry before MA exit can fire (default 10)
    HARD_STOP            — fraction below entry for hard stop (default 0.25)
    MIN_ARM_PROFIT_PCT   — fraction above entry to arm MA exit (default 0.15)
    PAPER_TRADING        — 'true' to log instead of placing orders (default true)
    POLL_SECONDS         — seconds between poll cycles (default 5)
"""

import re
import os
import requests
import time
import threading
import logging
from datetime import datetime

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
# ARMED BOLLINGER PARAMS — tune in Railway
# ============================================
MA_PERIOD          = int(os.environ.get("MA_PERIOD", "20"))
WARMUP_BARS        = int(os.environ.get("WARMUP_BARS", "10"))
HARD_STOP          = float(os.environ.get("HARD_STOP", "0.25"))
MIN_ARM_PROFIT_PCT = float(os.environ.get("MIN_ARM_PROFIT_PCT", "0.15"))
POLL_SECONDS       = int(os.environ.get("POLL_SECONDS", "5"))

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept":        "application/json",
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
    """
    Returns dict with last/bid/ask plus 'sell_price' for sell-side decisions.
    sell_price is the bid (what we'd actually fill at), with last/ask fallbacks.
    """
    resp = session.get(
        f"{API_BASE_URL}/markets/quotes",
        params={"symbols": option_symbol, "greeks": "false"}
    )
    resp.raise_for_status()
    quote = resp.json().get("quotes", {}).get("quote", {})
    last = float(quote.get("last") or 0)
    bid  = float(quote.get("bid")  or 0)
    ask  = float(quote.get("ask")  or 0)
    sell_price = bid or last or ask or 0
    return {"last": last, "bid": bid, "ask": ask, "sell_price": sell_price}


def get_intraday_bars(option_symbol):
    """Returns 1-min bars for today. Last bar may still be in-progress."""
    resp = session.get(
        f"{API_BASE_URL}/markets/timesales",
        params={
            "symbol":         option_symbol,
            "interval":       "1min",
            "start":          datetime.now().strftime("%Y-%m-%d 09:30"),
            "end":            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "session_filter": "open",
        }
    )
    resp.raise_for_status()
    series = resp.json().get("series", {})
    if not series or series == "null":
        return []
    data = series.get("data", [])
    return data if isinstance(data, list) else [data]


def get_closed_bars(option_symbol):
    """
    Returns 1-min bars EXCLUDING the currently in-progress bar.
    Critical: MA computation must only use bars that have actually closed.
    """
    bars = get_intraday_bars(option_symbol)
    if not bars:
        return []
    # A bar is closed if its timestamp is earlier than the current minute
    now_minute = datetime.now().strftime("%Y-%m-%d %H:%M")
    closed = [b for b in bars if b["time"][:16] < now_minute]
    return closed


def compute_ma(closed_bars, period):
    """SMA from the last `period` closes of CLOSED bars only."""
    if not closed_bars:
        return None
    closes = [float(b["close"]) for b in closed_bars]
    window = closes[-period:] if len(closes) >= period else closes
    if not window:
        return None
    return round(sum(window) / len(window), 4)


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
                "duration":      "day",
            }
        )
        resp.raise_for_status()
        result = resp.json().get("order", {})
        log.info(f"SOLD — ID: {result.get('id')}  Status: {result.get('status')}")
    except Exception as e:
        log.info(f"[{option_symbol}] Sell failed: {e}")


# ============================================
# ARMED BOLLINGER MONITOR — per-position thread
#
# Two independent decision layers:
#   LAYER 1 — Hard stop: every poll, uses live quote (fast)
#   LAYER 2 — MA arm + cross: only on new CLOSED bar (no noise)
# ============================================
def assign_stop(entry, option_symbol, underlying, quantity):
    hard_stop  = round(entry * (1 - HARD_STOP), 2)
    arm_target = round(entry * (1 + MIN_ARM_PROFIT_PCT), 2)

    state = {
        "ma_exit_armed":  False,
        "last_bar_time":  None,
        "prev_close":     entry,
        "prev_ma":        entry,
        "bars_evaluated": 0,
    }

    log.info(f"[{option_symbol}] ── ARMED BOLLINGER START ──")
    log.info(f"[{option_symbol}] Entry={entry}  Hard stop={hard_stop} ({HARD_STOP*100:.0f}%)  "
             f"Arm target={arm_target} (+{MIN_ARM_PROFIT_PCT*100:.0f}%)  "
             f"MA={MA_PERIOD}  Warmup={WARMUP_BARS}")

    while True:
        try:
            # ────────────────────────────────────────────
            # LAYER 1 — Hard stop check on live quote
            # ────────────────────────────────────────────
            quote = get_quote(option_symbol)
            sell_price = quote["sell_price"]

            if sell_price == 0:
                log.info(f"[{option_symbol}] No price available — skipping cycle")
                time.sleep(POLL_SECONDS)
                continue

            if sell_price <= hard_stop:
                log.info(f"[{option_symbol}] HARD STOP — bid {sell_price} <= {hard_stop}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                    sold_positions.add(option_symbol)
                break

            # ────────────────────────────────────────────
            # LAYER 2 — Bar-driven MA arm + cross check
            #   Only runs when a NEW closed bar is available.
            # ────────────────────────────────────────────
            closed_bars = get_closed_bars(option_symbol)
            if not closed_bars:
                log.info(f"[{option_symbol}] No closed bars yet — bid={sell_price}")
                time.sleep(POLL_SECONDS)
                continue

            latest = closed_bars[-1]

            # Already evaluated this bar — wait for the next one
            if latest["time"] == state["last_bar_time"]:
                log.info(f"[{option_symbol}] Bar {latest['time']} already evaluated  "
                         f"bid={sell_price}  armed={state['ma_exit_armed']}  "
                         f"bars_evaluated={state['bars_evaluated']}")
                time.sleep(POLL_SECONDS)
                continue

            # New closed bar — evaluate
            bar_high  = float(latest["high"])
            bar_close = float(latest["close"])
            ma = compute_ma(closed_bars, MA_PERIOD)

            if ma is None:
                state["last_bar_time"] = latest["time"]
                time.sleep(POLL_SECONDS)
                continue

            state["bars_evaluated"] += 1

            # ── Arming check ──
            if not state["ma_exit_armed"] and bar_high >= arm_target:
                state["ma_exit_armed"] = True
                log.info(f"[{option_symbol}] *** MA EXIT ARMED *** "
                         f"bar_high={bar_high} hit arm_target={arm_target}")

            # ── Deliberate cross exit (armed + past warmup + actual cross) ──
            if (state["ma_exit_armed"]
                and state["bars_evaluated"] > WARMUP_BARS
                and state["prev_close"] > state["prev_ma"]
                and bar_close < ma):
                log.info(f"[{option_symbol}] MA CROSS EXIT — "
                         f"prev_close={state['prev_close']} > prev_ma={state['prev_ma']}, "
                         f"bar_close={bar_close} < ma={ma}  fill≈{sell_price}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                    sold_positions.add(option_symbol)
                break

            # ── Log the bar evaluation ──
            phase = "ARMED" if state["ma_exit_armed"] else "WAITING-FOR-ARM"
            log.info(f"[{option_symbol}] Bar {latest['time']}  close={bar_close}  ma={ma}  "
                     f"phase={phase}  bars_evaluated={state['bars_evaluated']}  bid={sell_price}")

            # ── Roll state forward ──
            state["last_bar_time"] = latest["time"]
            state["prev_close"]    = bar_close
            state["prev_ma"]       = ma

        except Exception as e:
            log.info(f"[{option_symbol}] Error in poll loop: {e}")

        time.sleep(POLL_SECONDS)


# ============================================
# MONITOR POSITIONS — main loop
# ============================================
def monitor_positions():
    log.info(f"{'='*60}")
    log.info(f"ARMED BOLLINGER LIVE — {'PAPER MODE' if PAPER_TRADING else 'LIVE MODE'}")
    log.info(f"API           : {API_BASE_URL}")
    log.info(f"Hard stop     : {HARD_STOP*100:.0f}% below entry")
    log.info(f"Arm profit    : +{MIN_ARM_PROFIT_PCT*100:.0f}% above entry")
    log.info(f"MA period     : {MA_PERIOD} bars")
    log.info(f"Warmup        : {WARMUP_BARS} bars")
    log.info(f"Poll interval : {POLL_SECONDS}s")
    log.info(f"{'='*60}")

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

                quote = get_quote(symbol)
                sell_price = quote["sell_price"]
                hard_stop  = round(entry * (1 - HARD_STOP), 2)

                if sell_price == 0:
                    log.info(f"[{symbol}] No price available — skipping new position")
                    continue

                if sell_price <= hard_stop:
                    log.info(f"[{symbol}] Already at/below hard stop {hard_stop} "
                             f"(bid={sell_price}) — skipping")
                    continue

                log.info(f"[{symbol}] NEW POSITION  entry={entry}  bid={sell_price}  "
                         f"hard_stop={hard_stop}")

                with positions_lock:
                    active_positions.add(symbol)

                t = threading.Thread(
                    target=assign_stop,
                    args=(entry, symbol, underlying, quantity),
                    daemon=True
                )
                t.start()
                log.info(f"[{symbol}] Armed Bollinger monitor thread started")

        except Exception as e:
            log.info(f"Monitor error: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    monitor_positions()
