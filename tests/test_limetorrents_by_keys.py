import sys

import crawl_limetorrents_by_keys as batch


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
