"""测试 parse_limetorrents_time：将 LimeTorrents 列表里的时间文本转为 yyyy-mm-dd hh:mm:ss。"""
from datetime import datetime

from crawl_limetorrents import parse_limetorrents_time

REF = datetime(2026, 7, 26, 17, 0, 0)


def test_relative_hours():
    assert parse_limetorrents_time("7 hours ago", REF) == "2026-07-26 10:00:00"


def test_relative_days_with_category_suffix():
    assert parse_limetorrents_time("17 days ago - in Music", REF) == "2026-07-09 17:00:00"


def test_yesterday():
    assert parse_limetorrents_time("Yesterday", REF) == "2026-07-25 17:00:00"


def test_absolute_date():
    assert parse_limetorrents_time("Jul 21, 2026", REF) == "2026-07-21 00:00:00"


def test_unknown_time_is_empty():
    assert parse_limetorrents_time("unknown", REF) == ""
