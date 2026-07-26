"""run_one 单元：DocumentTooLarge 显式失败且不截断 files；Task 11 related_torrents 入库。"""
import hashlib
from datetime import datetime

from pymongo.errors import DocumentTooLarge

import crawl_detail_limetorrents as detail
from conftest import fixture
from crawl_detail_limetorrents import ParseError, parse_detail


DETAIL_URL = (
    "https://www.limetorrents.fun/"
    "St-Vincent-2014-1080p-PTV-WEB-DL-AAC-2-0-H-264-PiRaTeS-torrent-19859670.html"
)
REF = datetime(2026, 7, 26, 17, 0, 0)


class FakeList:
    """记录 coll_list 上所有 update_one(filter, update, upsert) 调用。"""

    def __init__(self):
        self.failed = None
        self.calls = []  # list[(filter_doc, update_doc, upsert)]

    def update_one(self, filter_doc, update_doc, upsert=False):
        self.calls.append((filter_doc, update_doc, upsert))
        if update_doc.get("$set", {}).get("detail_status") == "failed":
            self.failed = update_doc["$set"]["detail_error"]
        # 模拟 pymongo UpdateResult：upserted_id 在 update 路径里恒为 None；
        # 真实 Mongo 也常返 None（命中已有 _id 时）。upsert_listing 直接读
        # .upserted_id 需要此属性存在。
        return _FakeUpdateResult()


class _FakeUpdateResult:
    """pymongo UpdateResult 仿制品（仅暴露 upserted_id 属性供 upsert_listing 读）。"""

    upserted_id = None


class FakeDetailOK:
    """主详情归档成功：记录 replace_one 次数。"""

    def __init__(self):
        self.replace_count = 0

    def replace_one(self, filter_doc, doc, upsert=False):
        self.replace_count += 1


class OversizeDetail:
    def replace_one(self, *args, **kwargs):
        raise DocumentTooLarge("document exceeds 16MB")


class FakeTab:
    pass


def _related_id(detail_url: str) -> str:
    return hashlib.md5(detail_url.encode("utf-8")).hexdigest()


# ============================================================
# Task 5 既有约束：DocumentTooLarge 显式失败且不截断 files
# ============================================================

def test_document_too_large_is_failed_without_truncation(monkeypatch):
    files = [{"path": str(i)} for i in range(5000)]
    monkeypatch.setattr(detail, "fetch_one", lambda tab, url: "<html></html>")
    monkeypatch.setattr(detail, "save_html_cache", lambda url, html: None)
    monkeypatch.setattr(detail, "parse_detail", lambda html, url: {
        "_id": "abc", "detail_url": url, "files": files,
    })
    coll_list = FakeList()
    result = detail.run_one(
        FakeTab(),
        {"_id": "abc", "detail_url": "https://example/detail"},
        coll_list,
        OversizeDetail(),
    )
    assert result == "failed"
    assert "DocumentTooLarge" in coll_list.failed
    assert len(files) == 5000


# ============================================================
# Task 11：成功路径会把 Related torrents 灌回 bt_info_list
# ============================================================

def test_run_one_success_persists_related_into_list(monkeypatch):
    """成功路径：coll_detail.replace_once + coll_list.update_one(upsert=True) N 次。

    N = parse_detail 从 fixture 里解到的 related_torrents 条目数。
    """
    parsed = parse_detail(
        fixture("limetorrents_detail_st_vincent.html"),
        DETAIL_URL,
        REF,
    )
    related_count = len(parsed["related_torrents"])
    assert related_count >= 5, "fixture 应至少有 5 条 related 用以测试"

    monkeypatch.setattr(detail, "fetch_one", lambda tab, url: "<html></html>")
    monkeypatch.setattr(detail, "save_html_cache", lambda url, html: None)
    monkeypatch.setattr(detail, "parse_detail", lambda html, url: parsed)

    coll_list = FakeList()
    coll_detail = FakeDetailOK()
    result = detail.run_one(
        FakeTab(),
        {"_id": "main-id", "name": parsed["name"], "detail_url": DETAIL_URL},
        coll_list,
        coll_detail,
    )
    assert result == "done"

    # 主详情归档到 coll_detail 一次
    assert coll_detail.replace_count == 1

    # coll_list 上 upsert(True) 次数 == related 条目数
    upsert_calls = [
        (f, u) for (f, u, upsert) in coll_list.calls if upsert
    ]
    assert len(upsert_calls) == related_count, (
        f"期望 {related_count} 次 related upsert，实际 {len(upsert_calls)}"
    )

    # 每个 related upsert 的 _id 必须命中 md5(detail_url)
    expected_ids = {
        _related_id(r["detail_url"]) for r in parsed["related_torrents"]
    }
    actual_ids = {f["_id"] for (f, _) in upsert_calls}
    assert actual_ids == expected_ids, (
        f"upsert 的 _id 集合与 md5(detail_url) 集合不一致: "
        f"缺失 {expected_ids - actual_ids}, 多余 {actual_ids - expected_ids}"
    )

    # mark_done（不带 upsert）至少调用了一次
    mark_done_calls = [
        u for (_, u, upsert) in coll_list.calls
        if not upsert
        and u.get("$set", {}).get("detail_status") == "done"
    ]
    assert mark_done_calls, "mark_done 必须被调用"


