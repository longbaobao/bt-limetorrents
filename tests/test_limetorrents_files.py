from bs4 import BeautifulSoup

from crawl_detail_limetorrents import parse_files
from conftest import fixture


def test_real_file_tree_contains_three_files_and_directory():
    soup = BeautifulSoup(
        fixture("limetorrents_detail_st_vincent.html"),
        "html.parser",
    )
    entries, declared_count = parse_files(soup)
    assert declared_count == 3
    assert len(entries) == 4
    assert entries[0]["entry_type"] == "directory"
    assert entries[0]["depth"] == 0
    assert any(entry["entry_type"] == "video" and entry["size"] == "2.8 GB" for entry in entries)
    assert any(entry["entry_type"] == "nfo" and entry["size"] == "777 bytes" for entry in entries)
