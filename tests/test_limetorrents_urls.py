"""LimeTorrents URL 与分类纯函数测试。"""
import pytest

from crawl_limetorrents import (
    build_browse_url,
    build_search_url,
    normalize_category,
    slugify_keyword,
)


def test_default_browse_url_shape():
    assert build_browse_url("Movies", 2) == (
        "https://www.limetorrents.fun/browse-torrents/Movies/date/2/"
    )


def test_search_first_and_later_page_shape():
    assert build_search_url("all", "St Vincent", 1) == (
        "https://www.limetorrents.fun/search/all/St-Vincent/"
    )
    assert build_search_url("all", "St Vincent", 2) == (
        "https://www.limetorrents.fun/search/all/St-Vincent//2/"
    )


def test_slug_collapses_whitespace_and_encodes_path_chars():
    assert slugify_keyword("  St   Vincent / 2014  ") == "St-Vincent-%2F-2014"


def test_empty_keyword_is_rejected():
    with pytest.raises(ValueError, match="关键词不能为空"):
        slugify_keyword("   ")


@pytest.mark.parametrize(
    "value, expected",
    [("movies", "Movies"), ("TV shows", "TV-shows"), ("applications", "Applications")],
)
def test_normalize_category(value, expected):
    assert normalize_category(value) == expected


def test_all_only_allowed_for_search():
    assert normalize_category("all", allow_all=True) == "all"
    with pytest.raises(ValueError, match="不支持的分类"):
        normalize_category("all")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Anime.", "Anime"),
        ("Anime,", "Anime"),
        ("Anime ;", "Anime"),
        ("Movies  ", "Movies"),
        ('"Movies"', "Movies"),
    ],
)
def test_normalize_category_strips_trailing_punctuation(value, expected):
    """related 区域偶尔会写 `Anime.` / `Movies ;` 等带标点别名,需 trim 后查表。"""
    assert normalize_category(value) == expected


def test_normalize_category_unknown_still_raises_after_trim():
    with pytest.raises(ValueError, match="不支持的分类"):
        normalize_category("Reality-TV.")