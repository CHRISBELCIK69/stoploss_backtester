# ============================================================
# cache_qc.py
# Tiny file-based LRU cache for QC data downloads.
#
# Why: QC's API has rate limits + costs data points per request. Most
# backtests re-fetch the same OCCs and dates as the user iterates on
# strategy params. Caching the raw ZIP files on the Railway persistent
# volume turns the second-and-onward requests into local disk reads.
#
# Design:
#   - Key is the QC file_path (e.g. 'option/usa/minute/spy/20260520_trade.zip')
#   - Values are raw bytes
#   - Stored as files mirroring the key path under CACHE_DIR
#   - LRU eviction by file mtime when total size > MAX_BYTES
#   - mtime is bumped on read (touch) so freshly-accessed items survive
#
# Env vars:
#   QC_DATA_CACHE_DIR — where to write cache files (default /tmp/qc_cache)
#                       On Railway, mount a persistent volume here so the
#                       cache survives redeploys.
#   QC_CACHE_MAX_GB   — max total cache size in GB (default 10)
# ============================================================

import os
import time
import threading
from pathlib import Path
from typing import Optional


class QCFileCache:
    """Thread-safe file-backed LRU cache."""

    def __init__(self, cache_dir: Optional[str] = None,
                 max_bytes: Optional[int] = None):
        self.cache_dir = Path(
            cache_dir or os.environ.get('QC_DATA_CACHE_DIR', '/tmp/qc_cache')
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if max_bytes is None:
            gb = float(os.environ.get('QC_CACHE_MAX_GB', '10'))
            max_bytes = int(gb * 1024 * 1024 * 1024)
        self.max_bytes = max_bytes
        self._evict_lock = threading.Lock()

    def _path_for(self, key: str) -> Path:
        # Sanitize: no upward traversal, no absolute paths
        clean = key.lstrip('/').replace('..', '_')
        return self.cache_dir / clean

    def get(self, key: str) -> Optional[bytes]:
        path = self._path_for(key)
        if not path.is_file():
            return None
        try:
            # Touch mtime so this entry sorts as "recently used"
            os.utime(path, None)
            return path.read_bytes()
        except OSError:
            return None

    def put(self, key: str, data: bytes) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp + rename, so a crashed write doesn't leave
        # half-files that future reads would treat as valid.
        tmp = path.with_suffix(path.suffix + f'.tmp.{os.getpid()}.{int(time.time()*1000)}')
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        finally:
            if tmp.exists():
                try: tmp.unlink()
                except OSError: pass
        # Evict opportunistically — single thread at a time so we don't
        # double-delete the same file under concurrent puts.
        if self._evict_lock.acquire(blocking=False):
            try:
                self._evict_if_over_quota()
            finally:
                self._evict_lock.release()

    def _evict_if_over_quota(self) -> None:
        total = 0
        files = []
        for p in self.cache_dir.rglob('*'):
            if p.is_file():
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size

        if total <= self.max_bytes:
            return

        # Sort oldest-first and delete until under quota
        files.sort()
        for mtime, size, p in files:
            if total <= self.max_bytes:
                break
            try:
                p.unlink()
                total -= size
            except OSError:
                pass


# Module-level singleton (cheap to construct, but a singleton avoids
# walking the dir on every cache check)
_singleton: Optional[QCFileCache] = None


def get_cache() -> QCFileCache:
    global _singleton
    if _singleton is None:
        _singleton = QCFileCache()
    return _singleton
