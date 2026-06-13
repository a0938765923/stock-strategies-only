"""帶 parquet 快取 + 限流退避的 FinMind 取數層。

設計：一檔一 dataset 一 parquet（全歷史，不依日期切檔），讀取時記憶體過濾。
所有新 loader 一律呼叫 fetch_finmind_cached；回測逐日推進時命中快取、不重打 API。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

from .config import (
    FINMIND_CACHE_DIR,
    FINMIND_URL,
    FINMIND_MIN_INTERVAL,
    RATE_LIMIT_BACKOFF_BASE,
    RATE_LIMIT_MAX_RETRIES,
)


class FinMindRateLimitError(RuntimeError):
    """FinMind 限流且退避重試耗盡。由上層 loader 接住回中性結果。"""


def _cache_dir() -> Path:
    # 每次讀 env：測試會用 monkeypatch 改 FINMIND_CACHE_DIR
    import os
    return Path(os.environ.get("FINMIND_CACHE_DIR", FINMIND_CACHE_DIR))


def cache_path(dataset: str, data_id: str) -> Path:
    safe_id = data_id or "_ALL_"
    return _cache_dir() / f"{dataset}__{safe_id}.parquet"


def _meta_path(dataset: str, data_id: str) -> Path:
    return cache_path(dataset, data_id).with_suffix(".meta.json")


def clear_cache(dataset: str | None = None, data_id: str | None = None) -> int:
    """刪除符合條件的快取檔（含 sidecar meta），回傳刪除的 parquet 數。"""
    root = _cache_dir()
    if not root.exists():
        return 0
    if dataset and data_id:
        pattern = f"{dataset}__{data_id}.parquet"
    elif dataset:
        pattern = f"{dataset}__*.parquet"
    else:
        pattern = "*.parquet"
    removed = 0
    for p in root.glob(pattern):
        p.unlink()
        meta = p.with_suffix(".meta.json")
        if meta.exists():
            meta.unlink()
        removed += 1
    return removed


_last_request_monotonic = 0.0


def _throttle() -> None:
    """全域最小間隔節流，避免瞬間爆量。"""
    global _last_request_monotonic
    now = time.monotonic()
    wait = FINMIND_MIN_INTERVAL - (now - _last_request_monotonic)
    if wait > 0:
        time.sleep(wait)
    _last_request_monotonic = time.monotonic()


def _is_rate_limited(resp) -> bool:
    if resp.status_code in (402, 429):
        return True
    try:
        body = resp.json()
    except Exception:
        return False
    return (
        isinstance(body, dict)
        and body.get("status") not in (200, None)
        and "request" in str(body.get("msg", "")).lower()
    )


def _rate_limited_get(params: dict, timeout: int, max_retries: int) -> dict:
    """打 FinMind，處理限流（402/429/body status!=200 含 'request'）：
    指數退避重試，耗盡 raise FinMindRateLimitError。回傳已解析的 json dict。"""
    attempt = 0
    while True:
        _throttle()
        resp = requests.get(FINMIND_URL, params=params, timeout=timeout)
        if _is_rate_limited(resp):
            if attempt >= RATE_LIMIT_MAX_RETRIES:
                raise FinMindRateLimitError(
                    f"FinMind 限流重試 {attempt} 次仍失敗: {params.get('dataset')}"
                )
            backoff = min(RATE_LIMIT_BACKOFF_BASE * (2 ** attempt), 120)
            time.sleep(backoff)
            attempt += 1
            continue
        resp.raise_for_status()
        return resp.json()
