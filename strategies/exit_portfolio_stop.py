# ============================================================
# strategies/exit_portfolio_stop.py
# Family 6 — Composite / Conditional Stops
# Portfolio-level stop — not per-trade.
#
# HOW IT WORKS:
#   Exits THIS position when total portfolio P&L (across all
#   open positions) crosses a threshold.
#
#   Most stop strategies are per-trade. This one is portfolio-aware:
#   "If my total book is down $X, exit everything — including this
#   position regardless of how it individually looks."
#
#   Portfolio context is injected via params['_cache']['portfolioPnL']:
#     A running float representing current open P&L across all positions.
#     main.py sets this if the multi-position portfolio endpoint is used.
#     When not present, the strategy falls back to per-trade hard stop only.
#
#   TWO PORTFOLIO SIGNALS:
#     1. Max drawdown: portfolioPnL < -maxDrawdownDollars
#        "My book is down more than $X — exit all."
#     2. Profit target: portfolioPnL > profitTargetDollars
#        "My book is up enough — lock it in, exit all."
#
#   FALLBACK:
#     If portfolioPnL is not in the cache (single-contract backtest),
#     the strategy uses standard per-trade hard stop only, ensuring
#     it always provides at least baseline protection.
#
#   NOTE ON BACKTESTING:
#   True portfolio stop backtesting requires running all positions
#   simultaneously and tracking aggregate P&L. The backtester runs
#   strategies per-contract. To approximate: run a multi-contract
#   backtest and manually inject a portfolio_pnl estimate via the
#   _cache before calling this strategy.
# ============================================================

from backtest_engine import should_eod_exit, append_trace

META = {
    'enabled':  True,
    'id':       'portfolio_stop',
    'name':     'Portfolio-level stop',
    'description': 'Exit when total portfolio P&L crosses a threshold. '
                   'Not per-trade — monitors the whole book.',
    'params': [
        {'key': 'maxDrawdownDollars', 'label': 'Max portfolio drawdown ($)',
         'default': 500, 'min': 0, 'max': 100000, 'step': 50,
         'hint': 'Exit when total open P&L drops below -this amount. 0 = disabled.'},
        {'key': 'profitTargetDollars','label': 'Portfolio profit target ($)',
         'default': 0, 'min': 0, 'max': 100000, 'step': 50,
         'hint': 'Exit when total open P&L exceeds this. 0 = disabled.'},
        {'key': 'hardStopPct',        'label': 'Per-trade hard stop fallback (%)',
         'default': 50, 'min': 5, 'max': 100, 'step': 5,
         'hint': 'Used when portfolio P&L data is unavailable (single contract run).'},
        {'key': 'eodTime',            'label': 'EOD exit (CST)',
         'default': '15:45', 'type': 'time'},
    ],
}


def validate(params):
    if params['maxDrawdownDollars'] == 0 and params['profitTargetDollars'] == 0:
        return 'At least one of maxDrawdownDollars or profitTargetDollars must be > 0'
    return None


def execute(bars, entry_idx, entry_price, params):
    max_dd    = params['maxDrawdownDollars']
    pt        = params['profitTargetDollars']
    hs_pct    = params['hardStopPct'] / 100
    hard_stop = entry_price * (1 - hs_pct)
    cache     = params.get('_cache', {})
    qty       = params.get('qty', 1)

    trace  = []
    extras = {}

    for i in range(entry_idx + 1, len(bars)):
        bar       = bars[i]
        bar_open  = float(bar['open'])
        bar_close = float(bar['close'])
        bar_low   = float(bar['low'])
        bar_high  = float(bar['high'])

        # Per-trade P&L for this position this bar
        trade_pnl = (bar_close - entry_price) * 100 * qty

        # Read portfolio P&L from cache — set by multi-position orchestrator
        portfolio_pnl = cache.get('portfolioPnL')

        trace.append({'time': bar['time'], 'stopPrice': hard_stop})
        append_trace(extras, 'Hard stop', bar, hard_stop)

        # Hard stop fallback (always active)
        if bar_open <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_open,
                    'portfolioPnL': portfolio_pnl, 'tradePnL': round(trade_pnl, 2),
                    'stopTrace': trace, 'extraTraces': extras}
        if bar_low <= hard_stop:
            return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': hard_stop,
                    'portfolioPnL': portfolio_pnl, 'tradePnL': round(trade_pnl, 2),
                    'stopTrace': trace, 'extraTraces': extras}

        if portfolio_pnl is not None:
            # Portfolio drawdown stop
            if max_dd > 0 and portfolio_pnl < -max_dd:
                return {'exitBar': bar, 'exitReason': 'hard_stop', 'stopPrice': bar_close,
                        'exitType': 'portfolio_drawdown',
                        'portfolioPnL': round(portfolio_pnl, 2),
                        'tradePnL': round(trade_pnl, 2),
                        'maxDrawdown': -max_dd,
                        'stopTrace': trace, 'extraTraces': extras}

            # Portfolio profit target
            if pt > 0 and portfolio_pnl > pt:
                return {'exitBar': bar, 'exitReason': 'trailing_stop', 'stopPrice': bar_close,
                        'exitType': 'portfolio_target',
                        'portfolioPnL': round(portfolio_pnl, 2),
                        'tradePnL': round(trade_pnl, 2),
                        'profitTarget': pt,
                        'stopTrace': trace, 'extraTraces': extras}

        if should_eod_exit(bar, params):
            return {'exitBar': bar, 'exitReason': 'eod', 'stopPrice': bar_close,
                    'portfolioPnL': portfolio_pnl,
                    'stopTrace': trace, 'extraTraces': extras}

    return {'exitBar': bars[-1], 'exitReason': 'expiry',
            'stopPrice': float(bars[-1]['close']),
            'portfolioPnL': cache.get('portfolioPnL'),
            'stopTrace': trace, 'extraTraces': extras}
