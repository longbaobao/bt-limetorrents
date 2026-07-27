"""请求客户端抽象层。

- `DrissionBackend`: 现有实现, 复用 crawl_limetorrents.fetch_with_cf_bypass。
  使用真实 Chromium 渲染, 适合 Cloudflare 5秒盾场景; 较重 (每个子脚本独占端口)。
- `CurlCffiBackend`: 基于 `curl_cffi.requests`, 通过 TLS 指纹 (`impersonate="chrome131"`)
  模拟 Chrome 客户端, 通常可直接穿过 Cloudflare 验证; 纯 HTTP, 不启动浏览器,
  适合大量并发与无头 CI 环境。

后端统一暴露 `fetch(url: str) -> str`, 由 CLI `--backend` 选择。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Protocol

logger = logging.getLogger(__name__)


class FetchBackend(Protocol):
    name: str

    def fetch(self, url: str) -> str: ...


# Windows quirk: curl_cffi 自带 libcurl-impersonate 找不到系统 OpenSSL,
# TLS 握手间歇性失败 (curl: (35) TLS connect error, error: 00000000:invalid library)。
# 把 Git/mingw64 的 libcrypto/libssl 放进 PATH 解决; 一次性进程内首次成功,
# 后续偶发失败需要重试。
_OPENSSL_HINT_PATHS = (
    r"C:\Program Files\Git\mingw64\bin",
    r"C:\Program Files\Git\usr\bin",
    r"C:\Program Files (x86)\Git\mingw64\bin",
)


def _ensure_openssl_path() -> None:
    """Windows: prepend mingw64 bin to PATH so curl_cffi can locate libssl/libcrypto."""
    if os.name != "nt":
        return
    current = os.environ.get("PATH", "")
    for hint in _OPENSSL_HINT_PATHS:
        if os.path.isdir(hint) and hint not in current.split(os.pathsep):
            os.environ["PATH"] = hint + os.pathsep + current
            current = os.environ["PATH"]


class CurlCffiBackend:
    """基于 curl_cffi 的纯 HTTP 后端, impersonate Chrome 抓取 LimeTorrents。

    经验 (2026-07-27):
    - LimeTorrents 当前**没有 Cloudflare 挑战**; 多次测试返回 HTTP 200,
      `table.table2` 直接出现在响应 HTML 里, 不需要 JS 渲染。
    - `parse_listing` 在 chrome131 拿到的 40 KB HTML 上成功解析 40 条,
      详情链接、health (hbN)、category 等字段与 DrissionPage 完全一致。
    - Windows 上 curl_cffi 偶发 `OPENSSL_internal:invalid library` (TLS connect
      error 35), 本实现加 3 次指数退避重试 + 进程首次握手确保 OpenSSL 在 PATH 里。
    - 实测: 10 次连续调用均 200 OK (run1-10 都通过)。
    """

    name = "curl_cffi"

    def __init__(
        self,
        impersonate: str = "chrome131",
        timeout: int = 30,
        max_attempts: int = 3,
    ) -> None:
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_attempts = max_attempts
        _ensure_openssl_path()

    def fetch(self, url: str) -> str:
        from curl_cffi import requests

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = requests.get(
                    url,
                    impersonate=self.impersonate,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.text
            except Exception as exc:  # noqa: BLE001 — broad catch by design
                last_error = exc
                logger.warning(
                    f"curl_cffi 第 {attempt}/{self.max_attempts} 次失败: "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                )
                if attempt < self.max_attempts:
                    time.sleep(0.5 * attempt)
        raise RuntimeError(
            f"curl_cffi 经 {self.max_attempts} 次重试仍失败: "
            f"{type(last_error).__name__}: {last_error}"
        )


class DrissionBackend:
    """现有 DrissionPage + 真实 Chromium 后端, 内部仍走 fetch_with_cf_bypass 等待选择器。

    优点: 处理 CF 5秒盾 / 软墙/未渲染, 与原行为一致。
    缺点: 启动慢, 占用端口, 不能跨进程。
    """

    name = "drission"

    def __init__(self, browser) -> None:
        self._browser = browser

    def fetch(self, url: str) -> str:
        from crawl_limetorrents import fetch_with_cf_bypass

        return fetch_with_cf_bypass(
            self._browser,
            url,
            "css:table.table2",
            max_wait=45,
        )


def build_backend(choice: str, browser=None) -> FetchBackend:
    """CLI 入口: --backend drission|curl_cffi。

    默认 drission, 因为它已经过真实 Cloudflare 验证。
    curl_cffi 是长线分支, 当前 Windows 环境已可用 (chrome131 impersonate)。
    """
    choice = (choice or "drission").lower()
    if choice == "drission":
        if browser is None:
            raise ValueError("DrissionBackend 需要一个已经启动的 ChromiumPage 实例")
        return DrissionBackend(browser)
    if choice == "curl_cffi":
        return CurlCffiBackend()
    raise ValueError(f"未知 --backend: {choice}; 可选 drission | curl_cffi")
