"""parse_args 轻量单元：keyword 原地 slug 化 + 分类白名单 + 数字校验。"""
import pytest

import crawl_limetorrents as crawler


def test_keyword_is_slugified_in_place():
    """--keyword "St Vincent" 应被原地规范化为 'St-Vincent',而非保留原串。"""
    args = crawler.parse_args(["--keyword", "St Vincent", "--search-category", "all"])
    assert args.keyword == "St-Vincent"
    # build_search_url 拿到的就是 slug,不会再二次 quote
    assert crawler.build_search_url(args.search_category, args.keyword, 1) == (
        "https://www.limetorrents.fun/search/all/St-Vincent/"
    )


def test_keyword_collapsed_for_multiword_input():
    """连续空格应折叠为单个 '-',不留空隙。"""
    args = crawler.parse_args(["--keyword", "  Music   2024  ", "--search-category", "all"])
    assert " " not in args.keyword
    assert args.keyword == "Music-2024"


def test_category_whitelist_passed_via_normalize():
    """--category 在 parse_args 内就被白名单归一化;非法值应抛 ValueError。"""
    args = crawler.parse_args(["--category", "tv shows"])
    assert args.category == "TV-shows"
    with pytest.raises(ValueError, match="不支持的分类"):
        crawler.parse_args(["--category", "NotARealCategory"])


def test_search_category_defaults_to_all():
    args = crawler.parse_args(["--keyword", "Music"])
    assert args.search_category == "all"


def test_start_page_must_be_at_least_one():
    with pytest.raises(SystemExit):
        crawler.parse_args(["--start-page", "0"])


def test_max_pages_must_be_non_negative():
    with pytest.raises(SystemExit):
        crawler.parse_args(["--max-pages", "-1"])


def test_keyword_omitted_means_browse_mode():
    args = crawler.parse_args(["--category", "Movies"])
    assert args.keyword is None
    assert args.category == "Movies"
