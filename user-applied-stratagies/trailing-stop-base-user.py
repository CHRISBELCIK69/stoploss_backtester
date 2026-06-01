import re
import os
import requests
import time
import threading
from datetime import datetime

# ============================================
# TIMESTAMP HELPER
# ============================================
def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

# ============================================
# CONFIGURATION
# ============================================
ACCESS_TOKEN  = os.environ.get("ACCESS_TOKEN")
ACCOUNT_ID    = os.environ.get("ACCOUNT_ID")
API_BASE_URL  = os.environ.get("API_BASE_URL")

PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() == "true"

# ============================================
# TUNE THESE IN RAILWAY VARIABLES
# ============================================
INITIAL_STOP_PCT  = float(os.environ.get("INITIAL_STOP_PCT",  "0.25"))   # 25% below entry
PROFIT_TARGET_PCT = float(os.environ.get("PROFIT_TARGET_PCT", "0.25"))   # arm trail at +25%
TRAIL_GAP_PCT     = float(os.environ.get("TRAIL_GAP_PCT",     "0.15"))   # trail sits 15% below price
FLOOR_PCT         = float(os.environ.get("FLOOR_PCT",         "0.10"))   # floor at +10% above entry

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept":        "application/json",
})

active_positions = set()
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


def get_current_price(option_symbol):
    resp = session.get(
        f"{API_BASE_URL}/markets/quotes",
        params={"symbols": option_symbol, "greeks": "false"}
    )
    resp.raise_for_status()
    quote = resp.json().get("quotes", {}).get("quote", {})
    bid  = float(quote.get("bid")  or 0)
    last = float(quote.get("last") or 0)
    ask  = float(quote.get("ask")  or 0)
    return bid or last or ask or 0


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
                "duration":      "day",
            }
        )
        resp.raise_for_status()
        result = resp.json().get("order", {})
        print(f"[{ts()}] SOLD — ID: {result.get('id')}  Status: {result.get('status')}")
    except Exception as e:
        print(f"[{ts()}] [{option_symbol}] Sell order failed: {e}")


# ============================================
# TRAILING STOP STRATEGY
#
# PHASE 1 — INITIAL STOP:
#   Hard stop at entry × (1 - INITIAL_STOP_PCT).
#   Trails the running high by the same gap while below target.
#
# PHASE 2 — TRAILING STOP (armed at PROFIT_TARGET_PCT):
#   Stop = max(current_price × (1 - TRAIL_GAP_PCT), floor_stop)
#   Floor = entry × (1 + FLOOR_PCT) — stop never drops below this.
#   Trail only moves up, never down.
# ============================================
def assign_stop(entry, option_symbol, underlying, quantity):
    initial_stop    = round(entry * (1 - INITIAL_STOP_PCT), 2)
    highest_price   = entry
    trailing_stop   = initial_stop
    profit_unlocked = False
    profit_target   = round(entry * (1 + PROFIT_TARGET_PCT), 2)
    floor_stop      = round(entry * (1 + FLOOR_PCT), 2)

    print(f"[{ts()}] [{option_symbol}] ── TRAILING STOP START ──")
    print(f"[{ts()}] [{option_symbol}] Entry={entry}  "
          f"Initial stop={initial_stop} (-{INITIAL_STOP_PCT*100:.0f}%)  "
          f"Profit target={profit_target} (+{PROFIT_TARGET_PCT*100:.0f}%)  "
          f"Floor={floor_stop} (+{FLOOR_PCT*100:.0f}%)")

    while True:
        try:
            current_price = get_current_price(option_symbol)

            if current_price == 0:
                print(f"[{ts()}] [{option_symbol}] Price unavailable — waiting")
                time.sleep(30)
                continue

            # Track running high
            if current_price > highest_price:
                highest_price = current_price

            # ── Arm the trail at profit target ──
            if not profit_unlocked and current_price >= profit_target:
                profit_unlocked = True
                print(f"[{ts()}] [{option_symbol}] *** TRAIL ARMED ***  "
                      f"price={current_price}  "
                      f"switching to {TRAIL_GAP_PCT*100:.0f}% trail  "
                      f"floor={floor_stop}")

            # ── Update stop ──
            if profit_unlocked:
                # Phase 2: trail below current price with floor
                new_stop = round(current_price * (1 - TRAIL_GAP_PCT), 2)
                new_stop = max(new_stop, floor_stop)
            else:
                # Phase 1: trail below running high by initial gap
                new_stop = round(highest_price * (1 - INITIAL_STOP_PCT), 2)

            if new_stop > trailing_stop:
                old_stop      = trailing_stop
                trailing_stop = new_stop
                print(f"[{ts()}] [{option_symbol}] Stop raised: "
                      f"{old_stop} → {trailing_stop}  "
                      f"price={current_price}  high={highest_price}  "
                      f"mode={'TRAIL' if profit_unlocked else 'INITIAL'}")

            # ── Check stop ──
            if current_price <= trailing_stop:
                print(f"[{ts()}] [{option_symbol}] STOP TRIGGERED — "
                      f"price={current_price}  stop={trailing_stop}  "
                      f"mode={'TRAIL' if profit_unlocked else 'INITIAL'}")
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                break

            # ── Status log ──
            pnl_pct    = round((current_price - entry) / entry * 100, 2)
            gap_to_stop = round(current_price - trailing_stop, 2)
            phase      = "TRAIL" if profit_unlocked else "INITIAL"
            print(f"[{ts()}] [{option_symbol}]  "
                  f"price={current_price}  "
                  f"pnl={'+' if pnl_pct >= 0 else ''}{pnl_pct}%  "
                  f"stop={trailing_stop}  "
                  f"gap={gap_to_stop}  "
                  f"high={highest_price}  "
                  f"mode={phase}"
                  + (f"  floor={floor_stop}" if profit_unlocked else
                     f"  arm_at={profit_target}"))

        except Exception as e:
            print(f"[{ts()}] [{option_symbol}] Error: {e}")

        time.sleep(5)


