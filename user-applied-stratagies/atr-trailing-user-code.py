# Tradier defined atr trailing stop needs user defined env variable hostable on any virtual machine
# needs to be displayed instead of strat code so the user can run on there own accord

import re
import os
import requests
import time
import threading
from datetime import datetime, timedelta

# ============================================
# TIMESTAMP HELPER
# ============================================
def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

# ============================================
# CONFIGURATION
# ============================================
ACCESS_TOKEN  = os.environ.get("ACCESS_TOKEN", "")
ACCOUNT_ID    = os.environ.get("ACCOUNT_ID", "")
API_BASE_URL  = os.environ.get("API_BASE_URL", "")

PAPER_TRADING     = os.environ.get("PAPER_TRADING", "true").lower() == "true"

# ============================================
# TUNE THESE SYSTEM VARIABLES
# ============================================
INITIAL_STOP_PCT  = float(os.environ.get("INITIAL_STOP_PCT",  "0.25"))
PROFIT_TARGET_PCT = float(os.environ.get("PROFIT_TARGET_PCT", "0.25"))
TRAIL_GAP_PCT     = float(os.environ.get("TRAIL_GAP_PCT",     "0.15"))
FLOOR_PCT         = float(os.environ.get("FLOOR_PCT",         "0.10"))

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json"
})

active_positions = set()
positions_lock   = threading.Lock()


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


def get_option_history(option_symbol, days=14):
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)
    resp = session.get(
        f"{API_BASE_URL}/markets/history",
        params={
            "symbol":   option_symbol,
            "interval": "daily",
            "start":    start_date.strftime("%Y-%m-%d"),
            "end":      end_date.strftime("%Y-%m-%d"),
        }
    )
    resp.raise_for_status()
    history = resp.json().get("history", {})
    if not history or history == "null":
        return []
    days_data = history.get("day", [])
    return days_data if isinstance(days_data, list) else [days_data]


def get_current_price(option_symbol):
    resp = session.get(
        f"{API_BASE_URL}/markets/quotes",
        params={"symbols": option_symbol, "greeks": "false"}
    )
    resp.raise_for_status()
    quote = resp.json().get("quotes", {}).get("quote", {})
    return float(quote.get("last") or quote.get("ask") or quote.get("bid") or 0)


def sell_asset(option_symbol, underlying, quantity):
    if PAPER_TRADING:
        print(f"[{ts()}] [PAPER] SELL skipped — {option_symbol}  qty={quantity}")
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
        print(f"[{ts()}] SOLD — ID: {result.get('id')}  Status: {result.get('status')}")
    except Exception as e:
        print(f"[{ts()}] [{option_symbol}] Sell order failed: {e}")


