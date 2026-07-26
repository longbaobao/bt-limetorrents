"""初始化 LimeTorrents MongoDB 集合、状态和索引；可重复执行。"""
import sys

from pymongo import ASCENDING, DESCENDING, MongoClient

from crawl_limetorrents import DB_NAME, MONGO_URI
from crawl_detail_limetorrents import COLL_DETAIL

sys.stdout.reconfigure(encoding="utf-8")

COLL_LIST = "bt_info_list"


def initialize_database(db) -> dict[str, int]:
    """补齐详情状态字段并创建 LimeTorrents 所需索引。"""
    list_coll = db[COLL_LIST]
    detail_coll = db[COLL_DETAIL]
    result = list_coll.update_many(
        {"detail_status": {"$exists": False}},
        {
            "$set": {
                "detail_status": "pending",
                "detail_started_at": None,
                "detail_processed_at": None,
                "detail_error": None,
            }
        },
    )
    list_coll.create_index("detail_url", unique=True)
    list_coll.create_index("detail_status")
    list_coll.create_index("keywords")
    list_coll.create_index([("category", ASCENDING), ("added_at", DESCENDING)])
    detail_coll.create_index("detail_url", unique=True)
    detail_coll.create_index("info_hash")
    return {"status_initialized": result.modified_count}


def main() -> None:
    client = MongoClient(MONGO_URI)
    stats = initialize_database(client[DB_NAME])
    print(f"数据库 {DB_NAME} 初始化完成: {stats}")


if __name__ == "__main__":
    main()
