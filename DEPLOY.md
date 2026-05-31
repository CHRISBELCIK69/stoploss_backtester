# Deploying to Railway

This Flask backtester is configured for Railway. Push to `main`, get a public URL.

## One-time setup

### 1. Rotate the previously-committed Polygon key

The repo's git history contains a hardcoded Polygon API key from
`config.py`. Treat it as compromised:

1. Log into Polygon → Dashboard → Keys.
2. Revoke the old key (`M4AWhflTA70bQJaj7p_uR4juaJ_Z6WO0` and any clone).
3. Generate a new key. Keep it private — only paste it into Railway's
   Variables UI in step 3 below.

### 2. Create the Railway project

1. Sign up / log in at <https://railway.app>.
2. **New Project → Deploy from GitHub repo** → pick
   `CHRISBELCIK69/stoploss_backtester`.
3. Railway auto-detects Python via `requirements.txt`, runs Nixpacks,
   then executes `railway.toml`'s `startCommand` (gunicorn).
4. First deploy takes ~60–90 seconds.

### 3. Set environment variables

In the Railway project dashboard → **Variables**:

| Variable           | Required | Example       | Notes                                |
|--------------------|----------|---------------|--------------------------------------|
| `POLYGON_API_KEY`  | yes      | (your key)    | Paste here only — never in code.     |
| `EOD_MODE`         | no       | `daily`       | `daily` or `expiry`. Default `daily`.|
| `RISK_FREE_RATE`   | no       | `0.0525`      | Annual decimal. Default 5.25%.       |
| `HISTORICAL_VOL`   | no       | `0.20`        | Fallback IV. Default 20%.            |

Railway sets `PORT` automatically — don't override it.

### 4. Get the public URL

Project → **Settings → Networking → Generate Domain**. You'll get
something like `stoploss-backtester-production.up.railway.app`.

(Optional) Add a custom domain on the same page and update DNS.

## Auto-deploy

Every `git push origin main` triggers a new Railway build. To deploy
a different branch, change it under **Settings → Service → Source**.

## Health & logs

- **Health check:** Railway pings `/api/strategies` after each deploy
  (configured in `railway.toml`).
- **Logs:** Railway dashboard → Deployments → click any deployment →
  **Build logs** and **Deploy logs** tabs.
- **Metrics:** CPU / RAM / network graphs on the service overview.

## Cost

- **Railway Hobby:** $5/mo flat, includes $5 of usage. Typical usage for
  a single low-traffic Flask service stays inside that credit.
- **Polygon:** separate, depends on your data tier.
- **GitHub:** free.

See the in-chat cost breakdown for line items.

## Local development

```bash
cd backtester
export POLYGON_API_KEY=your_key_here
pip install -r requirements.txt
python main.py          # Flask dev server on :5000
# or, to mirror production:
gunicorn main:app --bind 0.0.0.0:5000 --workers 2
```

## Files involved

| File              | Purpose                                                |
|-------------------|--------------------------------------------------------|
| `Procfile`        | Legacy start command (also read by Railway).           |
| `railway.toml`    | Railway-specific build, start, healthcheck, replicas.  |
| `runtime.txt`     | Pins Python version for Nixpacks.                      |
| `requirements.txt`| Python dependencies.                                   |
| `config.py`       | Reads env vars at boot. No secrets in source anymore.  |

## Troubleshooting

| Symptom                              | Fix                                                  |
|--------------------------------------|------------------------------------------------------|
| Deploy hangs on "Building"           | Check the build log; usually a `pip install` failure.|
| App boots but `/api/backtest` 500s   | Likely missing `POLYGON_API_KEY`. Set it in Variables.|
| Healthcheck fails after deploy       | Strategy import error — check **Deploy logs**.       |
| `502 Application failed to respond`  | gunicorn crashed. Logs will show traceback.          |
| Backtests time out                   | Raise `--timeout` in `railway.toml`'s startCommand.  |
