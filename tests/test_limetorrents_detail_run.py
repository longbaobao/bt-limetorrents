"""run_one 单元：DocumentTooLarge 显式失败且不截断 files。"""
from pymongo.errors import DocumentTooLarge

import crawl_detail_limetorrents as detail


class FakeList:
    def __init__(self):
        self.failed = None

    def update_one(self, filter_doc, update_doc):
        if update_doc.get("$set", {}).get("detail_status") == "failed":
            self.failed = update_doc["$set"]["detail_error"]


class OversizeDetail:
    def replace_one(self, *args, **kwargs):
        raise DocumentTooLarge("document exceeds 16MB")


class FakeTab:
    pass


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