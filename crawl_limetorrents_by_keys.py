"""
LimeTorrents 多关键词并发抓取 wrapper。

从 data/keys.txt 读每个 key,subprocess 调用 crawl_limetorrents.py 处理,
成功的 key 追加到 data/keys-done.txt(线程锁保护)。
已 done 的 key 自动跳过,失败的 key 不写 done(下次重试可捡起)。

重试策略: 子脚本 crawl_limetorrents.py 内不内置重试,失败页直接 returncode 非 0,
wrapper 用 WORKER_TIMEOUT 兜底(防止子进程失控卡死)。
失败重跑 wrapper 即可从中断页续爬(checkpoint 由子脚本自己落盘到 data/checkpoints/)。

并发模型:
    -c N   ThreadPoolExecutor(N) 调 N 个 worker subprocess,每个 worker
           由 DrissionPage 子脚本内自启独立 headless Chrome(独立
           user-data-dir、独立端口),wrapper 不再管 Chrome 生命周期。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import argparse
import concurrent.futures
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

KEYS_FILE = Path("data/keys.txt")
DONE_FILE = Path("data/keys-done.txt")
SCRIPT = "crawl_limetorrents.py"
# 单 key 子进程的硬性兜底超时(含子脚本内全部重试时间,并非每次重试的独立超时)。
# 子脚本重试时退出码非 0(超时/CF 拦截/解析失败)会被 wrapper 标记为不写 done、
# 断点保留,下次重跑 wrapper 自动从中断页续爬。
WORKER_TIMEOUT = 600

# 重试策略:子脚本内不内置重试,失败直接 returncode 非 0 + 保留 checkpoint,
# 下次再跑 wrapper 通过 load_checkpoint 从中断页续爬。

# 全局并发设置:环境变量优先,默认 1(纯串行,向后兼容)
# 范围 [1, 16];CLI --concurrency 可临时覆盖
CRAWL_LIMETORRENTS_CONCURRENCY = "CRAWL_LIMETORRENTS_CONCURRENCY"
DEFAULT_CONCURRENCY = 1
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 16
_DONE_LOCK = threading.Lock()


def resolve_concurrency() -> int:
    """从环境变量读默认值(若非法回退到 1),CLI --concurrency 会在 argparse 后覆盖。"""
    raw = os.environ.get(CRAWL_LIMETORRENTS_CONCURRENCY)
    if raw is None or raw.strip() == "":
        return DEFAULT_CONCURRENCY
    try:
        v = int(raw)
    except ValueError:
        logger.warning(f"环境变量 {CRAWL_LIMETORRENTS_CONCURRENCY}={raw!r} 不是合法整数,回退默认 {DEFAULT_CONCURRENCY}")
        return DEFAULT_CONCURRENCY
    if not (MIN_CONCURRENCY <= v <= MAX_CONCURRENCY):
        logger.warning(
            f"环境变量 {CRAWL_LIMETORRENTS_CONCURRENCY}={v} 超出范围 [{MIN_CONCURRENCY}, {MAX_CONCURRENCY}],回退默认 {DEFAULT_CONCURRENCY}"
        )
        return DEFAULT_CONCURRENCY
    return v


def load_keys() -> list[str]:
    """读 keys.txt,trim,跳过空行与 # 注释,set 去重保序。"""
    if not KEYS_FILE.exists():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in KEYS_FILE.read_text(encoding="utf-8").splitlines():
        k = raw.strip()
        if not k or k.startswith("#"):
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def load_done() -> set[str]:
    if not DONE_FILE.exists():
        return set()
    return {
        line.strip()
        for line in DONE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def append_done(key: str, lock=None) -> None:
    """线程安全地追加一行到 done.txt 并 flush。"""
    active_lock = lock or _DONE_LOCK
    with active_lock:
        with DONE_FILE.open("a", encoding="utf-8") as f:
            f.write(key + "\n")
            f.flush()


def build_worker_args(key: str, search_category: str) -> list[str]:
    return [
        sys.executable,
        SCRIPT,
        "--keyword",
        key,
        "--search-category",
        search_category,
    ]


def run_one(key: str, search_category: str) -> tuple[str, int, str]:
    """subprocess 跑单个 key。返回 (key, returncode, stderr_tail)。

    重试逻辑不在子脚本内做:子脚本失败直接 returncode 非 0,checkpoint 保留,
    下次 wrapper 重跑时由 load_checkpoint 从中断页续爬。wrapper 这里只负责:
    每个 keyword 启一个 subprocess,用 WORKER_TIMEOUT 做硬性兜底
    (防止子进程失控卡死)。

    stdout 透传到父进程(实时看到子脚本的中文进度),stderr 截留备用(失败时 dump 尾部)。
    """
    args = build_worker_args(key, search_category)
    logger.info(
        f"[开始] {key} pid={os.getpid()} "
        f"(DrissionPage 子脚本内自启 Chrome,失败由 checkpoint 续爬)"
    )
    try:
        # encoding 显式 utf-8:Windows 中文系统默认 GBK 会让中文 logging 崩
        # stdout 不 capture,实时看到子脚本进度;stderr 截留,失败时 dump
        proc = subprocess.run(
            args,
            stdout=None,           # 透传到父进程 stdout
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WORKER_TIMEOUT,
        )
        stderr_tail = "\n".join((proc.stderr or "").splitlines()[-10:])
        return key, proc.returncode, stderr_tail
    except subprocess.TimeoutExpired:
        return key, 124, f"timeout after {WORKER_TIMEOUT}s"
    except Exception as exc:
        return key, 1, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LimeTorrents 多关键词并发抓取 wrapper")
    parser.add_argument("--search-category", default="all")
    parser.add_argument(
        "-c", "--concurrency", type=int, default=resolve_concurrency(), choices=range(MIN_CONCURRENCY, MAX_CONCURRENCY + 1), metavar="N",
        help=(
            f"并发 worker 数(范围 [{MIN_CONCURRENCY}, {MAX_CONCURRENCY}],默认读环境变量"
            f" {CRAWL_LIMETORRENTS_CONCURRENCY}={DEFAULT_CONCURRENCY};每个 worker 由 DrissionPage 子脚本自启独立 Chrome)"
        ),
    )
    args = parser.parse_args(argv)
    concurrency: int = args.concurrency
    env_val = os.environ.get(CRAWL_LIMETORRENTS_CONCURRENCY)
    if env_val and env_val.strip():
        logger.info(f"全局并发设置:环境变量 {CRAWL_LIMETORRENTS_CONCURRENCY}={env_val}(本次实际并发={concurrency})")

    keys = load_keys()
    done = load_done()
    pending = [k for k in keys if k not in done]
    logger.info(
        f"=== 启动批量抓取 === keys 文件={KEYS_FILE} done 文件={DONE_FILE} "
        f"并发数={concurrency} keys.txt 共 {len(keys)} 个 key,已完成 {len(done)} 个,待处理 {len(pending)} 个"
    )
    if not pending:
        logger.info("无新 key 待处理,退出")
        return 0

    failed: list[tuple[str, str]] = []
    started_at = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        worker_started: dict[concurrent.futures.Future, float] = {}
        for key in pending:
            logger.info(f"[入队] {key}")
            fut = pool.submit(run_one, key, args.search_category)
            futures[fut] = key
            worker_started[fut] = time.time()
        for fut in concurrent.futures.as_completed(futures):
            key = futures[fut]
            worker_elapsed = time.time() - worker_started[fut]
            try:
                _, rc, stderr_tail = fut.result()
            except Exception as e:
                failed.append((key, f"future 异常: {type(e).__name__}: {e}"))
                logger.error(f"[失败] {key} 耗时 {worker_elapsed:.1f}s 异常: {e}")
                continue
            if rc == 0:
                append_done(key)
                logger.info(f"[完成] {key} 耗时 {worker_elapsed:.1f}s → 已写入 done.txt")
            else:
                logger.error(
                    f"[失败] {key} 耗时 {worker_elapsed:.1f}s 退出码={rc} "
                    f"(子脚本失败，断点已保留下次可续爬)\n{stderr_tail}"
                )
                failed.append((key, f"退出码={rc}"))

    elapsed = time.time() - started_at
    ok_count = len(pending) - len(failed)
    logger.info("=" * 60)
    logger.info(f"=== 批量抓取完成,总耗时 {elapsed:.1f}s ===")
    logger.info(f"成功数: {ok_count} | 失败数: {len(failed)} | 跳过数(已完成): {len(keys) - len(pending)}")
    if failed:
        logger.info("失败列表(下次重试):")
        for k, reason in failed:
            logger.info(f"  - {k}: {reason}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
