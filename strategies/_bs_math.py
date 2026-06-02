# ============================================================
# strategies/_bs_math.py
# Black-Scholes math used by delta-based strategies.
#
# All functions are pure Python — no NumPy dependency. Performance is
# fine for the per-bar IV solve given typical 0DTE bar counts (~400).
#
# Conventions:
#   S       — underlying spot price
#   K       — strike
#   T       — time to expiry in years (calendar)
#   r       — risk-free rate (default 0.05)
#   sigma   — implied volatility
#   option_type — 'C' or 'P'
# ============================================================

import math
from datetime import datetime

SQRT_2PI = math.sqrt(2 * math.pi)


def _normal_cdf(x):
    """Standard normal CDF — accurate enough via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_pdf(x):
    return math.exp(-0.5 * x * x) / SQRT_2PI


def bs_price(S, K, T, r, sigma, option_type):
    """Black-Scholes price for a European option."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if option_type == 'C':
            return max(0.0, S - K)
        return max(0.0, K - S)

    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    discount = math.exp(-r * T)

    if option_type == 'C':
        return S * _normal_cdf(d1) - K * discount * _normal_cdf(d2)
    return K * discount * _normal_cdf(-d2) - S * _normal_cdf(-d1)


def bs_delta(S, K, T, r, sigma, option_type):
    """
    Black-Scholes delta.
      Call:  N(d1) — always in [0, 1]
      Put:   N(d1) - 1 — always in [-1, 0]
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if option_type == 'C':
            return 1.0 if S > K else (0.5 if S == K else 0.0)
        return -1.0 if S < K else (-0.5 if S == K else 0.0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    if option_type == 'C':
        return _normal_cdf(d1)
    return _normal_cdf(d1) - 1.0


# ─────────────────────────────────────────────
# Full greeks (gamma, theta, vega, charm, vanna)
# ─────────────────────────────────────────────
# Conventions (match the way option traders typically read greeks):
#   gamma  — per $1 move in underlying (always positive for long options)
#   theta  — per calendar day  (negative for long, divide annualised by 365)
#   vega   — per 1 percentage-point move in IV (divide annualised by 100)
#   charm  — per calendar day  (dDelta/dT, divide annualised by 365)
#   vanna  — dDelta/dSigma (per 1.0 change in sigma — NOT per 1%)
#
# All return None on degenerate inputs (T <= 0 or sigma <= 0 etc).
# Greeks at/near expiry are not meaningful — strategies should treat
# None as "unavailable" and fall back to hard-stop logic.

_CAL_DAYS = 365.0


def _d1_d2(S, K, T, r, sigma):
    """Helper — returns (d1, d2) or (None, None) on degenerate input."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    try:
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        return d1, d2
    except (ValueError, ZeroDivisionError):
        return None, None


def bs_gamma(S, K, T, r, sigma):
    """Gamma — same for calls and puts. Per $1 underlying move."""
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return None
    return _normal_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_theta(S, K, T, r, sigma, option_type):
    """Theta — per calendar day. Negative for long options."""
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return None
    sqrt_T  = math.sqrt(T)
    pdf_d1  = _normal_pdf(d1)
    disc    = math.exp(-r * T)
    term1   = -(S * pdf_d1 * sigma) / (2.0 * sqrt_T)
    if option_type == 'C':
        annual = term1 - r * K * disc * _normal_cdf(d2)
    else:
        annual = term1 + r * K * disc * _normal_cdf(-d2)
    return annual / _CAL_DAYS


def bs_vega(S, K, T, r, sigma):
    """Vega — per 1 percentage-point IV move. Same for calls and puts."""
    d1, _ = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return None
    return S * math.sqrt(T) * _normal_pdf(d1) / 100.0


def bs_charm(S, K, T, r, sigma, option_type):
    """
    Charm — dDelta/dT, per calendar day.

    NOTE: With dividend yield q = 0 (our current assumption), call-charm
    and put-charm are mathematically IDENTICAL. The dividend-yield term
    ±q·e^(-qT)·N(±d1) is the only thing that splits them, and we don't
    model q. The previous put-branch here added a spurious bond-discount
    adjustment that fails parity against the published `blackscholes`
    library (verified via tests/test_bs_parity.py: 64/64 put failures
    before this fix, 0 after).

    If/when we add dividend yield as a parameter, re-introduce the split
    with the correct ±q·e^(-qT)·N(±d1) terms.
    """
    _ = option_type  # accepted for API symmetry; intentionally unused
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return None
    pdf_d1 = _normal_pdf(d1)
    sqrt_T = math.sqrt(T)
    annual = -pdf_d1 * (2.0 * r * T - d2 * sigma * sqrt_T) / (2.0 * T * sigma * sqrt_T)
    return annual / _CAL_DAYS


