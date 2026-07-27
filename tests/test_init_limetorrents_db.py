from init_limetorrents_db import initialize_database


class Result:
    modified_count = 2


class FakeCollection:
    def __init__(self):
        self.indexes = []
        self.update = None

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def update_many(self, filter_doc, update_doc):
        self.update = (filter_doc, update_doc)
        return Result()


class FakeDB:
    def __init__(self):
        self.collections = {
            "bt_info_list": FakeCollection(),
            "bt_info_detail": FakeCollection(),
        }

    def __getitem__(self, name):
        return self.collections[name]


def test_initialize_database_uses_only_two_collections():
    db = FakeDB()
    stats = initialize_database(db)
    assert set(db.collections) == {"bt_info_list", "bt_info_detail"}
    assert stats == {"status_initialized": 2}
    list_indexes = db["bt_info_list"].indexes
    detail_indexes = db["bt_info_detail"].indexes
    assert any(keys == "detail_url" and opts.get("unique") for keys, opts in list_indexes)
    assert any(keys == "detail_url" and opts.get("unique") for keys, opts in detail_indexes)
    assert any(keys == "info_hash" and not opts.get("unique", False) for keys, opts in detail_indexes)


def test_initialize_database_sets_pending_detail_fields():
    db = FakeDB()
    initialize_database(db)
    filter_doc, update_doc = db["bt_info_list"].update
    assert filter_doc == {"detail_status": {"$exists": False}}
    assert update_doc == {
        "$set": {
            "detail_status": "pending",
            "detail_started_at": None,
            "detail_processed_at": None,
            "detail_error": None,
        }
    }


def test_initialize_database_defines_all_required_indexes():
    db = FakeDB()
    initialize_database(db)
    assert db["bt_info_list"].indexes == [
        ("detail_url", {"unique": True}),
        ("detail_status", {}),
        ("keywords", {}),
        ([
            ("category", 1),
            ("added_at", -1),
        ], {}),
    ]
    assert db["bt_info_detail"].indexes == [
        ("detail_url", {"unique": True}),
        ("info_hash", {}),
    ]
