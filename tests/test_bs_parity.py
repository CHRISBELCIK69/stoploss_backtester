# ============================================================
# tests/test_bs_parity.py
#
# Parity check: our strategies/_bs_math.py vs the published
# `blackscholes` library (https://pypi.org/project/blackscholes/).
#
# This is a DEV-ONLY tool. The blackscholes package is NOT in
# requirements.txt — install it ad-hoc before running:
#
#   pip install --user --break-system-packages blackscholes
#
# Then from the backtester/ directory:
#
#   python3 tests/test_bs_parity.py
#
# What it does:
#   - Builds a grid of (S, K, T, r, sigma, type) combinations
#     covering ATM/ITM/OTM × short-dated to long-dated × low/high vol
#   - Computes every greek both ways
#   - Converts the library's annualized conventions to our
#     trader conventions (theta/day, vega/1%, charm/day)
#   - Reports per-greek max absolute + relative error and the
#     worst-case input row, so we know which of our formulas
#     match a published reference and which drift
#
# Exit code: 0 if every greek matches within tolerance, 1 otherwise.
# Useful as a regression guard if you ever touch _bs_math.py.
# ============================================================

import sys
import os
from pathlib import Path

# Make `strategies` importable when running from the backtester/ root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from blackscholes import BlackScholesCall, BlackScholesPut
except ImportError:
    print("ERROR: blackscholes not installed. Install with:")
    print("  pip install --user --break-system-packages blackscholes")
    sys.exit(2)

from strategies._bs_math import bs_price, bs_greeks


# ── Conventions table ────────────────────────────────────────
# Library returns ANNUALIZED greeks; ours returns trader convention.
# Library    /  divisor   =  ours
# ─────────────────────────────────────────
#  delta     /     1      =  delta
#  gamma     /     1      =  gamma
#  vega      /   100      =  vega   (per 1% IV move)
#  theta     /   365      =  theta  (per calendar day, matches our _CAL_DAYS=365.0)
#  charm     /   365      =  charm  (per calendar day)
#  vanna     /     1      =  vanna  (both per 1.0 σ)

CONVERSIONS = {
    'delta': 1.0,
    'gamma': 1.0,
    'vega':  100.0,
    'theta': 365.0,
    'charm': 365.0,
    'vanna': 1.0,
}

# Acceptance tolerances. Generous on theta because the day-count
# constant is the most likely source of small mismatch.
TOL_ABS = {
    'delta': 1e-6,
    'gamma': 1e-6,
    'vega':  1e-4,
    'theta': 1e-4,
    'charm': 1e-4,
    'vanna': 1e-4,
}
TOL_REL = {  # relative used when |library| > 1e-3 to avoid divide-by-tiny
    'delta': 1e-4,
    'gamma': 1e-4,
    'vega':  1e-4,
    'theta': 1e-4,
    'charm': 5e-3,   # charm is the most numerically sensitive
    'vanna': 1e-4,
}


def build_grid():
    """Parameter grid spanning realistic option scenarios."""
    grid = []
    spots   = [50.0, 100.0, 250.0, 500.0]
    moneyness = [0.85, 0.95, 1.00, 1.05, 1.15]    # K/S
    expiries  = [
        1/365,            # 1-day (~0DTE held overnight)
        2/365,            # 2DTE
        7/365,            # weekly
        30/365,           # monthly
        90/365,           # quarterly
        365/365,          # 1y LEAP
    ]
    rates   = [0.0, 0.05]
    sigmas  = [0.10, 0.20, 0.40, 0.80]            # 10% to 80% IV
    types   = ['C', 'P']

    for S in spots:
        for m in moneyness:
            K = round(S * m, 2)
            for T in expiries:
                for r in rates:
                    for sigma in sigmas:
                        for typ in types:
                            grid.append((S, K, T, r, sigma, typ))
    return grid