def bs_vanna(S, K, T, r, sigma):
    """Vanna — dDelta/dSigma (per 1.0 change in sigma)."""
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return None
    return -_normal_pdf(d1) * d2 / sigma


def bs_greeks(S, K, T, r, sigma, option_type):
    """
    Compute every greek in one shot. Returns a dict:

      { 'delta', 'gamma', 'theta', 'vega', 'charm', 'vanna',
        'iv': sigma,  'T': T }

    All values are None if inputs are degenerate.
    Cheaper than calling each bs_* function separately — d1/d2 are
    computed once and reused.
    """
    out = {
        'delta': None, 'gamma': None, 'theta': None,
        'vega':  None, 'charm': None, 'vanna': None,
        'iv':    sigma,
        'T':     T,
    }

    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return out

    try:
        sqrt_T = math.sqrt(T)
        pdf_d1 = _normal_pdf(d1)
        disc   = math.exp(-r * T)

        # Delta
        if option_type == 'C':
            out['delta'] = _normal_cdf(d1)
        else:
            out['delta'] = _normal_cdf(d1) - 1.0

        # Gamma
        out['gamma'] = pdf_d1 / (S * sigma * sqrt_T)

        # Theta — per calendar day
        term1 = -(S * pdf_d1 * sigma) / (2.0 * sqrt_T)
        if option_type == 'C':
            theta_annual = term1 - r * K * disc * _normal_cdf(d2)
        else:
            theta_annual = term1 + r * K * disc * _normal_cdf(-d2)
        out['theta'] = theta_annual / _CAL_DAYS

        # Vega — per 1% IV move
        out['vega'] = S * sqrt_T * pdf_d1 / 100.0

        # Charm — dDelta/dT per calendar day.
        # Identical for calls and puts with q=0 (see bs_charm() comment).
        charm_annual = -pdf_d1 * (2.0 * r * T - d2 * sigma * sqrt_T) \
                       / (2.0 * T * sigma * sqrt_T)
        out['charm'] = charm_annual / _CAL_DAYS

        # Vanna — dDelta/dSigma
        out['vanna'] = -pdf_d1 * d2 / sigma

    except (ValueError, ZeroDivisionError, OverflowError):
        return {**out, 'delta': None, 'gamma': None, 'theta': None,
                'vega':  None, 'charm': None, 'vanna': None}

    return out


def implied_vol(market_price, S, K, T, r, option_type,
                initial_guess=0.5, max_iter=40, tol=1e-4):
    """
    IV solver: Newton-Raphson with bisection fallback. Returns sigma
    such that BS(sigma) ≈ market_price.

    Newton is tried first because it converges quadratically when it
    works (typically 3–6 iters from a cold start, 1–2 from a warm
    sigma_guess). It silently fails on three classes of input that the
    old version returned a stale guess for:

      1. NUMERICAL BREAKDOWN — log/sqrt domain errors, /0
      2. NEAR-ZERO VEGA       — deep OTM/ITM where the gradient explodes
      3. CLAMP TRAP           — true sigma outside [0.005, 5.0], Newton
                                gets pinned and never escapes
      4. MAX ITER EXHAUSTED   — Newton oscillates without converging

    Any of those routes to bisection, which is bracket-guaranteed
    (BS price is strictly monotonic in sigma) and converges in
    ~log2((10 - 1e-4) / 1e-4) ≈ 17 iters.

    Returns 0.0 on:
      - Degenerate inputs (T ≤ 0, S/K/price ≤ 0)
      - Pre-intrinsic prices (option < intrinsic → no IV is positive)
      - Bisection bracket miss (input outside [1e-4, 10] vol range,
        which is "deep arbitrage territory" or broken data)

    Backward-compatible: public signature unchanged; only the failure
    mode changed from "return whatever sigma was in the loop variable"
    to "fall through to bisection" or "0.0 if even bisection can't
    bracket."
    """
    # ── 1. Pre-screen: degenerate or pre-intrinsic ─────────────
    if T <= 0 or market_price <= 0 or S <= 0 or K <= 0:
        return 0.0

    if option_type == 'C':
        intrinsic = max(0.0, S - K * math.exp(-r * T))
    else:
        intrinsic = max(0.0, K * math.exp(-r * T) - S)
    if market_price <= intrinsic + 1e-6:
        return 0.0

    # ── 2. Newton-Raphson ──────────────────────────────────────
    sigma, ok = _newton_iv(market_price, S, K, T, r, option_type,
                           initial_guess, max_iter=min(max_iter, 20), tol=tol)
    if ok:
        return sigma

    # ── 3. Bisection fallback ──────────────────────────────────
    return _bisect_iv(market_price, S, K, T, r, option_type,
                      lo=1e-4, hi=10.0, max_iter=80, tol=tol)


