import subprocess
import sys

import pytest

import crawl_limetorrents_by_keys as batch


# -------- 既有契约 ---------------------------------------------------------


def test_env_concurrency_constant():
    """环境变量名常量锁定:值必须是 'CRAWL_LIMETORRENTS_CONCURRENCY'。"""
    assert batch.ENV_CONCURRENCY == "CRAWL_LIMETORRENTS_CONCURRENCY"


def test_script_constant():
    """worker 命令契约:子脚本文件名为 crawl_limetorrents.py。"""
    assert batch.SCRIPT == "crawl_limetorrents.py"


def test_build_worker_args():
    assert batch.build_worker_args("St Vincent", "all") == [
        sys.executable,
        "crawl_limetorrents.py",
        "--keyword",
        "St Vincent",
        "--search-category",
        "all",
    ]


def test_failed_key_is_not_appended(monkeypatch):
    appended = []
    monkeypatch.setattr(batch, "load_keys", lambda: ["ok", "bad"])
    monkeypatch.setattr(batch, "load_done", lambda: set())
    monkeypatch.setattr(
        batch,
        "run_one",
        lambda key, category: (key, 0 if key == "ok" else 2, "failed"),
    )
    monkeypatch.setattr(batch, "append_done", appended.append)
    assert batch.main(["--search-category", "all", "--concurrency", "1"]) == 1
    assert appended == ["ok"]


# -------- run_one 行为 -----------------------------------------------------


def test_run_one_passes_subprocess_kwargs(monkeypatch):
    """run_one 调用 subprocess.run 时必须传 encoding='utf-8' / errors='replace' / timeout=WORKER_TIMEOUT。"""
    captured = {}

    class _FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    batch.run_one("k", "all")
    kw = captured["kwargs"]
    assert kw["encoding"] == "utf-8"
    assert kw["errors"] == "replace"
    assert kw["timeout"] == batch.WORKER_TIMEOUT
    # 同时确认透传到子脚本的命令行
    assert captured["args"] == batch.build_worker_args("k", "all")


def test_run_one_truncates_stderr_to_10_lines(monkeypatch):
    """子进程返回多行 stderr 时,run_one 的 tail 只取最后 10 行。"""
    lines = [f"line-{i}" for i in range(25)]

    class _FakeProc:
        returncode = 2
        stderr = "\n".join(lines)

    monkeypatch.setattr(batch.subprocess, "run", lambda args, **kwargs: _FakeProc())
    _, rc, tail = batch.run_one("k", "all")
    assert rc == 2
    tail_lines = tail.splitlines()
    assert len(tail_lines) == 10
    assert tail_lines == lines[-10:]
    assert "line-0" not in tail
    assert "line-14" not in tail
    assert tail_lines[-1] == "line-24"


def test_run_one_timeout_mapped_to_rc_124(monkeypatch):
    """subprocess.TimeoutExpired -> 返回 (key, 124, timeout ...)。"""
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=batch.WORKER_TIMEOUT)

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    key, rc, tail = batch.run_one("k", "all")
    assert key == "k"
    assert rc == 124
    assert "timeout" in tail.lower()


def test_run_one_wrapper_exception_mapped_to_rc_1(monkeypatch):
    """wrapper 内部异常 -> 返回 (key, 1, "ExceptionClass: msg")。"""
    def fake_run(args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    key, rc, tail = batch.run_one("k", "all")
    assert key == "k"
    assert rc == 1
    assert "RuntimeError" in tail
    assert "boom" in tail


# -------- main 行为:异常隔离 / done 语义 --------------------------------


def test_timeout_does_not_append_done(monkeypatch):
    """subprocess.TimeoutExpired 时,main 不会把 key 写入 done.txt。"""
    appended = []
    monkeypatch.setattr(batch, "load_keys", lambda: ["k1"])
    monkeypatch.setattr(batch, "load_done", lambda: set())
    monkeypatch.setattr(
        batch,
        "run_one",
        lambda key, category: (key, 124, "timeout after 600s"),
    )
    monkeypatch.setattr(batch, "append_done", appended.append)
    assert batch.main(["--search-category", "all", "--concurrency", "1"]) == 1
    assert appended == []


def test_one_worker_exception_other_key_still_completed(monkeypatch):
    """单个 worker 抛 RuntimeError(注:run_one 内部已捕获,这里模拟的是 future.result 抛的异常),
    其他关键词仍能被 append_done。
    """
    appended = []
    keys = ["ok1", "bad", "ok2"]

    def fake_run_one(key, category):
        if key == "bad":
            raise RuntimeError("worker 内部异常")
        return key, 0, ""

    monkeypatch.setattr(batch, "load_keys", lambda: keys)
    monkeypatch.setattr(batch, "load_done", lambda: set())
    monkeypatch.setattr(batch, "run_one", fake_run_one)
    monkeypatch.setattr(batch, "append_done", appended.append)
    rc = batch.main(["--search-category", "all", "--concurrency", "1"])
    assert rc == 1
    assert appended == ["ok1", "ok2"]


# -------- resolve_concurrency 环境变量解析 --------------------------------


def test_resolve_concurrency_valid_integer(monkeypatch):
    monkeypatch.setenv(batch.ENV_CONCURRENCY, "4")
    assert batch.resolve_concurrency() == 4


def test_resolve_concurrency_invalid_string(monkeypatch):
    monkeypatch.setenv(batch.ENV_CONCURRENCY, "abc")
    assert batch.resolve_concurrency() == batch.DEFAULT_CONCURRENCY


def test_resolve_concurrency_out_of_range_low(monkeypatch):
    monkeypatch.setenv(batch.ENV_CONCURRENCY, "0")
    assert batch.resolve_concurrency() == batch.DEFAULT_CONCURRENCY


def test_resolve_concurrency_out_of_range_high(monkeypatch):
    monkeypatch.setenv(batch.ENV_CONCURRENCY, "17")
    assert batch.resolve_concurrency() == batch.DEFAULT_CONCURRENCY


def test_resolve_concurrency_unset(monkeypatch):
    monkeypatch.delenv(batch.ENV_CONCURRENCY, raising=False)
    assert batch.resolve_concurrency() == batch.DEFAULT_CONCURRENCY


# -------- CLI argparse 范围门 --------------------------------------------


def test_cli_concurrency_zero_is_argparse_error(monkeypatch):
    """CLI --concurrency 0 触发 argparse error(超出 choices 范围)。"""
    monkeypatch.setattr(batch, "load_keys", lambda: [])
    monkeypatch.setattr(batch, "load_done", lambda: set())
    monkeypatch.setattr(batch, "append_done", lambda key: None)
    with pytest.raises(SystemExit):
        batch.main(["--concurrency", "0"])


def test_cli_concurrency_17_is_argparse_error(monkeypatch):
    """CLI --concurrency 17 触发 argparse error(超出 choices 范围)。"""
    monkeypatch.setattr(batch, "load_keys", lambda: [])
    monkeypatch.setattr(batch, "load_done", lambda: set())
    monkeypatch.setattr(batch, "append_done", lambda key: None)
    with pytest.raises(SystemExit):
        batch.main(["--concurrency", "17"])
