from datetime import datetime

from bs4 import BeautifulSoup

from crawl_limetorrents import (
    detect_next_url,
    parse_listing,
    parse_result_row,
)
from conftest import fixture

REF = datetime(2026, 7, 26, 17, 0, 0)


def test_browse_page_parses_real_rows():
    items = parse_listing(
        fixture("limetorrents_browse_movies_page2.html"),
        mode="browse",
        category="Movies",
        ref_now=REF,
    )
    assert len(items) >= 35
    st_vincent = next(item for item in items if "St Vincent 2014 1080p PTV" in item["name"])
    assert st_vincent["category"] == "Movies"
    assert st_vincent["size"] == "2.8 GB"
    assert st_vincent["seeders"] >= 0
    assert st_vincent["leechers"] >= 0
    assert st_vincent["detail_url"].endswith("torrent-19859670.html")
    assert ".torrent" in st_vincent["torrent_url"]


def test_search_ignores_sponsored_table_and_extracts_category():
    items = parse_listing(
        fixture("limetorrents_search_st_vincent.html"),
        mode="search",
        category="all",
        keyword="St Vincent",
        ref_now=REF,
    )
    assert items
    assert all("Sponsored" not in item["name"] for item in items)
    assert all("leet2" not in item["detail_url"] for item in items)
    assert any(item["category"] == "Movies" for item in items)


def test_next_url_uses_actual_href():
    url = detect_next_url(
        fixture("limetorrents_search_st_vincent.html"),
        "https://www.limetorrents.fun/search/all/St-Vincent/",
    )
    assert url == "https://www.limetorrents.fun/search/all/St-Vincent//2/"


def test_unrecognized_row_returns_none():
    row = BeautifulSoup("<tr><td>broken</td></tr>", "html.parser").tr
    assert parse_result_row(row, fallback_category="Movies", ref_now=REF) is None
