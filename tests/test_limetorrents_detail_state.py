"""详情爬虫状态机单元：mark_done / mark_failed / build_pending_query。"""
import crawl_detail_limetorrents as detail


class FakeCollection:
    def __init__(self):
        self.calls = []

    def update_one(self, filter_doc, update_doc):
        self.calls.append((filter_doc, update_doc))


def test_mark_failed_records_document_too_large():
    coll = FakeCollection()
    detail.mark_failed(coll, "abc", "DocumentTooLarge: 18MB")
    filter_doc, update_doc = coll.calls[-1]
    assert filter_doc == {"_id": "abc"}
    assert update_doc["$set"]["detail_status"] == "failed"
    assert "DocumentTooLarge" in update_doc["$set"]["detail_error"]


def test_keyword_query_targets_array_membership():
    assert detail.build_pending_query("St Vincent") == {
        "detail_status": "pending",
        "keywords": "St Vincent",
    }
