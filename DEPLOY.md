# Deploying to Railway

This Flask backtester is configured for Railway. Data comes from the
QuantConnect Data API; the previous Polygon integration has been
retired.

## One-time setup

### 1. Generate a QuantConnect API token

1. Log into <https://www.quantconnect.com>.
2. Account → API → copy your **User ID** and create an **API Token**.
3. Verify in **Datasets → My Subscriptions** that you have access to:
   - US Equity Options — Minute (or your equivalent options-data tier)
   - US Equity Options — IV / Greeks
   - US Equity — Minute (underlying bars for greek context)

### 2. Create the Railway project

1. Sign up / log in at <https://railway.app>.
2. **New Project → Deploy from GitHub repo** → pick this repo.
3. Railway auto-detects Python via `requirements.txt`, runs Nixpacks,
   then executes `railway.toml`'s `startCommand` (gunicorn).
4. First deploy takes ~60–90 seconds.

### 3. Attach a persistent volume for the data cache

QC's API is rate-limited and charges data points per request. The
backtester caches every downloaded ZIP locally so re-running the same
backtest is free.

1. Project → Service → **Settings → Volumes → Add Volume**.
2. **Mount path:** `/data/cache`
3. **Size:** 10 GB is plenty for typical use; cache evicts oldest-
   accessed when over quota. ~$2.50/mo.

### 4. Set environment variables

In the Variables tab:

| Variable             | Required | Example                  | Notes                                                            |
|----------------------|----------|--------------------------|------------------------------------------------------------------|
| `QC_USER_ID`         | yes      | (your numeric ID)        | From quantconnect.com → Account → API.                           |
| `QC_API_TOKEN`       | yes      | (your token)             | Same screen.                                                     |
| `QC_DATA_CACHE_DIR`  | yes      | `/data/cache`            | Must match the volume mount path above.                          |
| `QC_CACHE_MAX_GB`    | no       | `10`                     | LRU eviction trigger.                                            |
| `QC_API_BASE`        | no       | (defaults to QC's prod)  | Override for staging/testing.                                    |
| `QC_DATA_DIR_OPTION` | no       | `option/usa/minute`      | Lean path template — only override if QC restructures.           |
| `QC_DATA_DIR_IV`     | no       | `option/usa/iv/minute`   | Lean path template for the IV/greeks dataset.                    |
| `QC_DATA_DIR_EQUITY` | no       | `equity/usa/minute`      | Underlying bars.                                                 |
| `EOD_MODE`           | no       | `daily`                  | `daily` or `expiry`. Default `daily`.                            |
| `RISK_FREE_RATE`     | no       | `0.0525`                 | Annual decimal. Only used as fallback when QC IV is missing.     |
| `HISTORICAL_VOL`     | no       | `0.20`                   | Fallback IV when even Newton-Raphson can't solve.                |

Railway sets `PORT` automatically — don't override it.

### 5. Get the public URL

Project → **Settings → Networking → Generate Domain**.

(Optional) Add a custom domain on the same page and update DNS.

## Auto-deploy

Every `git push origin main` triggers a new Railway build.

## Local development

```bash
cd backtester
export QC_USER_ID=...
export QC_API_TOKEN=...
export QC_DATA_CACHE_DIR=$(pwd)/.qc-cache
pip install -r requirements.txt
python main.py          # Flask dev server on :8080
# or:
gunicorn main:app --bind 0.0.0.0:5000 --workers 2
```

## Cost

- **Railway Hobby:** $5/mo flat, includes $5 of usage. Typical usage stays inside.
- **Railway persistent volume:** $0.25/GB/mo × 10 GB = $2.50/mo
- **QuantConnect:** depends on your subscription tier. Options Minute + IV is usually $100–150/mo.
- **GitHub:** free.

## Files involved

| File                | Purpose                                                                       |
|---------------------|-------------------------------------------------------------------------------|
| `Procfile`          | Legacy start command (Railway also reads it).                                 |
| `railway.toml`      | Builder, start command, healthcheck, restart policy.                          |
| `runtime.txt`       | Pins Python version for Nixpacks.                                             |
| `requirements.txt`  | Python dependencies.                                                          |
| `config.py`         | Reads env vars at boot. No secrets in source.                                 |
| `qc_client.py`      | Authenticated REST client for QC's Data API.                                  |
| `cache_qc.py`       | LRU file cache for downloaded ZIPs.                                           |
| `data_provider_qc.py` | QC-backed implementations of `fetch_bars` / `fetch_underlying_bars` / `fetch_daily_bars`. |
| `data_provider.py`  | Thin compatibility shim that re-exports from `data_provider_qc`.              |

## Troubleshooting

| Symptom                              | Fix                                                                                                                  |
|--------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| `QCAuthError` on first backtest      | Wrong `QC_USER_ID` / `QC_API_TOKEN`. Regenerate the token on QC and update Railway Variables.                        |
| `QCFileMissing` for every contract   | Data subscription doesn't cover that dataset. Check **Datasets → My Subscriptions** on QC.                           |
| Slow first backtest, fast second     | Expected. The cache populates on cold runs; subsequent runs over the same dates are local reads.                     |
| Volume not visible at `/data/cache`  | Verify the mount path matches `QC_DATA_CACHE_DIR`. Restart the service after attaching a new volume.                 |
| Backtest results match Polygon       | Expected for liquid contracts. Slight greek differences are normal (QC models American exercise + dividends).        |
| `502 Application failed to respond`  | gunicorn crashed. Check **Deploy logs** for the traceback.                                                           |
