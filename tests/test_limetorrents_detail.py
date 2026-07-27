"""测试 parse_detail：HTML → 结构化字典（LimeTorrents 详情）。"""
from datetime import datetime

import pytest

from crawl_detail_limetorrents import ParseError, parse_detail
from conftest import fixture

DETAIL_URL = (
    "https://www.limetorrents.fun/"
    "St-Vincent-2014-1080p-PTV-WEB-DL-AAC-2-0-H-264-PiRaTeS-torrent-19859670.html"
)
REF = datetime(2026, 7, 26, 17, 0, 0)


def test_real_detail_basic_fields_and_links():
    detail = parse_detail(
        fixture("limetorrents_detail_st_vincent.html"),
        DETAIL_URL,
        REF,
    )
    assert detail["name"].startswith("St Vincent 2014")
    assert detail["info_hash"] == "700D963C82A513317703A730DD3C030E19FFAD8E"
    assert detail["category"] == "Movies"
    assert detail["total_size"] == "2.8 GB"
    assert detail["resource_links"]["magnet"].startswith("magnet:?xt=urn:btih:")
    assert ".torrent" in detail["resource_links"]["torrent"]
    assert detail["resource_links"]["stream"] == "https://www.limemovies.org/"
    assert detail["declared_file_count"] == 3
    assert detail["file_entry_count"] == 4
    assert len(detail["files"]) == 4
    assert len(detail["related_torrents"]) >= 5
    assert detail["comments_count"] == 0


def test_broken_detail_raises_parse_error():
    with pytest.raises(ParseError, match="详情页结构无法识别"):
        parse_detail("<html><h1>broken</h1></html>", DETAIL_URL, REF)