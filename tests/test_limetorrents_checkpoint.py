"""测试 LimeTorrents 列表断点续爬：每个查询独立 checkpoint，原子写。"""
import crawl_limetorrents as crawler


def test_checkpoint_is_query_specific_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, "CHECKPOINT_DIR", tmp_path)
    state = {
        "query_type": "search",
        "category": "all",
        "keyword": "St Vincent",
        "current_page": 1,
        "next_url": "https://www.limetorrents.fun/search/all/St-Vincent//2/",
        "updated_at": "2026-07-26 17:00:00",
    }
    crawler.save_checkpoint(state)
    loaded = crawler.load_checkpoint("search", "all", "St Vincent")
    assert loaded == state
    assert not list(tmp_path.glob("*.tmp"))
    assert crawler.load_checkpoint("browse", "Movies", None) is None


def test_clear_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, "CHECKPOINT_DIR", tmp_path)
    state = {
        "query_type": "browse",
        "category": "Movies",
        "keyword": None,
        "current_page": 2,
        "next_url": None,
        "updated_at": "2026-07-26 17:00:00",
    }
    crawler.save_checkpoint(state)
    crawler.clear_checkpoint("browse", "Movies", None)
    assert crawler.load_checkpoint("browse", "Movies", None) is None