def lib_greeks(S, K, T, r, sigma, option_type, q=0.0):
    """All greeks via the published library, raw (annualized)."""
    cls = BlackScholesCall if option_type == 'C' else BlackScholesPut
    o   = cls(S=S, K=K, T=T, r=r, sigma=sigma, q=q)
    return {
        'price': o.price(),
        'delta': o.delta(),
        'gamma': o.gamma(),
        'theta': o.theta(),
        'vega':  o.vega(),
        'charm': o.charm(),
        'vanna': o.vanna(),
    }


def compare(theirs, ours, divisor):
    """Returns (abs_diff, rel_diff) where rel is meaningful."""
    theirs_converted = theirs / divisor
    abs_d = abs(ours - theirs_converted)
    if abs(theirs_converted) > 1e-3:
        rel_d = abs_d / abs(theirs_converted)
    else:
        rel_d = 0.0
    return abs_d, rel_d, theirs_converted


def main():
    grid = build_grid()
    print(f"Grid size: {len(grid)} combinations\n")

    # Per-greek worst-case tracking
    worst = {g: {'abs': 0.0, 'rel': 0.0, 'row': None,
                 'ours': None, 'theirs': None} for g in CONVERSIONS}
    failures = {g: 0 for g in CONVERSIONS}
    skipped  = 0

    # Price sanity check too (no convention difference there)
    price_worst = {'abs': 0.0, 'rel': 0.0, 'row': None}

    for row in grid:
        S, K, T, r, sigma, typ = row
        try:
            lib = lib_greeks(S, K, T, r, sigma, typ)
        except Exception as e:
            skipped += 1
            continue

        our_p = bs_price(S, K, T, r, sigma, typ)
        our_g = bs_greeks(S, K, T, r, sigma, typ)

        # Price
        pd = abs(our_p - lib['price'])
        pr = pd / abs(lib['price']) if abs(lib['price']) > 1e-3 else 0.0
        if pd > price_worst['abs']:
            price_worst.update({'abs': pd, 'row': row, 'ours': our_p, 'theirs': lib['price']})
        if pr > price_worst['rel']:
            price_worst['rel'] = pr

        # Each greek
        for g, divisor in CONVERSIONS.items():
            ours = our_g.get(g)
            theirs = lib[g]
            if ours is None:
                continue  # ours bailed on degenerate input — OK
            a, rel, theirs_conv = compare(theirs, ours, divisor)
            if a > worst[g]['abs']:
                worst[g].update({'abs': a, 'row': row, 'ours': ours, 'theirs': theirs_conv})
            if rel > worst[g]['rel']:
                worst[g]['rel'] = rel
            # Failure check
            if a > TOL_ABS[g] and rel > TOL_REL[g]:
                failures[g] += 1

    # ── Report ──────────────────────────────────────────────
    print("PRICE PARITY")
    print(f"  max abs diff: {price_worst['abs']:.6e}")
    print(f"  max rel diff: {price_worst['rel']:.6e}")
    if price_worst['row']:
        S, K, T, r, sigma, typ = price_worst['row']
        print(f"  worst row: S={S} K={K} T={T:.5f} r={r} σ={sigma} {typ}")
        print(f"             ours={price_worst['ours']:.6f}  theirs={price_worst['theirs']:.6f}")
    print()

    print(f"{'GREEK':<8} {'MAX ABS':>14} {'MAX REL':>14} {'FAIL/TOTAL':>14}  WORST INPUT")
    print("─" * 90)
    any_fail = False
    for g in CONVERSIONS:
        w = worst[g]
        f = failures[g]
        if f:
            any_fail = True
        row_str = ''
        if w['row']:
            S, K, T, r, sigma, typ = w['row']
            row_str = f"S={S} K={K} T={T:.5f} r={r} σ={sigma} {typ}  ours={w['ours']:.6f}  theirs={w['theirs']:.6f}"
        marker = '❌' if f else '✓ '
        print(f"{marker} {g:<6} {w['abs']:>14.6e} {w['rel']:>14.6e} {f:>8d}/{len(grid):<5d}  {row_str}")

    print()
    if skipped:
        print(f"Skipped {skipped} rows (library raised on them)")

    if any_fail:
        print("\nFAILURES present. Inspect rows above to decide whether to fix our code.")
        return 1
    print("\nAll greeks within tolerance.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
