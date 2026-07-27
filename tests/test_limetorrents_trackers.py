"""测试 parse_trackers：trackers 表 → list[dict]（LimeTorrents 详情）。"""
from datetime import datetime

from bs4 import BeautifulSoup

from crawl_detail_limetorrents import parse_trackers
from conftest import fixture


def test_real_tracker_rows():
    soup = BeautifulSoup(
        fixture("limetorrents_detail_st_vincent.html"),
        "html.parser",
    )
    trackers = parse_trackers(soup, datetime(2026, 7, 26, 17, 0, 0))
    assert len(trackers) >= 10
    first = trackers[0]
    assert first["url"].startswith(("udp://", "http://", "https://"))
    assert first["status"] in {"success", "failed"}
    assert isinstance(first["seeders"], int)
    assert isinstance(first["leechers"], int)