def _newton_iv(market_price, S, K, T, r, option_type, guess, max_iter, tol):
    """
    Newton step. Returns (sigma, converged_flag). The flag is the only
    honest signal of success — `True` ONLY when the final price diff is
    inside tol. All four failure modes documented in implied_vol() route
    to `False` so the caller knows to fall through to bisection.
    """
    sigma = guess
    clamp_hits = 0
    SIGMA_LOW, SIGMA_HIGH = 0.005, 5.0

    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type)
        diff  = price - market_price
        if abs(diff) < tol:
            return sigma, True                # ← only honest success path

        # FAILURE 1: numerical breakdown computing d1 → bisect
        try:
            d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) \
                 / (sigma * math.sqrt(T))
        except (ValueError, ZeroDivisionError):
            return sigma, False

        # FAILURE 2: near-zero vega — Newton step explodes → bisect
        vega = S * _normal_pdf(d1) * math.sqrt(T)
        if vega < 1e-10:
            return sigma, False

        new_sigma = sigma - diff / vega

        # FAILURE 3: clamp trap — Newton wants to step outside [LOW, HIGH]
        # twice in a row, meaning true sigma is past the clamp. Bisection
        # uses a wider bracket so it can reach it.
        if new_sigma <= SIGMA_LOW:
            new_sigma  = SIGMA_LOW
            clamp_hits += 1
        elif new_sigma >= SIGMA_HIGH:
            new_sigma  = SIGMA_HIGH
            clamp_hits += 1
        else:
            clamp_hits = 0
        if clamp_hits >= 2:
            return sigma, False

        sigma = new_sigma

    # FAILURE 4: max iterations exhausted without abs(diff) < tol
    return sigma, False


def _bisect_iv(market_price, S, K, T, r, option_type,
               lo=1e-4, hi=10.0, max_iter=80, tol=1e-4):
    """
    Bracket-guaranteed IV solver. BS price is strictly monotonic in
    sigma, so if market_price lies inside [BS(lo), BS(hi)] a root
    exists and bisection finds it.

    If market_price is OUTSIDE that price range (deep arbitrage,
    σ < 1e-4 or > 10 — neither plausible for real options), returns
    0.0 as a "we cannot represent this" signal.
    """
    f_lo = bs_price(S, K, T, r, lo, option_type) - market_price
    f_hi = bs_price(S, K, T, r, hi, option_type) - market_price

    # No sign change → market price isn't bracketed → bail.
    if f_lo * f_hi > 0:
        return 0.0

    for _ in range(max_iter):
        mid   = 0.5 * (lo + hi)
        f_mid = bs_price(S, K, T, r, mid, option_type) - market_price
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid

    return 0.5 * (lo + hi)


def years_to_expiry(bar_time_str, expiry_date, expiry_hour=16):
    """
    Compute time-to-expiry in years (calendar basis) from a bar timestamp
    to the standard 16:00 ET expiry.

    bar_time_str — 'YYYY-MM-DD HH:MM'
    expiry_date  — 'YYYY-MM-DD'
    expiry_hour  — defaults to 16 (4 PM ET, standard equity-options expiry)
    """
    try:
        bar_dt    = datetime.strptime(bar_time_str[:16], '%Y-%m-%d %H:%M')
        expiry_dt = datetime.strptime(expiry_date, '%Y-%m-%d').replace(hour=expiry_hour)
        delta_seconds = (expiry_dt - bar_dt).total_seconds()
        if delta_seconds <= 0:
            return 0.0
        return delta_seconds / (365.25 * 24 * 3600)
    except (ValueError, TypeError):
        return 0.0


def build_underlying_index(underlying_bars):
    """
    Build a {time: close_price} dict for O(1) lookup of spot price
    at a given option-bar timestamp.
    """
    if not underlying_bars:
        return {}
    return {b['time'][:16]: float(b['close']) for b in underlying_bars}


def spot_at(underlying_index, bar_time_str):
    """
    Look up spot price at a given timestamp. Falls back to the nearest
    earlier timestamp if exact match missing (which happens at minute
    boundaries occasionally).
    """
    key = bar_time_str[:16]
    if key in underlying_index:
        return underlying_index[key]
    # Walk back up to 5 minutes
    try:
        dt = datetime.strptime(key, '%Y-%m-%d %H:%M')
        for back in range(1, 6):
            from datetime import timedelta
            k2 = (dt - timedelta(minutes=back)).strftime('%Y-%m-%d %H:%M')
            if k2 in underlying_index:
                return underlying_index[k2]
    except ValueError:
        pass
    return None


