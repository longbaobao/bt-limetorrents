"""测试幂等列表 upsert：detail_status 只在 insert 时设置，$set 不覆盖。"""
from crawl_limetorrents import upsert_listing


class FakeResult:
    upserted_id = "new-id"


class FakeCollection:
    def __init__(self):
        self.filter = None
        self.update = None

    def update_one(self, filter_doc, update_doc, upsert=False):
        self.filter = filter_doc
        self.update = update_doc
        assert upsert is True
        return FakeResult()


def test_upsert_uses_set_on_insert_for_status():
    coll = FakeCollection()
    item = {
        "_id": "abc",
        "name": "Torrent",
        "detail_url": "https://example/torrent-1.html",
        "torrent_url": "https://itorrents/torrent/abc.torrent",
        "category": "Movies",
        "added_text": "7 hours ago",
        "added_at": "2026-07-26 10:00:00",
        "size": "2.8 GB",
        "seeders": 3,
        "leechers": 24,
        "observed_at": "2026-07-26 17:00:00",
        "source": "limetorrents",
        "discovery_mode": "search",
        "keyword": "St Vincent",
    }
    assert upsert_listing(coll, item) is True
    assert coll.filter == {"_id": "abc"}
    assert coll.update["$setOnInsert"]["detail_status"] == "pending"
    assert "detail_status" not in coll.update["$set"]
    assert coll.update["$addToSet"] == {
        "keywords": "St Vincent",
        "discovery_modes": "search",
    }


def test_related_upsert_does_not_conflict_keywords():
    """related 路径 keyword 为空，keywords 只能出现在 $setOnInsert，不能进 $set / $addToSet。

    回归 Task 11: MongoDB code 40 "Updating the path 'keywords' would create
    a conflict at 'keywords'" 由同一 update 多 operator 操作 keywords 触发。
    """
    coll = FakeCollection()
    item = {
        "_id": "rel-1",
        "name": "Related",
        "detail_url": "https://example/rel-1.html",
        "torrent_url": "",
        "category": "Movies",
        "added_text": "1 day ago",
        "added_at": "2026-07-26 10:00:00",
        "size": "1.2 GB",
        "seeders": 5,
        "leechers": 1,
        "observed_at": "2026-07-26 17:00:00",
        "source": "limetorrents",
        "discovery_mode": "related",
        "keyword": "",          # 模拟 _persist_related_listings 传空
        "keywords": [],         # 简报字段契约：显式空数组
    }
    assert upsert_listing(coll, item) is True

    update = coll.update
    # keywords 必须在 $setOnInsert 而非 $addToSet
    assert "keywords" not in update["$addToSet"], (
        f"related 路径的 keywords 不应在 $addToSet（避免与 $setOnInsert 冲突），"
        f"实际: {update['$addToSet']}"
    )
    assert update["$setOnInsert"].get("keywords") == []
    # $set 字段集也不应包含 keywords（避免任何可能的 multi-operator 冲突）
    assert "keywords" not in update["$set"], (
        f"$set 不应包含 keywords 字段，实际: {sorted(update['$set'].keys())}"
    )
    # discovery_modes 仍正常累积
    assert update["$addToSet"]["discovery_modes"] == "related"


def test_related_upsert_caller_keywords_is_ignored():
    """caller 显式塞进 item['keywords'] 也会被 upsert_listing 丢弃，避免污染。

    之前 caller 显式 keywords=[] 会被 stored 字段选入 $set，与 $addToSet / $setOnInsert 冲突。
    """
    coll = FakeCollection()
    item = {
        "_id": "rel-2",
        "name": "Related",
        "detail_url": "https://example/rel-2.html",
        "torrent_url": "",
        "category": "Movies",
        "added_text": "",
        "added_at": "",
        "size": "",
        "seeders": 0,
        "leechers": 0,
        "observed_at": "2026-07-26 17:00:00",
        "source": "limetorrents",
        "discovery_mode": "related",
        "keyword": "",
        "keywords": ["foo", "bar"],  # caller 误传也安全
    }
    assert upsert_listing(coll, item) is True
    assert "keywords" not in coll.update["$set"]
    assert "keywords" not in coll.update["$addToSet"]
    assert coll.update["$setOnInsert"]["keywords"] == []

