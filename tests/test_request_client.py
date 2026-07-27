"""Tests for request_client backend abstraction.

These tests do not hit the network. They lock in:
- ``CurlCffiBackend.fetch`` issues a request with impersonate=... and returns
  ``response.text``.
- ``DrissionBackend.fetch`` delegates to ``fetch_with_cf_bypass``.
- ``build_backend`` picks the right backend by name; unknown names raise
  ``ValueError``; ``DrissionBackend`` requires a browser instance.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import request_client


class FakeResponse:
    def __init__(self, text: str = "<html></html>", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, body: str = "<html>ok</html>"):
        self.body = body
        self.calls: list[dict] = []

    def get(self, url, *, impersonate, timeout, **kwargs):
        self.calls.append(
            {"url": url, "impersonate": impersonate, "timeout": timeout, **kwargs}
        )
        return FakeResponse(self.body)


def test_curl_cffi_backend_uses_impersonate_and_returns_text(monkeypatch):
    fake = FakeSession()
    fake_module = type(
        "F",
        (),
        {"requests": type("R", (), {"get": fake.get})()},
    )
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    backend = request_client.CurlCffiBackend(impersonate="chrome124", timeout=15)
    body = backend.fetch("https://www.limetorrents.fun/browse-torrents/Movies/date/1/")
    assert body == "<html>ok</html>"
    assert fake.calls == [
        {
            "url": "https://www.limetorrents.fun/browse-torrents/Movies/date/1/",
            "impersonate": "chrome124",
            "timeout": 15,
        }
    ]


def test_curl_cffi_backend_propagates_5xx(monkeypatch):
    fake = FakeSession()

    def boom(*args, **kwargs):
        return FakeResponse(status_code=503)

    fake.get = boom
    fake_module = type("F", (), {"requests": type("R", (), {"get": fake.get})()})()
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    backend = request_client.CurlCffiBackend()
    with pytest.raises(RuntimeError, match="HTTP 503"):
        backend.fetch("https://example.com/")


def test_build_backend_curl_cffi_does_not_need_browser():
    backend = request_client.build_backend("curl_cffi")
    assert isinstance(backend, request_client.CurlCffiBackend)
    assert backend.name == "curl_cffi"


def test_build_backend_drission_requires_browser():
    with pytest.raises(ValueError, match="DrissionBackend"):
        request_client.build_backend("drission", browser=None)


def test_build_backend_unknown_name_raises():
    with pytest.raises(ValueError, match="未知 --backend"):
        request_client.build_backend("foobar")


def test_drission_backend_delegates_to_fetch_with_cf_bypass(monkeypatch):
    captured: list[tuple[str, str, int]] = []

    def fake_cf(browser, url, selector, max_wait):
        captured.append((url, selector, max_wait))
        return f"<html>{url}</html>"

    monkeypatch.setattr("crawl_limetorrents.fetch_with_cf_bypass", fake_cf)
    sentinel = object()
    backend = request_client.DrissionBackend(sentinel)
    body = backend.fetch("https://example.com/foo")
    assert body == "<html>https://example.com/foo</html>"
    assert captured == [("https://example.com/foo", "css:table.table2", 45)]
