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
