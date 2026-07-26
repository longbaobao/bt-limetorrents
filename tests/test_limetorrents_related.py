from datetime import datetime

from bs4 import BeautifulSoup

from crawl_detail_limetorrents import parse_comments_count, parse_related_torrents
from conftest import fixture


def test_related_torrents():
    soup = BeautifulSoup(fixture("limetorrents_detail_st_vincent.html"), "html.parser")
    related = parse_related_torrents(soup, datetime(2026, 7, 26, 17, 0, 0))
    assert len(related) >= 5
    assert all(item["detail_url"].endswith(".html") for item in related)
    assert all(set(item) == {
        "name", "detail_url", "added_text", "added_at",
        "category", "size", "seeders", "leechers",
    } for item in related)


def test_comments_count():
    soup = BeautifulSoup(fixture("limetorrents_detail_st_vincent.html"), "html.parser")
    assert parse_comments_count(soup) == 0