# ============================================
# POSITION SCANNER
# ============================================
def monitor_positions():
    print(f"\n{'='*55}")
    print(f"TRAILING STOP — {'PAPER MODE' if PAPER_TRADING else 'LIVE MODE'}")
    print(f"Started : {ts()}")
    print(f"API     : {API_BASE_URL}")
    print(f"Settings:")
    print(f"  Initial stop  : -{INITIAL_STOP_PCT*100:.0f}% from entry (phase 1)")
    print(f"  Profit target : +{PROFIT_TARGET_PCT*100:.0f}% to arm trail (phase 2)")
    print(f"  Trail gap     : -{TRAIL_GAP_PCT*100:.0f}% below current price")
    print(f"  Floor         : +{FLOOR_PCT*100:.0f}% above entry (trail never drops below)")
    print(f"{'='*55}\n")

    while True:
        try:
            positions = get_positions()
            if not positions:
                print(f"[{ts()}] No open positions — waiting")

            for p in positions:
                symbol = p["symbol"]

                with positions_lock:
                    if symbol in active_positions:
                        continue

                cost_basis = p["cost_basis"]
                quantity   = p["quantity"]
                entry      = cost_basis / (100 * quantity)
                underlying = re.match(r'^([A-Z]+)', symbol).group(1)

                # Check current price before spinning up a thread
                current    = get_current_price(symbol)
                hard_stop  = round(entry * (1 - INITIAL_STOP_PCT), 2)

                if current == 0:
                    print(f"[{ts()}] [{symbol}] No price available — skipping")
                    continue

                if current <= hard_stop:
                    print(f"[{ts()}] [{symbol}] Already at/below initial stop "
                          f"(price={current}  stop={hard_stop}) — skipping")
                    continue

                print(f"[{ts()}] [{symbol}] NEW POSITION  "
                      f"entry={entry}  price={current}  "
                      f"initial_stop={hard_stop}")

                with positions_lock:
                    active_positions.add(symbol)

                t = threading.Thread(
                    target=assign_stop,
                    args=(entry, symbol, underlying, quantity),
                    daemon=True
                )
                t.start()
                print(f"[{ts()}] [{symbol}] Monitor thread started")

        except Exception as e:
            print(f"[{ts()}] Monitor error: {e}")

        time.sleep(5)


if __name__ == "__main__":
    monitor_positions()