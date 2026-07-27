"""请求客户端抽象层。

- `DrissionBackend`: 现有实现, 复用 crawl_limetorrents.fetch_with_cf_bypass。
  使用真实 Chromium 渲染, 适合 Cloudflare 5秒盾场景; 较重 (每个子脚本独占端口)。
- `CurlCffiBackend`: 基于 `curl_cffi.requests`, 通过 TLS 指纹 (`impersonate="chrome124"`)
  模拟 Chrome 客户端, 通常可直接穿过 Cloudflare 验证; 纯 HTTP, 不启动浏览器,
  适合大量并发与无头 CI 环境。

后端统一暴露 `fetch(url: str) -> str`, 由 CLI `--backend` 选择。
"""
from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class FetchBackend(Protocol):
    name: str

    def fetch(self, url: str) -> str: ...


class CurlCffiBackend:
    """基于 curl_cffi 的纯 HTTP 后端, impersonate Chrome 绕过 Cloudflare。

    实现细节:
    - 真实版 `requests.get(impersonate="chrome124", timeout=30)` 在我们测试中
      第一次拿到 HTTP 200, 但表 table.table2 缺失, 表明需要进一步
      注入 Cookie / 等待 JS challenge / 走 CF clearance 路径。
    - 第二个 curl_cffi 子进程（subprocess）使用 headless Chrome 先拿 cf_clearance,
      再走 curl_cffi 持续抓取, 是常见工程方案。
    - 本骨架保留为接口契约; 真实生产化需要补充 CF clearance 注入与代理轮换。
    """

    name = "curl_cffi"

    def __init__(
        self,
        impersonate: str = "chrome124",
        timeout: int = 30,
    ) -> None:
        self.impersonate = impersonate
        self.timeout = timeout

    def fetch(self, url: str) -> str:
        from curl_cffi import requests

        resp = requests.get(
            url,
            impersonate=self.impersonate,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.text


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

    默认 drission, 因为它已经过真实 Cloudflare 验证。curl_cffi 长线,
    目前只对可绕过 CF 的页面有效。
    """
    choice = (choice or "drission").lower()
    if choice == "drission":
        if browser is None:
            raise ValueError("DrissionBackend 需要一个已经启动的 ChromiumPage 实例")
        return DrissionBackend(browser)
    if choice == "curl_cffi":
        return CurlCffiBackend()
    raise ValueError(f"未知 --backend: {choice}; 可选 drission | curl_cffi")
