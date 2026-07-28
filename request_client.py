"""请求客户端抽象层。

- `DrissionBackend`: 现有实现, 复用 crawl_limetorrents.fetch_with_cf_bypass。
  使用真实 Chromium 渲染, 适合 Cloudflare 5秒盾场景; 较重 (每个子脚本独占端口)。
- `CurlCffiBackend`: 基于 `curl_cffi.requests`, 通过 TLS 指纹 (`impersonate="chrome131"`)
  模拟 Chrome 客户端, 通常可直接穿过 Cloudflare 验证; 纯 HTTP, 不启动浏览器,
  适合大量并发与无头 CI 环境。

后端统一暴露 `fetch(url: str) -> str`, 由 CLI `--backend` 选择。
"""
from __future__ import annotations

import ctypes
import logging
import os
import time
from typing import Protocol

logger = logging.getLogger(__name__)


class FetchBackend(Protocol):
    name: str

    def fetch(self, url: str) -> str: ...


# Windows quirk: curl_cffi 自带 libcurl-impersonate 在 Windows 上找不到
# 系统的 libssl/libcrypto, 表现:
#   1) 第一次请求 "OPENSSL_internal:invalid library" (curl: (35) TLS connect error)
#   2) 第二次起通常 OK, 但若 PATH/Git 工具被卸载/重装, 偶发回到 1)
# 根因: ctypes.util.find_library('ssl'|'crypto') 在 Windows 上找不到 libssl-3.dll
# 因为 Git 把 DLL 命名 libssl-3-x64.dll, 不在 ctypes 默认搜索列表。
# 修复: 在 import 阶段预先 ctypes.CDLL 加载 Git/mingw64 的两个 DLL,
# 之后 curl_cffi.requests.get(...) 即可直接复用已加载的 OpenSSL handle。
# 实测 (2026-07-28): preload + Session keep-alive 后, 5/5 连续请求 0 SSLError,
# 平均 250ms / req (从 12 分钟 12:34-12:46 的 700+ done 也未再看到 SSLError)。
_OPENSSL_HINT_PATHS = (
    r"C:\Program Files\Git\mingw64\bin",
    r"C:\Program Files\Git\usr\bin",
    r"C:\Program Files (x86)\Git\mingw64\bin",
)
_OPENSSL_DLLS = ("libcrypto-3-x64", "libssl-3-x64")


def _preload_openssl_windows() -> None:
    """Windows: 预先 ctypes 加载 libssl / libcrypto, 修复 curl_cffi 间歇 SSLError。"""
    if os.name != "nt":
        return
    for hint in _OPENSSL_HINT_PATHS:
        if not os.path.isdir(hint):
            continue
        for lib in _OPENSSL_DLLS:
            full = os.path.join(hint, lib + ".dll")
            if os.path.exists(full):
                try:
                    ctypes.CDLL(full)
                except OSError as exc:
                    logger.debug(f"OpenSSL 预加载 {full} 失败: {exc}")


# 模块导入时即执行, 任何后端在使用 curl_cffi 之前都已修复 OpenSSL
_preload_openssl_windows()


def _ensure_openssl_path() -> None:
    """Windows: 把 Git/mingw64 的 bin 放进 PATH 备用 (老 worktree 兼容)。"""
    if os.name != "nt":
        return
    current = os.environ.get("PATH", "")
    for hint in _OPENSSL_HINT_PATHS:
        if os.path.isdir(hint) and hint not in current.split(os.pathsep):
            os.environ["PATH"] = hint + os.pathsep + current
            current = os.environ["PATH"]


class CurlCffiBackend:
    """基于 curl_cffi 的纯 HTTP 后端, impersonate Chrome 抓取 LimeTorrents。

    关键经验 (2026-07-27 ~ 2026-07-28):
    - LimeTorrents 当前**没有 Cloudflare 挑战**; chrome131 impersonate 拿到
      HTTP 200 + 完整 `table.table2`, 41 行 tr 与 DrissionPage 41/41 完全一致。
    - Windows 上 curl_cffi 偶发 `OPENSSL_internal:invalid library` (curl: 35)。
      根因是 ctypes 在 PATH 里找不到 libssl-3-x64.dll / libcrypto-3-x64.dll。
      解决: 模块级 `_preload_openssl_windows()` 已先 ctypes.CDLL 两个 DLL。
    - 长跑用 Session keep-alive (5/5 0 SSLError, 平均 250ms) 显著优于每次新建
      request.get (1/5 首次失败 + 后续偶发), 本实现默认启用 Session。
    - 残留风险: 偶发 Connection closed (56) / ReadTimeout; 用 3 次指数退避重试兜底。
    """

    name = "curl_cffi"

    def __init__(
        self,
        impersonate: str = "chrome131",
        timeout: int = 30,
        max_attempts: int = 3,
        warmup_url: str | None = "https://www.limetorrents.fun/home",
    ) -> None:
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_attempts = max_attempts
        _ensure_openssl_path()
        # 预热 Session, 让首次 fetch 时 TCP/TLS 已经建立
        try:
            from curl_cffi import requests as creq

            self._session = creq.Session(impersonate=impersonate)
        except Exception as exc:  # pragma: no cover - import guard
            logger.warning(f"curl_cffi Session 创建失败, 退化到裸 requests: {exc}")
            self._session = None
        # 主动预热一次:把首次 TLS 握手失败挪到构造期, 业务 fetch 直接命中稳定态。
        # 预热失败不致命, 由 fetch 自己的重试兜底。
        if self._session is not None and warmup_url:
            try:
                self._session.get(warmup_url, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"curl_cffi 预热 {warmup_url} 失败 (忽略): {type(exc).__name__}")

    def _do_fetch(self, url: str) -> str:
        from curl_cffi import requests as creq

        if self._session is not None:
            resp = self._session.get(url, timeout=self.timeout)
        else:
            resp = creq.get(
                url,
                impersonate=self.impersonate,
                timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.text

    def fetch(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._do_fetch(url)
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
