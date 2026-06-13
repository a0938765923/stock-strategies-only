import pytest
from stock_strategies import cache


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"status": 200, "data": [{"x": 1}]}

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code not in (402, 429):
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def test_rate_limit_retries_then_succeeds(monkeypatch):
    # 前兩次回限流(body status=402)，第三次成功
    seq = [
        _FakeResp(body={"status": 402, "msg": "request limit reached"}),
        _FakeResp(body={"status": 402, "msg": "request limit reached"}),
        _FakeResp(body={"status": 200, "data": [{"x": 1}]}),
    ]
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        i = calls["n"]; calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(cache.requests, "get", fake_get)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)  # 不真睡
    out = cache._rate_limited_get({"dataset": "X"}, timeout=5, max_retries=2)
    assert out["data"] == [{"x": 1}]
    assert calls["n"] == 3


def test_rate_limit_exhausts_raises(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResp(body={"status": 402, "msg": "request limit reached"})

    monkeypatch.setattr(cache.requests, "get", fake_get)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    with pytest.raises(cache.FinMindRateLimitError):
        cache._rate_limited_get({"dataset": "X"}, timeout=5, max_retries=2)