def test_run_one_related_upsert_carries_source_and_discovery_mode(monkeypatch):
    """每条 related upsert 都向 $addToSet 写 discovery_modes="related"，$set 含 limetorrents。"""
    parsed = parse_detail(
        fixture("limetorrents_detail_st_vincent.html"),
        DETAIL_URL,
        REF,
    )
    monkeypatch.setattr(detail, "fetch_one", lambda tab, url: "<html></html>")
    monkeypatch.setattr(detail, "save_html_cache", lambda url, html: None)
    monkeypatch.setattr(detail, "parse_detail", lambda html, url: parsed)

    coll_list = FakeList()
    coll_detail = FakeDetailOK()
    detail.run_one(
        FakeTab(),
        {"_id": "main-id", "name": parsed["name"], "detail_url": DETAIL_URL},
        coll_list,
        coll_detail,
    )

    upsert_calls = [
        (f, u) for (f, u, upsert) in coll_list.calls if upsert
    ]
    assert upsert_calls
    for _, update_doc in upsert_calls:
        # $set 应覆盖 limetorrents 源 + detail_url
        assert update_doc["$set"]["source"] == "limetorrents"
        assert "detail_url" in update_doc["$set"]
        # $addToSet 累积 discovery_modes="related"
        assert update_doc["$addToSet"]["discovery_modes"] == "related"


# ============================================================
# Task 11：失败路径不污染 bt_info_list（related 不写、主状态机不乱）
# ============================================================

def test_run_one_parse_error_does_not_upsert_related(monkeypatch):
    """ParseError 失败：解析就炸了，根本没有 parsed → 不应有任何 upsert。"""
    def boom(html, url):
        raise ParseError(f"无法解析 {url}")

    monkeypatch.setattr(detail, "fetch_one", lambda tab, url: "<html></html>")
    monkeypatch.setattr(detail, "save_html_cache", lambda url, html: None)
    monkeypatch.setattr(detail, "parse_detail", boom)

    coll_list = FakeList()
    coll_detail = FakeDetailOK()
    result = detail.run_one(
        FakeTab(),
        {"_id": "main-id", "name": "any", "detail_url": DETAIL_URL},
        coll_list,
        coll_detail,
    )
    assert result == "failed"
    # 没有任何 upsert(True) 调用：related 列表不被污染
    assert all(
        not upsert for (_, _, upsert) in coll_list.calls
    ), "ParseError 路径下不应有 related upsert"
    # 主详情也不会写入 coll_detail
    assert coll_detail.replace_count == 0
    # 状态机走的是 mark_failed
    assert coll_list.failed is not None
    assert "ParseError" in coll_list.failed


def test_run_one_document_too_large_does_not_upsert_related(monkeypatch):
    """DocumentTooLarge 失败：解析成功但 detail 写失败 → 主 done 路径走不到，且 related 不写。"""
    parsed = parse_detail(
        fixture("limetorrents_detail_st_vincent.html"),
        DETAIL_URL,
        REF,
    )
    monkeypatch.setattr(detail, "fetch_one", lambda tab, url: "<html></html>")
    monkeypatch.setattr(detail, "save_html_cache", lambda url, html: None)
    monkeypatch.setattr(detail, "parse_detail", lambda html, url: parsed)

    coll_list = FakeList()
    coll_detail = OversizeDetail()
    result = detail.run_one(
        FakeTab(),
        {"_id": "main-id", "name": parsed["name"], "detail_url": DETAIL_URL},
        coll_list,
        coll_detail,
    )
    assert result == "failed"
    assert "DocumentTooLarge" in coll_list.failed
    # DocumentTooLarge 走 early return，不应再调 _persist_related_listings
    assert all(
        not upsert for (_, _, upsert) in coll_list.calls
    ), "DocumentTooLarge 失败路径下不应有 related upsert"
