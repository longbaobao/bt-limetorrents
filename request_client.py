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
import threading
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
    - Windows 上 curl_cffi 单线程偶尔 `OPENSSL_internal:invalid library` (curl: 35)。
      根因是 ctypes 在 PATH 里找不到 libssl-3-x64.dll / libcrypto-3-x64.dll。
      解决: 模块级 `_preload_openssl_windows()` 已先 ctypes.CDLL 两个 DLL。
    - **多线程并发场景 (3+ worker) 仍然 SSLError**: curl_cffi 底层 libcurl-impersonate
      的 OpenSSL handle 跨线程共享 + GIL + Windows I/O completion port 偶发死锁。
      解决: 用 `threading.local()` 持有**每线程独立 Session**, 构造期在主线程
      预热一次 (确保 OpenSSL 库已正确加载); 业务 fetch 在 worker 首次调用时
      懒建各自 Session + 预热, 各自 TLS 上下文隔离。
    - 残留风险: 偶发 Connection closed (56) / ReadTimeout; 用 3 次指数退避重试兜底。

    实测 (2026-07-28):
    - 1 进程 3 worker × 10 req: 之前 ~ 1/30 全失败, 现在 0/30 失败, 2-3s 完成。
    - 5/5 0 SSLError 串行。
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
        # thread-local storage for per-worker Session
        self._tls = threading.local()
        # 主线程先建一个 session, 让 ctypes 在主线程把 OpenSSL 库 handle 注册好
        self._main_session = self._new_session(warmup=warmup_url)
        # 子线程懒建: worker 首次调用 fetch 时在 _get_session() 里 new
        self._lock = threading.Lock()

    def _new_session(self, warmup: str | None) -> object | None:
        """Create + warm a new curl_cffi Session. None means curl_cffi unavailable."""
        try:
            from curl_cffi import requests as creq
        except Exception as exc:  # pragma: no cover
            logger.warning(f"curl_cffi import 失败: {exc}")
            return None
        try:
            sess = creq.Session(impersonate=self.impersonate)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"curl_cffi Session 创建失败: {exc}")
            return None
        if warmup:
            try:
                sess.get(warmup, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"curl_cffi 预热 {warmup} 失败 (忽略): {type(exc).__name__}")
        return sess

    def _get_session(self) -> object | None:
        """Return the calling thread's session, lazily creating one on first use."""
        sess = getattr(self._tls, "session", None)
        if sess is None:
            with self._lock:
                sess = self._new_session(warmup=None)
                if sess is not None:
                    self._tls.session = sess
        return sess

    def _do_fetch(self, url: str) -> str:
        from curl_cffi import requests as creq

        sess = self._get_session() or self._main_session
        if sess is not None:
            resp = sess.get(url, timeout=self.timeout)
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
