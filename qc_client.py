# ============================================================
# qc_client.py
# QuantConnect Data API client.
#
# Authenticates with QC's REST API using their HMAC-SHA256 scheme:
#   timestamp     = unix seconds, sent as `Timestamp` header
#   hash          = SHA256(token + ':' + timestamp)
#   Authorization = Basic base64(user_id + ':' + hash)
#
# Fetches:
#   - Option minute trade bars (OHLCV) per contract per day
#   - Option minute IV + greeks (delta/gamma/theta/vega) per contract per day
#   - Underlying minute bars (equities)
#
# Returns RAW BYTES of the QC ZIP files; parsing is done by
# data_provider_qc.py. This file's only job is "authenticated network."
#
# Env vars consumed:
#   QC_USER_ID           — your QC numeric user ID (Account → API)
#   QC_API_TOKEN         — your QC API token
#   QC_API_BASE          — defaults to https://www.quantconnect.com
#   QC_DATA_DIR_OPTION   — defaults to "option/usa/minute"
#   QC_DATA_DIR_EQUITY   — defaults to "equity/usa/minute"
#   QC_DATA_DIR_IV       — defaults to "option/usa/iv/minute"
#                          (path may differ — verify on first fetch)
# ============================================================

import os
import hashlib
import base64
import time
import json
import requests
from typing import Optional


class QCAuthError(Exception):
    """Raised when QC rejects credentials or quota is exhausted."""
    pass


class QCFileMissing(Exception):
    """Raised when QC returns 404 / file-not-found for a data path."""
    pass


class QCClient:
    """
    Thin wrapper over QC's Data API. One instance per request is fine
    (auth is computed per-request anyway). Re-use via a module-level
    singleton in data_provider_qc.
    """

    DEFAULT_BASE = 'https://www.quantconnect.com'

    def __init__(self, user_id: Optional[str] = None,
                 api_token: Optional[str] = None,
                 base_url: Optional[str] = None,
                 timeout: int = 30):
        self.user_id   = user_id   or os.environ.get('QC_USER_ID', '').strip()
        self.api_token = api_token or os.environ.get('QC_API_TOKEN', '').strip()
        self.base_url  = (base_url or os.environ.get('QC_API_BASE', self.DEFAULT_BASE)).rstrip('/')
        self.timeout   = timeout

        if not self.user_id or not self.api_token:
            raise QCAuthError(
                'QC_USER_ID and QC_API_TOKEN env vars must be set. '
                'Find them at quantconnect.com → Account → API.'
            )

        # Data folder templates — overridable via env so we don't have to
        # redeploy code if QC restructures their paths.
        self.dir_option = os.environ.get('QC_DATA_DIR_OPTION', 'option/usa/minute')
        self.dir_equity = os.environ.get('QC_DATA_DIR_EQUITY', 'equity/usa/minute')
        self.dir_iv     = os.environ.get('QC_DATA_DIR_IV',     'option/usa/iv/minute')
        self.dir_daily  = os.environ.get('QC_DATA_DIR_OPTION_DAILY', 'option/usa/daily')

    # ─────────────────────────────────────────────────────────────
    # Auth
    # ─────────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict:
        """Build the per-request HMAC auth headers QC expects."""
        ts   = str(int(time.time()))
        # QC's scheme: hash = sha256(api_token + ':' + timestamp)
        digest = hashlib.sha256(f'{self.api_token}:{ts}'.encode()).hexdigest()
        # Authorization: Basic base64(user_id:hash)
        token  = base64.b64encode(f'{self.user_id}:{digest}'.encode()).decode()
        return {
            'Authorization': f'Basic {token}',
            'Timestamp':     ts,
            # JSON Content-Type even on GET — QC's API expects it
            'Content-Type':  'application/json',
        }

    def _post(self, path: str, payload: dict) -> dict:
        url = f'{self.base_url}{path}'
        resp = requests.post(url, headers=self._auth_headers(),
                             data=json.dumps(payload), timeout=self.timeout)
        if resp.status_code == 401 or resp.status_code == 403:
            raise QCAuthError(f'QC rejected auth: {resp.status_code} {resp.text[:200]}')
        if resp.status_code == 404:
            raise QCFileMissing(f'QC 404 for {path}: {resp.text[:200]}')
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {'_raw': resp.text}

    # ─────────────────────────────────────────────────────────────
    # Data file fetch
    # ─────────────────────────────────────────────────────────────

    def fetch_data_link(self, file_path: str) -> str:
        """
        Ask QC for a presigned download URL for a data file path. This
        is the indirection their CLI uses — first hit the API to get a
        signed S3-ish URL, then HTTP-GET the bytes from that URL.

        file_path: a Lean-relative data path, e.g.
          'option/usa/minute/spy/20260520_trade.zip'
        """
        result = self._post('/api/v2/data/links/read',
                            {'filePath': file_path})
        if not result.get('success', True):
            err = result.get('errors') or result.get('message') or 'unknown error'
            raise QCFileMissing(f'QC link denied for {file_path}: {err}')
        link = result.get('link') or result.get('url')
        if not link:
            raise QCFileMissing(f'QC returned no link for {file_path}: {result}')
        return link

    def download_file(self, file_path: str) -> bytes:
        """High-level: resolve link + download bytes."""
        link = self.fetch_data_link(file_path)
        resp = requests.get(link, timeout=self.timeout)
        if resp.status_code == 404:
            raise QCFileMissing(f'QC 404 downloading {file_path}')
        resp.raise_for_status()
        return resp.content

    # ─────────────────────────────────────────────────────────────
    # Path builders — encapsulate Lean's directory layout so callers
    # don't sprinkle path strings everywhere.
    # ─────────────────────────────────────────────────────────────

    def option_trade_path(self, underlying: str, date_yyyymmdd: str) -> str:
        return f'{self.dir_option}/{underlying.lower()}/{date_yyyymmdd}_trade.zip'

    def option_quote_path(self, underlying: str, date_yyyymmdd: str) -> str:
        return f'{self.dir_option}/{underlying.lower()}/{date_yyyymmdd}_quote.zip'

    def option_iv_path(self, underlying: str, date_yyyymmdd: str) -> str:
        return f'{self.dir_iv}/{underlying.lower()}/{date_yyyymmdd}_iv.zip'

    def equity_trade_path(self, ticker: str, date_yyyymmdd: str) -> str:
        return f'{self.dir_equity}/{ticker.lower()}/{date_yyyymmdd}_trade.zip'

    def option_daily_path(self, underlying: str) -> str:
        # Daily files in Lean are typically one ZIP per symbol covering
        # all dates, not per-day. Verify on first fetch.
        return f'{self.dir_daily}/{underlying.lower()}.zip'