# ─────────────────────────────────────────────
# Bar enrichment — write greeks into every bar
# ─────────────────────────────────────────────

def enrich_bars_with_greeks(bars, contract, cfg, underlying_bars,
                            solve_iv=True):
    """
    Mutate `bars` in place so each bar gets bar['greeks'] = {
      delta, gamma, theta, vega, charm, vanna, iv, T, dte, S_used
    }.

    If `underlying_bars` is None/empty (no spot data), every bar gets
    bar['greeks'] = None — strategies are expected to detect that and
    fall back to hard-stop-only behavior (mirrors the pattern in
    exit_delta_threshold.py).

    Params:
      bars             — list of bar dicts from fetch_bars()
      contract         — contract dict with strike, type, expiry, symbol
      cfg              — CONFIG (uses defaults.riskFreeRate, defaults.historicalVol)
      underlying_bars  — list of underlying bars from fetch_underlying_bars()
      solve_iv         — if True, back-solve IV per bar; if False, use
                         historicalVol from config.
    """
    if not bars:
        return

    K           = float(contract.get('strike', 0))
    option_type = contract.get('type', 'C').upper()
    expiry_date = contract.get('expiry', '')

    defaults = cfg.get('defaults', {}) if isinstance(cfg, dict) else {}
    r        = float(defaults.get('riskFreeRate', 0.0525))
    hist_vol = float(defaults.get('historicalVol', 0.35))

    if K <= 0 or not expiry_date or not underlying_bars:
        for bar in bars:
            bar['greeks'] = None
        return

    spot_idx = build_underlying_index(underlying_bars)
    if not spot_idx:
        for bar in bars:
            bar['greeks'] = None
        return

    # Warm-start sigma between bars — Newton converges much faster
    # from a previous-bar guess than from a cold 0.5.
    sigma_guess = 0.5
    try:
        expiry_dt = datetime.strptime(expiry_date, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        expiry_dt = None

    prev_greeks   = None    # Last computed greek dict (carry-forward for synthetic bars)
    prev_real_idx = -1      # Index of last REAL bar seen, for spot/T comparison

    for i, bar in enumerate(bars):
        bar_time = bar['time']

        # Synthetic bars (carry-forward minutes added by data_provider.
        # _pad_minute_gaps for Polygon gaps) have close==prev close and
        # zero volume. Re-solving IV on them yields the same sigma as
        # the prior bar, just with a slightly different T. We copy the
        # prior bar's greeks instead — saves Newton iterations across
        # large stretches of illiquid contracts (~30% of bars on some
        # contracts).
        if bar.get('synthetic') and prev_greeks is not None:
            # Copy the dict so downstream mutations don't bleed across
            # bars sharing the same reference.
            bar['greeks'] = dict(prev_greeks)
            continue

        S = spot_at(spot_idx, bar_time)
        if S is None or S <= 0:
            # No spot at this timestamp — leave greeks empty so strategies
            # treat this bar as "unavailable". Do NOT substitute K for S.
            bar['greeks'] = None
            continue

        T = years_to_expiry(bar_time, expiry_date)

        # Implied vol — solve from bar close, warm-started.
        bar_close = float(bar.get('close') or 0)
        if solve_iv and bar_close > 0 and T > 0:
            sigma = implied_vol(bar_close, S, K, T, r, option_type,
                                initial_guess=sigma_guess)
            if sigma and sigma > 0:
                sigma_guess = sigma
            else:
                sigma = hist_vol
        else:
            sigma = hist_vol

        greeks = bs_greeks(S, K, T, r, sigma, option_type)

        # DTE (calendar days remaining)
        dte = None
        if expiry_dt is not None:
            try:
                bar_date = datetime.strptime(bar_time[:10], '%Y-%m-%d').date()
                dte = max(0, (expiry_dt - bar_date).days)
            except (ValueError, TypeError):
                dte = None

        greeks['dte']    = dte
        greeks['S_used'] = round(S, 4)
        bar['greeks']    = greeks
        prev_greeks      = greeks
        prev_real_idx    = i


def get_greek(bar, greek_name, fallback=None):
    """
    Safe reader for bar['greeks'][greek_name]. Returns `fallback` if
    greeks are unavailable (spot was missing, T was 0, BS diverged, etc).

    Usage in a strategy:
        from strategies._bs_math import get_greek
        gamma = get_greek(bar, 'gamma')
        if gamma is None:
            continue                 # fall through to hard stop
        if gamma > 0.5:
            ...
    """
    greeks = bar.get('greeks') if isinstance(bar, dict) else None
    if not greeks:
        return fallback
    val = greeks.get(greek_name)
    return fallback if val is None else val