def assign_stop(GAP, entry, option_symbol, underlying, quantity):
    initial_stop    = round(entry - GAP, 2)
    highest_price   = entry
    trailing_stop   = initial_stop
    profit_unlocked = False
    profit_target   = round(entry * (1 + PROFIT_TARGET_PCT), 2)
    floor_stop      = round(entry * (1 + FLOOR_PCT), 2)

    print(f"[{ts()}] [{option_symbol}] Entry={entry}  Initial stop={initial_stop}  "
          f"Profit target={profit_target}  Floor={floor_stop}  (GAP={GAP})")

    while True:
        try:
            current_price = get_current_price(option_symbol)

            if current_price == 0:
                print(f"[{ts()}] [{option_symbol}] Price unavailable — waiting")
                time.sleep(30)
                continue

            if current_price > highest_price:
                highest_price = current_price

            if not profit_unlocked and current_price >= profit_target:
                profit_unlocked = True
                print(f"[{ts()}] [{option_symbol}] {PROFIT_TARGET_PCT*100}% profit hit — "
                      f"switching to {TRAIL_GAP_PCT*100}% trailing stop  "
                      f"floor={floor_stop}")

            if profit_unlocked:
                new_stop = round(current_price * (1 - TRAIL_GAP_PCT), 2)
                new_stop = max(new_stop, floor_stop)

                if new_stop > trailing_stop:
                    trailing_stop = new_stop
                    print(f"[{ts()}] [{option_symbol}] Stop raised to: {trailing_stop}  "
                          f"(price={current_price}  "
                          f"{TRAIL_GAP_PCT*100}% gap={round(current_price * TRAIL_GAP_PCT, 2)}  "
                          f"floor={floor_stop})")
            else:
                new_stop = round(highest_price - GAP, 2)
                if new_stop > trailing_stop:
                    trailing_stop = new_stop
                    print(f"[{ts()}] [{option_symbol}] Stop raised to: {trailing_stop}  "
                          f"(high={highest_price})")

            if current_price <= trailing_stop:
                print(f"[{ts()}] [{option_symbol}] STOP TRIGGERED — "
                      f"price={current_price}  stop={trailing_stop}  "
                      f"mode={'TRAILING' if profit_unlocked else 'ATR/PCT'}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                break
            else:
                print(f"[{ts()}] [{option_symbol}] Price: {current_price}  |  "
                      f"Stop: {trailing_stop}  |  "
                      f"Gap to stop: {round(current_price - trailing_stop, 2)}  |  "
                      f"Mode: {'TRAILING' if profit_unlocked else 'ATR/PCT'}")

        except Exception as e:
            print(f"[{ts()}] [{option_symbol}] Error: {e}")

        time.sleep(5)


def monitor_positions():
    print(f"\n{'='*50}")
    print(f"ATR SIMPLE — {'PAPER MODE' if PAPER_TRADING else 'LIVE MODE'}")
    print(f"Started : {ts()}")
    print(f"API: {API_BASE_URL}")
    print(f"Settings:")
    print(f"  Initial stop  : {INITIAL_STOP_PCT*100}% below entry")
    print(f"  Profit target : {PROFIT_TARGET_PCT*100}%")
    print(f"  Trail gap     : {TRAIL_GAP_PCT*100}% below current price")
    print(f"  Floor         : {FLOOR_PCT*100}% above entry")
    print(f"{'='*50}\n")

    while True:
        try:
            positions = get_positions()
            if not positions:
                print(f"[{ts()}] No open positions")

            for p in positions:
                symbol = p["symbol"]

                with positions_lock:
                    if symbol in active_positions:
                        continue

                cost_basis = p["cost_basis"]
                quantity   = p["quantity"]
                entry      = cost_basis / (100 * quantity)
                underlying = re.match(r'^([A-Z]+)', symbol).group(1)

                bars = get_option_history(symbol, days=14)
                true_ranges = []
                print(f"\n[{ts()}] [{symbol}] ── ATR History ──")
                for bar in bars:
                    tr = max(
                        bar["high"] - bar["low"],
                        abs(bar["high"] - bar["close"]),
                        abs(bar["close"] - bar["low"])
                    )
                    true_ranges.append(round(tr, 2))
                    print(f"  {bar['date']}  TR={round(tr, 2)}")

                if not true_ranges:
                    print(f"[{ts()}] [{symbol}] No history — skipping")
                    continue

                atr       = round(sum(true_ranges) / len(true_ranges), 2)
                max_gap   = round(entry * INITIAL_STOP_PCT, 2)
                GAP       = min(atr, max_gap)
                stop_loss = round(entry - GAP, 2)

                print(f"[{ts()}] [{symbol}] entry={entry}  ATR={atr}  GAP={GAP}  "
                      f"stop={stop_loss}  "
                      f"profit_target={round(entry * (1 + PROFIT_TARGET_PCT), 2)}  "
                      f"floor={round(entry * (1 + FLOOR_PCT), 2)}")

                current = get_current_price(symbol)
                if current <= stop_loss:
                    print(f"[{ts()}] [{symbol}] Current price {current} already at or below "
                          f"stop {stop_loss} — skipping")
                    continue

                with positions_lock:
                    active_positions.add(symbol)

                t = threading.Thread(
                    target=assign_stop,
                    args=(GAP, entry, symbol, underlying, quantity),
                    daemon=True
                )
                t.start()
                print(f"[{ts()}] [{symbol}] Stop monitor thread started — current price={current}")

        except Exception as e:
            print(f"[{ts()}] Monitor error: {e}")

        time.sleep(5)


monitor_positions()