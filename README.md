# Options contract backtester

Replay exact options contracts against Tradier 1-minute bar history.
Drop in a list of contracts, configure stop loss and EOD exit times,
and get per-trade and cumulative P&L results.

---

## File structure

```
backtester/
├── index.html   — HTML shell: layout, form inputs, results sections
├── style.css    — All visual tokens, layout, dark mode, components
├── api.js       — Tradier API calls, OCC symbol builder, contract parser
├── engine.js    — Pure backtesting logic: entry/exit scanning, P&L calculation
└── ui.js        — DOM rendering, chart, and the main runBacktest() orchestrator
```

### What each file does

**`api.js`** — knows how to talk to Tradier and parse your input
- `buildOCC()` — converts human-readable parts (SPY, 560, C, 2026-01-22) into OCC format (SPY260122C00560000)
- `parseContracts()` — reads the pasted contract list into structured objects
- `fetchTimeSales()` — calls the Tradier `/markets/timesales` endpoint for 1-min bars

**`engine.js`** — pure logic, zero DOM, zero network calls
- `fillPrice()` — extracts the fill price from a bar (uses close price)
- `toMinutes()` — converts "HH:MM" to minutes-since-midnight for fast bar comparison
- `findEntryBar()` — scans bars to find the first bar at/after your signal time
- `walkForwardToExit()` — walks bars forward checking stop loss and EOD conditions
- `calcPnL()` — computes dollar P&L and % return with the ×100 contract multiplier
- `processContract()` — orchestrates the above into a single trade result

**`ui.js`** — wires everything together
- Reads form inputs, calls api.js + engine.js per contract, renders results
- Draws the cumulative P&L chart with Chart.js
- Exposes `window.runBacktest()` which the button in index.html calls

**`style.css`** — no logic, only appearance
- CSS custom properties (design tokens) for colors, spacing, typography
- Full dark mode via `prefers-color-scheme`

---

## How to run

Because `ui.js` uses ES module imports (`import ... from './api.js'`),
the files must be served over HTTP — you cannot open `index.html` directly
from the filesystem (`file://`).

**Option 1 — Python (built-in, no install needed):**
```bash
cd backtester
python3 -m http.server 8080
```
Then open: http://localhost:8080

**Option 2 — Node.js:**
```bash
cd backtester
npx serve .
```

---

## Contract input format

One contract per line, 6 comma-separated fields:

```
SYMBOL, STRIKE, TYPE, EXPIRY, ENTRY_DATE, ENTRY_TIME
```

| Field        | Format       | Example       |
|--------------|--------------|---------------|
| SYMBOL       | Ticker       | SPY           |
| STRIKE       | Number       | 560           |
| TYPE         | C or P       | C             |
| EXPIRY       | YYYY-MM-DD   | 2026-01-22    |
| ENTRY_DATE   | YYYY-MM-DD   | 2026-01-05    |
| ENTRY_TIME   | HH:MM CST    | 09:30         |

Lines starting with `#` are ignored (use for comments).

Example:
```
# Q1 2026 signals
SPY, 560, C, 2026-01-22, 2026-01-05, 09:30
SPY, 555, P, 2026-01-22, 2026-01-10, 10:15
QQQ, 480, C, 2026-02-19, 2026-01-28, 09:30
```

---

## Tradier API notes

- **Sandbox** (`sandbox.tradier.com`) — limited historical depth, good for testing
- **Production** (`api.tradier.com`) — full history, requires a paid Individual account
- The backtester uses `/v1/markets/timesales` with `interval=1min` and `session_filter=open`
- Fill price uses the bar's `close` (last traded price for that minute)
- Stop loss checks the bar's `low` — more realistic than checking close only

---

## Extending the engine

To add a **profit target**, edit `walkForwardToExit()` in `engine.js`:

```js
// Add before the stop loss check:
if (barClose >= profitTargetPrice) {
  return { exitBar: bar, exitReason: 'target' };
}
```

To add **trailing stops**, maintain a `highWaterMark` variable inside the loop
and update `stopPrice` dynamically as the trade moves in your favour.
