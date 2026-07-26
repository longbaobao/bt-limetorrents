# LimeTorrents 爬虫替换实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 1337x 列表、批量关键词和详情爬虫直接替换为 LimeTorrents 实现，支持分类浏览、关键词搜索、完整详情解析、MongoDB 幂等续跑和离线测试。

**Architecture:** 保留“列表发现 → `bt_info_list` → 详情状态机 → `bt_info_detail`”两阶段架构。列表入口统一处理分类浏览和单关键词搜索，批量 wrapper 复用单关键词入口；详情脚本使用 DrissionPage 抓取页面、缓存 HTML、解析完整 tracker/文件/相关资源并按 CAS 状态机写库。

**Tech Stack:** Python 3.11、DrissionPage、BeautifulSoup4、PyMongo、pytest、本地 MongoDB、Chromium。

## Global Constraints

- 数据库固定为 `bt_limetorrents_spider_db`。
- 集合只使用 `bt_info_list` 和 `bt_info_detail`，不得创建 `bt_info_files`。
- 完整文件清单保存到 `bt_info_detail.files`；超过 MongoDB 16 MB 时明确失败，不得静默截断。
- 分类浏览默认 `Movies`；允许 `Anime`、`Applications`、`Games`、`Movies`、`Music`、`TV-shows`、`Other`。
- 关键词搜索分类默认 `all`。
- 默认详情并发为 `2`。
- 所有业务时间保存为 `yyyy-mm-dd hh:mm:ss` 字符串，并保留站点原始时间文本。
- 页面访问使用 DrissionPage + 真实 Chromium；不得用 `requests` 或页面内 `fetch` 代替浏览器导航。
- 不下载 `.torrent`，不打开 magnet，不访问相关 torrent 二级详情页。
- 现有 1337x 脚本直接改名替换；最终不得保留旧入口。
- 每个行为变更遵循 red → green → refactor；每个任务结束运行指定测试并用中文提交。
- 所有 Python、pytest 和 compileall 命令使用项目解释器 `.venv/Scripts/python.exe`，不得调用系统 Python。
- 实施开始前使用 `superpowers:using-git-worktrees` 在 `.claude/worktrees/worktree-feature-limetorrents-crawler-2026-07-26` 隔离执行。

---

### Task 1: 建立可工作的 LimeTorrents 文件基线

**Files:**
- Rename: `crawl_1337x_by_key.py` → `crawl_limetorrents.py`
- Rename: `crawl_1337x_by_keys.py` → `crawl_limetorrents_by_keys.py`
- Rename: `crawl_detail_1337x.py` → `crawl_detail_limetorrents.py`
- Rename: `migrate_1337x.py` → `init_limetorrents_db.py`
- Rename: `tests/test_parse_detail.py` → `tests/test_limetorrents_detail.py`
- Rename: `tests/test_parse_relative_time.py` → `tests/test_limetorrents_time.py`
- Modify: all Python imports that reference renamed modules

**Interfaces:**
- Consumes: 当前 1337x 文件和现有测试。
- Produces: 可导入的 `crawl_limetorrents`、`crawl_limetorrents_by_keys`、`crawl_detail_limetorrents` 模块；本任务只做机械重命名，不改变抓取行为。

- [ ] **Step 1: 使用 Git 重命名三个入口和两个测试文件**

```bash
git mv crawl_1337x_by_key.py crawl_limetorrents.py
git mv crawl_1337x_by_keys.py crawl_limetorrents_by_keys.py
git mv crawl_detail_1337x.py crawl_detail_limetorrents.py
git mv migrate_1337x.py init_limetorrents_db.py
git mv tests/test_parse_detail.py tests/test_limetorrents_detail.py
git mv tests/test_parse_relative_time.py tests/test_limetorrents_time.py
```

- [ ] **Step 2: 更新模块导入和 wrapper 脚本名**

将 Python 文件中的导入统一改为：

```python
from crawl_limetorrents import DB_NAME, MONGO_URI, fetch_with_cf_bypass
from crawl_detail_limetorrents import parse_detail
```

在 `crawl_limetorrents_by_keys.py` 中改为：

```python
SCRIPT = "crawl_limetorrents.py"
```

测试文件中的导入改为：

```python
from crawl_limetorrents import parse_1337x_time
from crawl_detail_limetorrents import parse_detail, ParseError
```

此时暂时保留旧函数名，确保纯机械重命名后行为未改变。

- [ ] **Step 3: 验证没有遗留旧模块导入**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests test_checkpoint.py test_empty_page.py test_signature.py -q
```

Expected: 当前基线测试全部通过；不得出现 `ModuleNotFoundError`。

- [ ] **Step 4: 提交机械重命名**

```bash
git add -A
git commit -m "重构：重命名 LimeTorrents 爬虫入口"
```

---

### Task 2: 实现分类、关键词 URL 与时间解析纯函数

**Files:**
- Modify: `crawl_limetorrents.py`
- Create: `tests/test_limetorrents_urls.py`
- Modify: `tests/test_limetorrents_time.py`

**Interfaces:**
- Produces:
  - `normalize_category(value: str, allow_all: bool = False) -> str`
  - `slugify_keyword(keyword: str) -> str`
  - `build_browse_url(category: str, page: int = 1) -> str`
  - `build_search_url(category: str, keyword: str, page: int = 1) -> str`
  - `parse_limetorrents_time(text: str, ref_now: datetime | None = None) -> str`
  - `now_str() -> str`

- [ ] **Step 1: 写分类与 URL 的失败测试**

Create `tests/test_limetorrents_urls.py`:

```python
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
```

- [ ] **Step 2: 运行 URL 测试并确认失败**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_urls.py -v
```

Expected: FAIL，提示 `build_browse_url` 等函数不存在。

- [ ] **Step 3: 实现 URL 和分类函数**

在 `crawl_limetorrents.py` 中替换旧 BASE/DB 常量，并加入：

```python
import re
from urllib.parse import quote

BASE = "https://www.limetorrents.fun"
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "bt_limetorrents_spider_db"
COLL_NAME = "bt_info_list"

BROWSE_CATEGORIES = {
    "anime": "Anime",
    "applications": "Applications",
    "games": "Games",
    "movies": "Movies",
    "music": "Music",
    "tv-shows": "TV-shows",
    "tv shows": "TV-shows",
    "tv": "TV-shows",
    "other": "Other",
}


def normalize_category(value: str, allow_all: bool = False) -> str:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    if allow_all and normalized == "all":
        return "all"
    try:
        return BROWSE_CATEGORIES[normalized]
    except KeyError as exc:
        raise ValueError(f"不支持的分类: {value}") from exc


def slugify_keyword(keyword: str) -> str:
    collapsed = re.sub(r"\s+", "-", keyword.strip())
    if not collapsed:
        raise ValueError("关键词不能为空")
    return quote(collapsed, safe="-")


def build_browse_url(category: str, page: int = 1) -> str:
    if page < 1:
        raise ValueError("page 必须大于等于 1")
    category = normalize_category(category)
    return f"{BASE}/browse-torrents/{category}/date/{page}/"


def build_search_url(category: str, keyword: str, page: int = 1) -> str:
    if page < 1:
        raise ValueError("page 必须大于等于 1")
    category = normalize_category(category, allow_all=True)
    base = f"{BASE}/search/{category}/{slugify_keyword(keyword)}/"
    return base if page == 1 else f"{base}/{page}/"
```

- [ ] **Step 4: 运行 URL 测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_limetorrents_urls.py -v`  
Expected: PASS。

- [ ] **Step 5: 用 LimeTorrents 语义重写时间测试**

Replace `tests/test_limetorrents_time.py` with:

```python
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
```

- [ ] **Step 6: 运行时间测试并确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_limetorrents_time.py -v`  
Expected: FAIL，提示 `parse_limetorrents_time` 不存在。

- [ ] **Step 7: 实现时间解析**

```python
from datetime import datetime, timedelta


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_limetorrents_time(
    text: str, ref_now: datetime | None = None
) -> str:
    ref_now = ref_now or datetime.now()
    raw = re.sub(r"\s+-?\s*in\s+.+$", "", text.strip(), flags=re.IGNORECASE)
    if not raw:
        return ""
    if raw.lower() == "today":
        return ref_now.strftime("%Y-%m-%d %H:%M:%S")
    if raw.lower() == "yesterday":
        return (ref_now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    match = re.fullmatch(
        r"(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("minute"):
            delta = timedelta(minutes=value)
        elif unit.startswith("hour"):
            delta = timedelta(hours=value)
        elif unit.startswith("day"):
            delta = timedelta(days=value)
        elif unit.startswith("week"):
            delta = timedelta(weeks=value)
        elif unit.startswith("month"):
            delta = timedelta(days=30 * value)
        else:
            delta = timedelta(days=365 * value)
        return (ref_now - delta).strftime("%Y-%m-%d %H:%M:%S")

    for fmt in ("%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""
```

- [ ] **Step 8: 运行纯函数测试并提交**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_urls.py tests/test_limetorrents_time.py -v
git add crawl_limetorrents.py tests/test_limetorrents_urls.py tests/test_limetorrents_time.py
git commit -m "功能：添加 LimeTorrents URL 与时间解析"
```

---

### Task 3: 捕获真实 fixture 并实现列表解析

**Files:**
- Create: `tests/fixtures/capture_limetorrents_fixtures.py`
- Create: `tests/fixtures/limetorrents_browse_movies_page2.html`
- Create: `tests/fixtures/limetorrents_search_st_vincent.html`
- Create: `tests/fixtures/limetorrents_detail_st_vincent.html`
- Create: `tests/test_limetorrents_listing.py`
- Modify: `crawl_limetorrents.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: Task 2 URL/time函数。
- Produces:
  - `extract_added_category(text: str, fallback: str) -> tuple[str, str]`
  - `parse_result_row(row, *, fallback_category: str, ref_now: datetime) -> dict | None`
  - `parse_listing(html: str, *, mode: str, category: str, keyword: str | None = None, ref_now: datetime | None = None) -> list[dict]`
  - `detect_next_url(html: str, current_url: str) -> str | None`
  - `has_result_table(html: str) -> bool`

- [ ] **Step 1: 编写 fixture 捕获脚本**

```python
# tests/fixtures/capture_limetorrents_fixtures.py
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parents[2]))

from DrissionPage import ChromiumOptions, ChromiumPage

TARGETS = {
    "limetorrents_browse_movies_page2.html":
        "https://www.limetorrents.fun/browse-torrents/Movies/date/2/",
    "limetorrents_search_st_vincent.html":
        "https://www.limetorrents.fun/search/all/St-Vincent/",
    "limetorrents_detail_st_vincent.html":
        "https://www.limetorrents.fun/St-Vincent-2014-1080p-PTV-WEB-DL-AAC-2-0-H-264-PiRaTeS-torrent-19859670.html",
}


def main() -> None:
    output_dir = Path(__file__).parent
    page = ChromiumPage(ChromiumOptions().auto_port(True))
    try:
        for filename, url in TARGETS.items():
            page.get(url)
            selector = "css:div.torrentinfo" if "detail" in filename else "css:table.table2"
            page.ele(selector, timeout=45)
            html = page.html
            (output_dir / filename).write_text(html, encoding="utf-8")
            print(f"保存 {filename}: {len(html)} 字符")
    finally:
        page.quit()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行捕获脚本并验证三个文件**

```bash
.venv/Scripts/python.exe tests/fixtures/capture_limetorrents_fixtures.py
.venv/Scripts/python.exe -c "from pathlib import Path; fs=list(Path('tests/fixtures').glob('limetorrents_*.html')); print([(f.name, f.stat().st_size) for f in fs]); assert len(fs) >= 3; assert all(f.stat().st_size > 10000 for f in fs)"
```

Expected: 三个 fixture 均大于 10 KB。

- [ ] **Step 3: 添加 fixture 读取 helper**

在 `tests/conftest.py` 中保留现有 `fixture(name)`，并确保实现为：

```python
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")
```

- [ ] **Step 4: 写列表解析失败测试**

Create `tests/test_limetorrents_listing.py`:

```python
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
```

- [ ] **Step 5: 运行列表测试并确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_limetorrents_listing.py -v`  
Expected: FAIL，缺少新的 parser 接口。

- [ ] **Step 6: 实现列表 parser**

在 `crawl_limetorrents.py` 中加入：

```python
import hashlib
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

DETAIL_HREF_RE = re.compile(r"-torrent-\d+\.html(?:$|\?)", re.IGNORECASE)


def _as_int(text: str) -> int:
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else 0


def extract_added_category(text: str, fallback: str) -> tuple[str, str]:
    match = re.search(r"\s+-?\s*in\s+(.+?)\s*$", text, re.IGNORECASE)
    if not match:
        return text.strip(), fallback
    category = normalize_category(match.group(1), allow_all=False)
    added_text = text[:match.start()].strip()
    return added_text, category


def parse_result_row(row, *, fallback_category: str, ref_now: datetime) -> dict | None:
    detail_link = None
    torrent_link = None
    for link in row.select("td.tdleft a[href]"):
        href = link.get("href", "")
        if DETAIL_HREF_RE.search(href):
            detail_link = link
        elif ".torrent" in href.lower():
            torrent_link = link
    if detail_link is None:
        return None

    normal_cells = row.select("td.tdnormal")
    added_raw = normal_cells[0].get_text(" ", strip=True) if normal_cells else ""
    size = normal_cells[1].get_text(" ", strip=True) if len(normal_cells) > 1 else ""
    added_text, category = extract_added_category(added_raw, fallback_category)
    detail_url = urljoin(BASE, detail_link.get("href", ""))
    observed_at = ref_now.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "_id": hashlib.md5(detail_url.encode("utf-8")).hexdigest(),
        "name": detail_link.get_text(" ", strip=True),
        "detail_url": detail_url,
        "torrent_url": urljoin(BASE, torrent_link.get("href", "")) if torrent_link else "",
        "category": category,
        "added_text": added_raw,
        "added_at": parse_limetorrents_time(added_raw, ref_now),
        "size": size,
        "seeders": _as_int(row.select_one("td.tdseed").get_text(strip=True)) if row.select_one("td.tdseed") else 0,
        "leechers": _as_int(row.select_one("td.tdleech").get_text(strip=True)) if row.select_one("td.tdleech") else 0,
        "observed_at": observed_at,
        "source": "limetorrents",
    }


def parse_listing(
    html: str,
    *,
    mode: str,
    category: str,
    keyword: str | None = None,
    ref_now: datetime | None = None,
) -> list[dict]:
    if mode not in {"browse", "search"}:
        raise ValueError(f"未知 mode: {mode}")
    ref_now = ref_now or datetime.now()
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for table in soup.select("table.table2"):
        for row in table.select("tr"):
            if row.select_one("th"):
                continue
            item = parse_result_row(
                row,
                fallback_category=category,
                ref_now=ref_now,
            )
            if item:
                item["discovery_mode"] = mode
                item["keyword"] = keyword
                result.append(item)
    return result


def has_result_table(html: str) -> bool:
    return BeautifulSoup(html, "html.parser").select_one("table.table2") is not None


def detect_next_url(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select("a[href]"):
        if link.get_text(" ", strip=True).lower() == "next page":
            return urljoin(current_url, link["href"])
    return None
```

- [ ] **Step 7: 运行列表测试与纯函数回归**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_listing.py tests/test_limetorrents_urls.py tests/test_limetorrents_time.py -v
```

Expected: PASS。

- [ ] **Step 8: 提交 fixture 和列表 parser**

```bash
git add crawl_limetorrents.py tests/conftest.py tests/fixtures tests/test_limetorrents_listing.py
git commit -m "功能：解析 LimeTorrents 列表与搜索结果"
```

---

### Task 4: 实现幂等列表写入、checkpoint 与双模式主循环

**Files:**
- Modify: `crawl_limetorrents.py`
- Replace: `test_checkpoint.py` → `tests/test_limetorrents_checkpoint.py`
- Create: `tests/test_limetorrents_persistence.py`
- Modify: `test_empty_page.py`
- Modify: `test_signature.py`

**Interfaces:**
- Consumes: Task 3 `parse_listing`、`detect_next_url`、`has_result_table`。
- Produces:
  - `checkpoint_path(mode: str, category: str, keyword: str | None) -> Path`
  - `load_checkpoint(mode: str, category: str, keyword: str | None) -> dict | None`
  - `save_checkpoint(state: dict) -> None`
  - `clear_checkpoint(mode: str, category: str, keyword: str | None) -> None`
  - `upsert_listing(coll, item: dict) -> bool`
  - `parse_args(argv: list[str] | None = None) -> argparse.Namespace`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 将 checkpoint 测试迁入 pytest 并写失败用例**

Create `tests/test_limetorrents_checkpoint.py`:

```python
import json

import crawl_limetorrents as crawler


def test_checkpoint_is_query_specific_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, "CHECKPOINT_DIR", tmp_path)
    state = {
        "query_type": "search",
        "category": "all",
        "keyword": "St Vincent",
        "current_page": 1,
        "next_url": "https://www.limetorrents.fun/search/all/St-Vincent//2/",
        "updated_at": "2026-07-26 17:00:00",
    }
    crawler.save_checkpoint(state)
    loaded = crawler.load_checkpoint("search", "all", "St Vincent")
    assert loaded == state
    assert not list(tmp_path.glob("*.tmp"))
    assert crawler.load_checkpoint("browse", "Movies", None) is None


def test_clear_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler, "CHECKPOINT_DIR", tmp_path)
    state = {
        "query_type": "browse",
        "category": "Movies",
        "keyword": None,
        "current_page": 2,
        "next_url": None,
        "updated_at": "2026-07-26 17:00:00",
    }
    crawler.save_checkpoint(state)
    crawler.clear_checkpoint("browse", "Movies", None)
    assert crawler.load_checkpoint("browse", "Movies", None) is None
```

- [ ] **Step 2: 写列表 upsert 失败测试**

Create `tests/test_limetorrents_persistence.py`:

```python
from crawl_limetorrents import upsert_listing


class FakeResult:
    upserted_id = "new-id"


class FakeCollection:
    def __init__(self):
        self.filter = None
        self.update = None

    def update_one(self, filter_doc, update_doc, upsert=False):
        self.filter = filter_doc
        self.update = update_doc
        assert upsert is True
        return FakeResult()


def test_upsert_uses_set_on_insert_for_status():
    coll = FakeCollection()
    item = {
        "_id": "abc",
        "name": "Torrent",
        "detail_url": "https://example/torrent-1.html",
        "torrent_url": "https://itorrents/torrent/abc.torrent",
        "category": "Movies",
        "added_text": "7 hours ago",
        "added_at": "2026-07-26 10:00:00",
        "size": "2.8 GB",
        "seeders": 3,
        "leechers": 24,
        "observed_at": "2026-07-26 17:00:00",
        "source": "limetorrents",
        "discovery_mode": "search",
        "keyword": "St Vincent",
    }
    assert upsert_listing(coll, item) is True
    assert coll.filter == {"_id": "abc"}
    assert coll.update["$setOnInsert"]["detail_status"] == "pending"
    assert "detail_status" not in coll.update["$set"]
    assert coll.update["$addToSet"] == {
        "keywords": "St Vincent",
        "discovery_modes": "search",
    }
```

- [ ] **Step 3: 运行 checkpoint 和 persistence 测试确认失败**

Run:

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_checkpoint.py tests/test_limetorrents_persistence.py -v
```

Expected: FAIL，接口尚未实现。

- [ ] **Step 4: 实现原子 checkpoint**

```python
import json
from pathlib import Path

CHECKPOINT_DIR = Path("data/checkpoints")


def _query_key(mode: str, category: str, keyword: str | None) -> str:
    return f"{mode}|{category}|{keyword or ''}"


def checkpoint_path(mode: str, category: str, keyword: str | None) -> Path:
    digest = hashlib.md5(_query_key(mode, category, keyword).encode("utf-8")).hexdigest()
    return CHECKPOINT_DIR / f"limetorrents-{digest}.json"


def load_checkpoint(mode: str, category: str, keyword: str | None) -> dict | None:
    path = checkpoint_path(mode, category, keyword)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(state: dict) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(state["query_type"], state["category"], state.get("keyword"))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def clear_checkpoint(mode: str, category: str, keyword: str | None) -> None:
    checkpoint_path(mode, category, keyword).unlink(missing_ok=True)
```

- [ ] **Step 5: 实现幂等列表 upsert**

```python
def upsert_listing(coll, item: dict) -> bool:
    stored = {
        key: value
        for key, value in item.items()
        if key not in {"keyword", "discovery_mode", "observed_at"}
    }
    stored["last_seen_at"] = item["observed_at"]
    set_on_insert = {
        "first_seen_at": item["observed_at"],
        "detail_status": "pending",
        "detail_started_at": None,
        "detail_processed_at": None,
        "detail_error": None,
    }
    add_to_set = {"discovery_modes": item["discovery_mode"]}
    if item.get("keyword"):
        add_to_set["keywords"] = item["keyword"]
    else:
        set_on_insert["keywords"] = []

    result = coll.update_one(
        {"_id": item["_id"]},
        {
            "$set": stored,
            "$setOnInsert": set_on_insert,
            "$addToSet": add_to_set,
        },
        upsert=True,
    )
    return result.upserted_id is not None
```

- [ ] **Step 6: 把 argparse 移出 import 副作用并实现双模式参数**

```python
import argparse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LimeTorrents 列表与关键词爬虫")
    parser.add_argument("--keyword")
    parser.add_argument("--category", default="Movies")
    parser.add_argument("--search-category", default="all")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--page-sleep", type=float, default=PAGE_SLEEP)
    args = parser.parse_args(argv)
    if args.start_page < 1:
        parser.error("--start-page 必须大于等于 1")
    if args.max_pages < 0:
        parser.error("--max-pages 必须大于等于 0")
    args.category = normalize_category(args.category)
    args.search_category = normalize_category(args.search_category, allow_all=True)
    if args.keyword is not None:
        slugify_keyword(args.keyword)
    return args
```

- [ ] **Step 7: 重写主循环，失败页不得推进 checkpoint**

主循环必须遵循以下控制流：

```python
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mode = "search" if args.keyword else "browse"
    category = args.search_category if mode == "search" else args.category
    checkpoint = load_checkpoint(mode, category, args.keyword)
    if checkpoint:
        page_number = checkpoint["current_page"] + 1
        url = checkpoint["next_url"]
    else:
        page_number = args.start_page
        url = (
            build_search_url(category, args.keyword, page_number)
            if mode == "search"
            else build_browse_url(category, page_number)
        )

    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLL_NAME]
    processed_pages = 0
    browser = ChromiumPage(ChromiumOptions().auto_port(True))
    try:
        while url:
            html = load_page_with_retry(browser, url, page_number)
            if html is None or not has_result_table(html):
                return 2
            items = parse_listing(
                html,
                mode=mode,
                category=category,
                keyword=args.keyword,
            )
            next_url = detect_next_url(html, url)
            if not items and next_url:
                return 3
            for item in items:
                upsert_listing(coll, item)
            save_checkpoint({
                "query_type": mode,
                "category": category,
                "keyword": args.keyword,
                "current_page": page_number,
                "next_url": next_url,
                "updated_at": now_str(),
            })
            processed_pages += 1
            if args.max_pages and processed_pages >= args.max_pages:
                return 0
            if next_url is None:
                clear_checkpoint(mode, category, args.keyword)
                return 0
            url = next_url
            page_number += 1
            time.sleep(args.page_sleep)
        clear_checkpoint(mode, category, args.keyword)
        return 0
    finally:
        browser.quit()
```

保留现有 Cloudflare challenge marker 和浏览器重试实现，但目标选择器改为 `css:table.table2`。

- [ ] **Step 8: 更新旧 smoke 测试为 LimeTorrents 语义**

`test_empty_page.py` 应断言：

```python
assert has_result_table("<html></html>") is False
assert parse_listing(
    "<table class='table2'><tr><th>Torrent Name</th></tr></table>",
    mode="browse",
    category="Movies",
) == []
```

`test_signature.py` 应导入并检查：

```python
from crawl_limetorrents import main, parse_args, parse_listing
assert callable(main)
assert callable(parse_args)
assert callable(parse_listing)
```

- [ ] **Step 9: 运行列表全套测试并提交**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_urls.py tests/test_limetorrents_time.py tests/test_limetorrents_listing.py tests/test_limetorrents_checkpoint.py tests/test_limetorrents_persistence.py test_empty_page.py test_signature.py -v
git add -A
git commit -m "功能：实现 LimeTorrents 列表续跑与幂等写入"
```

---

### Task 5: 更新批量关键词 wrapper

**Files:**
- Modify: `crawl_limetorrents_by_keys.py`
- Create: `tests/test_limetorrents_by_keys.py`

**Interfaces:**
- Consumes: `crawl_limetorrents.py --keyword <key> --search-category <category>`。
- Produces:
  - `build_worker_args(key: str, search_category: str) -> list[str]`
  - `run_one(key: str, search_category: str) -> tuple[str, int, str]`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 写 wrapper 参数和 done 语义测试**

```python
# tests/test_limetorrents_by_keys.py
import sys

import crawl_limetorrents_by_keys as batch


def test_build_worker_args():
    assert batch.build_worker_args("St Vincent", "all") == [
        sys.executable,
        "crawl_limetorrents.py",
        "--keyword",
        "St Vincent",
        "--search-category",
        "all",
    ]


def test_failed_key_is_not_appended(monkeypatch):
    appended = []
    monkeypatch.setattr(batch, "load_keys", lambda: ["ok", "bad"])
    monkeypatch.setattr(batch, "load_done", lambda: set())
    monkeypatch.setattr(
        batch,
        "run_one",
        lambda key, category: (key, 0 if key == "ok" else 2, "failed"),
    )
    monkeypatch.setattr(batch, "append_done", appended.append)
    assert batch.main(["--search-category", "all", "--concurrency", "1"]) == 1
    assert appended == ["ok"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_limetorrents_by_keys.py -v`  
Expected: FAIL，函数签名不匹配。

- [ ] **Step 3: 实现 worker 参数和可测试 main**

```python
def build_worker_args(key: str, search_category: str) -> list[str]:
    return [
        sys.executable,
        SCRIPT,
        "--keyword",
        key,
        "--search-category",
        search_category,
    ]


def run_one(key: str, search_category: str) -> tuple[str, int, str]:
    args = build_worker_args(key, search_category)
    try:
        proc = subprocess.run(
            args,
            stdout=None,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WORKER_TIMEOUT,
        )
        tail = "\n".join((proc.stderr or "").splitlines()[-10:])
        return key, proc.returncode, tail
    except subprocess.TimeoutExpired:
        return key, 124, f"timeout after {WORKER_TIMEOUT}s"
    except Exception as exc:
        return key, 1, f"{type(exc).__name__}: {exc}"
```

`main(argv=None)` 使用 argparse 接收：

```python
parser.add_argument("--search-category", default="all")
parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
```

只有 `returncode == 0` 时调用 `append_done(key)`；存在任何失败时最终返回 `1`。

- [ ] **Step 4: 运行 wrapper 测试并提交**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_by_keys.py -v
git add crawl_limetorrents_by_keys.py tests/test_limetorrents_by_keys.py
git commit -m "功能：支持批量 LimeTorrents 关键词搜索"
```

---

### Task 6: 实现详情基础字段、下载链接与 tracker 解析

**Files:**
- Modify: `crawl_detail_limetorrents.py`
- Replace: `tests/test_limetorrents_detail.py`
- Create: `tests/test_limetorrents_trackers.py`

**Interfaces:**
- Consumes: Task 2 `parse_limetorrents_time`、`now_str`，Task 3 真实详情 fixture。
- Produces:
  - `class ParseError(Exception)`
  - `find_table_after_heading(soup, heading_text: str, table_class: str | None = None)`
  - `parse_basic_info(soup, ref_now: datetime) -> dict`
  - `parse_trackers(soup, ref_now: datetime) -> list[dict]`
  - `parse_detail(html: str, detail_url: str, ref_now: datetime | None = None) -> dict`

- [ ] **Step 1: 写详情基础字段失败测试**

Replace `tests/test_limetorrents_detail.py` with:

```python
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


def test_broken_detail_raises_parse_error():
    with pytest.raises(ParseError, match="详情页结构无法识别"):
        parse_detail("<html><h1>broken</h1></html>", DETAIL_URL, REF)
```

- [ ] **Step 2: 写 tracker 失败测试**

Create `tests/test_limetorrents_trackers.py`:

```python
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
```

- [ ] **Step 3: 运行详情测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_detail.py tests/test_limetorrents_trackers.py -v
```

Expected: FAIL，旧 1337x parser 不识别 `div.torrentinfo`。

- [ ] **Step 4: 更新详情模块常量和缓存路径**

```python
from crawl_limetorrents import (
    BASE,
    DB_NAME,
    MONGO_URI,
    fetch_with_cf_bypass,
    now_str,
    parse_limetorrents_time,
)

COLL_LIST = "bt_info_list"
COLL_DETAIL = "bt_info_detail"
HTML_DIR = Path("data/html/limetorrents")
CONCURRENCY = 2
```

- [ ] **Step 5: 实现 heading 后表格定位和基本信息**

```python
def find_table_after_heading(soup, heading_text: str, table_class: str | None = None):
    for heading in soup.select("h2"):
        if heading.get_text(" ", strip=True).lower() != heading_text.lower():
            continue
        for element in heading.find_all_next():
            if element is not heading and element.name == "h2":
                break
            if element.name != "table":
                continue
            if table_class and table_class not in element.get("class", []):
                continue
            return element
    return None


def parse_basic_info(soup, ref_now: datetime) -> dict:
    table = soup.select_one("div.torrentinfo > table")
    if table is None:
        raise ParseError("详情页缺少基本信息表")
    values = {}
    links = {}
    for row in table.select("tr"):
        cells = row.select("td")
        if len(cells) < 2:
            continue
        key = cells[0].get_text(" ", strip=True).rstrip(":").strip().lower()
        values[key] = cells[1].get_text(" ", strip=True)
        link = cells[1].select_one("a[href]")
        if link:
            links[key] = urljoin(BASE, link["href"])
    added_raw = values.get("torrent added", "")
    category_match = re.search(r"\bin\s+(.+)$", added_raw, re.IGNORECASE)
    return {
        "info_hash": values.get("torrent hash", "").strip(),
        "added_text": added_raw,
        "added_at": parse_limetorrents_time(added_raw, ref_now),
        "category": category_match.group(1).strip() if category_match else "",
        "total_size": values.get("torrent size", ""),
        "stream": links.get("stream", ""),
    }
```

- [ ] **Step 6: 实现 tracker parser**

```python
def parse_trackers(soup, ref_now: datetime) -> list[dict]:
    table = find_table_after_heading(soup, "Trackers List", "table3")
    if table is None:
        return []
    trackers = []
    for row in table.select("tr"):
        cells = row.select("td")
        if len(cells) != 5:
            continue
        trackers.append({
            "url": cells[0].get_text(" ", strip=True),
            "last_check_text": cells[1].get_text(" ", strip=True),
            "last_checked_at": parse_limetorrents_time(
                cells[1].get_text(" ", strip=True),
                ref_now,
            ),
            "status": cells[2].get_text(" ", strip=True).lower(),
            "seeders": _as_int(cells[3].get_text(strip=True)),
            "leechers": _as_int(cells[4].get_text(strip=True)),
        })
    return trackers
```

- [ ] **Step 7: 实现第一版 parse_detail**

```python
def parse_detail(
    html: str,
    detail_url: str,
    ref_now: datetime | None = None,
) -> dict:
    ref_now = ref_now or datetime.now()
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("div.torrentinfo") is None:
        raise ParseError(f"详情页结构无法识别: {detail_url}")
    heading = soup.select_one("h1")
    name = heading.get_text(" ", strip=True) if heading else ""
    basic = parse_basic_info(soup, ref_now)
    if not name or not basic["info_hash"]:
        raise ParseError(f"详情页缺少核心字段: {detail_url}")

    magnet = soup.select_one("div.downloadarea a[href^='magnet:']")
    torrent = soup.select_one("div.downloadarea a[href*='.torrent']")
    resources = {
        "magnet": magnet.get("href", "") if magnet else "",
        "torrent": urljoin(BASE, torrent.get("href", "")) if torrent else "",
        "stream": basic.pop("stream"),
    }
    trackers = parse_trackers(soup, ref_now)
    return {
        "_id": hashlib.md5(detail_url.encode("utf-8")).hexdigest(),
        "detail_url": detail_url,
        "name": name,
        **basic,
        "meta_description": (
            soup.select_one("meta[name='description']").get("content", "")
            if soup.select_one("meta[name='description']")
            else ""
        ),
        "resource_links": resources,
        "trackers": trackers,
        "tracker_count": len(trackers),
        "successful_tracker_count": sum(t["status"] == "success" for t in trackers),
        "failed_tracker_count": sum(t["status"] == "failed" for t in trackers),
        "files": [],
        "declared_file_count": 0,
        "file_entry_count": 0,
        "related_torrents": [],
        "comments_count": 0,
        "html_cache_path": str(html_cache_path(detail_url)).replace("\\", "/"),
        "source": "limetorrents",
        "parsed_at": ref_now.strftime("%Y-%m-%d %H:%M:%S"),
    }
```

- [ ] **Step 8: 运行详情基础测试并提交**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_detail.py tests/test_limetorrents_trackers.py -v
git add crawl_detail_limetorrents.py tests/test_limetorrents_detail.py tests/test_limetorrents_trackers.py
git commit -m "功能：解析 LimeTorrents 详情与 Tracker"
```

---

### Task 7: 解析完整文件树、相关 torrent 与评论计数

**Files:**
- Modify: `crawl_detail_limetorrents.py`
- Create: `tests/test_limetorrents_files.py`
- Create: `tests/test_limetorrents_related.py`
- Modify: `tests/test_limetorrents_detail.py`

**Interfaces:**
- Consumes: Task 3 `parse_result_row`，Task 6 基础详情 parser。
- Produces:
  - `parse_files(soup) -> tuple[list[dict], int]`
  - `parse_related_torrents(soup, ref_now: datetime) -> list[dict]`
  - `parse_comments_count(soup) -> int`

- [ ] **Step 1: 写文件树失败测试**

```python
# tests/test_limetorrents_files.py
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
```

- [ ] **Step 2: 写相关 torrent 和评论失败测试**

```python
# tests/test_limetorrents_related.py
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
```

- [ ] **Step 3: 运行测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_files.py tests/test_limetorrents_related.py -v
```

Expected: FAIL，函数不存在。

- [ ] **Step 4: 实现基于 DOM child 顺序的文件解析**

```python
from bs4.element import NavigableString, Tag

ICON_TYPES = {
    "csprite_doc_dir": "directory",
    "csprite_doc_video": "video",
    "csprite_doc_nfo": "nfo",
    "csprite_doc_doc": "document",
}


def _icon_type(node: Tag) -> str | None:
    classes = set(node.get("class", []))
    for class_name, entry_type in ICON_TYPES.items():
        if class_name in classes:
            return entry_type
    if any(name.startswith("csprite_doc_") for name in classes):
        return "file"
    return None


def parse_files(soup) -> tuple[list[dict], int]:
    declared_count = 0
    for heading in soup.select("h2"):
        match = re.search(
            r"Torrent File Content\s*\((\d+)\s+files?\)",
            heading.get_text(" ", strip=True),
            re.IGNORECASE,
        )
        if match:
            declared_count = int(match.group(1))
            break

    entries = []
    directory_stack: list[str] = []
    for fileline in soup.select(".fileline"):
        current_type = "file"
        current_text: list[str] = []
        current_size = ""

        def flush() -> None:
            nonlocal current_text, current_size, directory_stack
            raw = "".join(current_text)
            leading = len(raw) - len(raw.lstrip(" \t\r\n\xa0"))
            depth_hint = leading // 4
            raw_name = " ".join(raw.replace("\xa0", " ").split()).rstrip(" -")
            if not raw_name:
                current_text = []
                current_size = ""
                return
            if current_type == "directory":
                depth = min(depth_hint, len(directory_stack))
                parents = directory_stack[:depth]
                path = "/".join([*parents, raw_name])
                directory_stack = [*parents, raw_name]
            else:
                depth = min(
                    depth_hint or len(directory_stack),
                    len(directory_stack),
                )
                path = "/".join([*directory_stack[:depth], raw_name])
            entries.append({
                "entry_index": len(entries),
                "path": path,
                "size": current_size,
                "entry_type": current_type,
                "depth": depth,
            })
            current_text = []
            current_size = ""

        for child in fileline.children:
            if isinstance(child, NavigableString):
                current_text.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            detected = _icon_type(child)
            if detected:
                flush()
                current_type = detected
            elif "filelinesize" in child.get("class", []):
                current_size = child.get_text(" ", strip=True)
            elif child.name == "br":
                flush()
            else:
                current_text.append(child.get_text(" ", strip=True))
        flush()
    return entries, declared_count
```

前导空格或 `\xa0` 每四个表示一层目录；没有前导缩进的后续文件默认归入当前目录栈。目录层级来自 DOM 文本缩进，不按文件扩展名推断。

- [ ] **Step 5: 实现相关资源和评论计数**

```python
from crawl_limetorrents import parse_result_row


def parse_related_torrents(soup, ref_now: datetime) -> list[dict]:
    table = find_table_after_heading(soup, "Related torrents", "table2")
    if table is None:
        return []
    result = []
    for row in table.select("tr"):
        if row.select_one("th"):
            continue
        item = parse_result_row(row, fallback_category="", ref_now=ref_now)
        if not item:
            continue
        result.append({
            key: item[key]
            for key in (
                "name", "detail_url", "added_text", "added_at",
                "category", "size", "seeders", "leechers",
            )
        })
    return result


def parse_comments_count(soup) -> int:
    for heading in soup.select("h2"):
        match = re.search(
            r"Comments\s*\((\d+)\s+Comments?\)",
            heading.get_text(" ", strip=True),
            re.IGNORECASE,
        )
        if match:
            return int(match.group(1))
    return 0
```

- [ ] **Step 6: 接入完整详情文档**

在 `parse_detail` 返回前加入：

```python
files, declared_file_count = parse_files(soup)
related_torrents = parse_related_torrents(soup, ref_now)
```

返回字段改为：

```python
"files": files,
"declared_file_count": declared_file_count,
"file_entry_count": len(files),
"related_torrents": related_torrents,
"comments_count": parse_comments_count(soup),
```

- [ ] **Step 7: 扩展详情综合断言**

在 `tests/test_limetorrents_detail.py` 的真实详情测试中加入：

```python
assert detail["declared_file_count"] == 3
assert detail["file_entry_count"] == 4
assert len(detail["files"]) == 4
assert len(detail["related_torrents"]) >= 5
assert detail["comments_count"] == 0
```

- [ ] **Step 8: 运行 parser 全套测试并提交**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_detail.py tests/test_limetorrents_trackers.py tests/test_limetorrents_files.py tests/test_limetorrents_related.py -v
git add crawl_detail_limetorrents.py tests
git commit -m "功能：解析完整文件树与相关 Torrent"
```

---

### Task 8: 更新详情抓取、状态机、DocumentTooLarge 与 CLI

**Files:**
- Modify: `crawl_detail_limetorrents.py`
- Create: `tests/test_limetorrents_detail_state.py`
- Create: `tests/test_limetorrents_detail_run.py`
- Modify: `test_signature.py`

**Interfaces:**
- Consumes: Task 6-7 `parse_detail`。
- Produces:
  - `html_cache_path(detail_url: str) -> Path`
  - `fetch_one(tab, url: str) -> str`
  - `save_html_cache(detail_url: str, html: str) -> None`
  - `claim_one(coll_list, doc_id: str) -> dict | None`
  - `mark_done(coll_list, doc_id: str) -> None`
  - `mark_failed(coll_list, doc_id: str, error_msg: str) -> None`
  - `run_one(tab, doc: dict, coll_list, coll_detail, dry_run: bool = False) -> str`
  - `parse_args(argv: list[str] | None = None) -> argparse.Namespace`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 写状态字段和关键词过滤测试**

```python
# tests/test_limetorrents_detail_state.py
import crawl_detail_limetorrents as detail


class FakeCollection:
    def __init__(self):
        self.calls = []

    def update_one(self, filter_doc, update_doc):
        self.calls.append((filter_doc, update_doc))


def test_mark_failed_records_document_too_large():
    coll = FakeCollection()
    detail.mark_failed(coll, "abc", "DocumentTooLarge: 18MB")
    filter_doc, update_doc = coll.calls[-1]
    assert filter_doc == {"_id": "abc"}
    assert update_doc["$set"]["detail_status"] == "failed"
    assert "DocumentTooLarge" in update_doc["$set"]["detail_error"]


def test_keyword_query_targets_array_membership():
    assert detail.build_pending_query("St Vincent") == {
        "detail_status": "pending",
        "keywords": "St Vincent",
    }
```

- [ ] **Step 2: 写 run_one 不截断文件的失败测试**

```python
# tests/test_limetorrents_detail_run.py
from pymongo.errors import DocumentTooLarge

import crawl_detail_limetorrents as detail


class FakeList:
    def __init__(self):
        self.failed = None

    def update_one(self, filter_doc, update_doc):
        if update_doc.get("$set", {}).get("detail_status") == "failed":
            self.failed = update_doc["$set"]["detail_error"]


class OversizeDetail:
    def replace_one(self, *args, **kwargs):
        raise DocumentTooLarge("document exceeds 16MB")


class FakeTab:
    pass


def test_document_too_large_is_failed_without_truncation(monkeypatch):
    files = [{"path": str(i)} for i in range(5000)]
    monkeypatch.setattr(detail, "fetch_one", lambda tab, url: "<html></html>")
    monkeypatch.setattr(detail, "save_html_cache", lambda url, html: None)
    monkeypatch.setattr(detail, "parse_detail", lambda html, url: {
        "_id": "abc", "detail_url": url, "files": files,
    })
    coll_list = FakeList()
    result = detail.run_one(
        FakeTab(),
        {"_id": "abc", "detail_url": "https://example/detail"},
        coll_list,
        OversizeDetail(),
    )
    assert result == "failed"
    assert "DocumentTooLarge" in coll_list.failed
    assert len(files) == 5000
```

- [ ] **Step 3: 运行状态与 run 测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_detail_state.py tests/test_limetorrents_detail_run.py -v
```

Expected: FAIL，旧实现未提供 `build_pending_query` 且异常分类不符。

- [ ] **Step 4: 更新缓存和详情目标选择器**

```python
def html_cache_path(detail_url: str) -> Path:
    digest = hashlib.md5(detail_url.encode("utf-8")).hexdigest()
    return HTML_DIR / f"{digest}.html"


def fetch_one(tab, url: str) -> str:
    return fetch_with_cf_bypass(tab, url, "css:div.torrentinfo", max_wait=45)


def save_html_cache(detail_url: str, html: str) -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    html_cache_path(detail_url).write_text(html, encoding="utf-8")
```

- [ ] **Step 5: 实现显式 DocumentTooLarge 分支**

在 `run_one` 中，顺序固定为 fetch → cache → parse → replace/upsert → done：

```python
from pymongo.errors import DocumentTooLarge


def run_one(tab, doc, coll_list, coll_detail, dry_run: bool = False) -> str:
    doc_id = doc["_id"]
    url = doc["detail_url"]
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            html = fetch_one(tab, url)
            save_html_cache(url, html)
            parsed = parse_detail(html, url)
            if dry_run:
                logger.info(
                    "dry-run %s hash=%s files=%s trackers=%s",
                    url,
                    parsed["info_hash"],
                    parsed["file_entry_count"],
                    parsed["tracker_count"],
                )
                return "done"
            coll_detail.replace_one({"_id": parsed["_id"]}, parsed, upsert=True)
            mark_done(coll_list, doc_id)
            return "done"
        except DocumentTooLarge as exc:
            last_error = f"DocumentTooLarge: {exc}"
            if not dry_run:
                mark_failed(coll_list, doc_id, last_error)
            return "failed"
        except ParseError as exc:
            last_error = f"ParseError: {exc}"
            if not dry_run:
                mark_failed(coll_list, doc_id, last_error)
            return "failed"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF[attempt - 1])
    if not dry_run:
        mark_failed(coll_list, doc_id, last_error or "unknown")
    return "failed"
```

不得在 `DocumentTooLarge` 分支切片、压缩或删除 `files`。

- [ ] **Step 6: 实现过滤范围与 CLI 语义**

```python
def build_pending_query(keyword: str | None = None) -> dict:
    query = {"detail_status": "pending"}
    if keyword:
        query["keywords"] = keyword
    return query


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LimeTorrents 详情爬虫")
    parser.add_argument("-c", "--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("-b", "--batch", type=int, default=BATCH)
    parser.add_argument("-p", "--pace", type=float, default=1.0)
    parser.add_argument("-l", "--limit", type=int, default=0)
    parser.add_argument("-k", "--keyword")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency 必须大于等于 1")
    return args
```

重置范围必须先构造：

```python
scope = {"keywords": args.keyword} if args.keyword else {}
if args.force:
    coll_list.update_many(scope, {"$set": {"detail_status": "pending"}, "$unset": {
        "detail_started_at": "", "detail_processed_at": "", "detail_error": "",
    }})
elif args.retry_failed:
    coll_list.update_many({**scope, "detail_status": "failed"}, {
        "$set": {"detail_status": "pending"},
        "$unset": {"detail_started_at": "", "detail_processed_at": "", "detail_error": ""},
    })
```

`--dry-run` 不调用 `claim_one`，直接把 pending 文档交给 `run_batch(..., dry_run=True)`。

- [ ] **Step 7: 将默认并发改为 2 并完成 LimeTorrents 日志文案**

```python
CONCURRENCY = 2
```

所有日志、argparse description 和模块 docstring 中不得再出现 `1337x`。

- [ ] **Step 8: 运行详情全套测试并提交**

```bash
.venv/Scripts/python.exe -m pytest tests/test_limetorrents_detail.py tests/test_limetorrents_trackers.py tests/test_limetorrents_files.py tests/test_limetorrents_related.py tests/test_limetorrents_detail_state.py tests/test_limetorrents_detail_run.py -v
git add crawl_detail_limetorrents.py tests test_signature.py
git commit -m "功能：实现 LimeTorrents 详情状态机与完整入库"
```

---

### Task 9: 合并数据库初始化脚本并移除旧迁移

**Files:**
- Rewrite: `init_limetorrents_db.py`
- Delete: `migrate_detail_status.py`
- Create: `tests/test_init_limetorrents_db.py`

**Interfaces:**
- Consumes: `crawl_limetorrents.MONGO_URI`、`DB_NAME`、`COLL_NAME` 和 `crawl_detail_limetorrents.COLL_DETAIL`。
- Produces:
  - `initialize_database(db) -> dict[str, int]`
  - 幂等索引和缺失状态补齐。

- [ ] **Step 1: 写初始化脚本失败测试**

```python
# tests/test_init_limetorrents_db.py
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
```

- [ ] **Step 2: 运行初始化测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_init_limetorrents_db.py -v`  
Expected: FAIL，旧迁移脚本没有 `initialize_database`。

- [ ] **Step 3: 重写幂等初始化脚本**

```python
"""初始化 LimeTorrents MongoDB 集合、状态和索引；可重复执行。"""
import sys

from pymongo import ASCENDING, DESCENDING, MongoClient

from crawl_limetorrents import DB_NAME, MONGO_URI

sys.stdout.reconfigure(encoding="utf-8")

COLL_LIST = "bt_info_list"
COLL_DETAIL = "bt_info_detail"


def initialize_database(db) -> dict[str, int]:
    list_coll = db[COLL_LIST]
    detail_coll = db[COLL_DETAIL]
    result = list_coll.update_many(
        {"detail_status": {"$exists": False}},
        {"$set": {
            "detail_status": "pending",
            "detail_started_at": None,
            "detail_processed_at": None,
            "detail_error": None,
        }},
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
```

- [ ] **Step 4: 删除第二个旧迁移脚本并检查旧站点字符串**

```bash
git rm migrate_detail_status.py
```

在运行代码和测试中搜索 `1337x`，允许它只出现在设计/计划的历史背景段，不得存在于 Python 入口、集合名、数据库名或日志。

- [ ] **Step 5: 运行初始化测试并实际初始化本地数据库**

```bash
.venv/Scripts/python.exe -m pytest tests/test_init_limetorrents_db.py -v
.venv/Scripts/python.exe init_limetorrents_db.py
```

Expected:

```text
数据库 bt_limetorrents_spider_db 初始化完成: {'status_initialized': <数字>}
```

- [ ] **Step 6: 提交初始化脚本**

```bash
git add -A
git commit -m "功能：初始化 LimeTorrents MongoDB 索引"
```

---

### Task 10: 全量回归、真实浏览器验收和收口

**Files:**
- Modify: `CLAUDE.md`
- Modify: `requirements.txt` only if fixture/tests reveal a missing imported dependency
- Delete: obsolete 1337x-only root tests after equivalent pytest coverage exists
- Verify: all project Python files and MongoDB collections

**Interfaces:**
- Consumes: Tasks 1-9 全部实现。
- Produces: 可运行、可续跑、通过离线测试和真实页面冒烟验证的 LimeTorrents 爬虫。

- [ ] **Step 1: 运行完整离线测试**

```bash
.venv/Scripts/python.exe -m pytest -v
```

Expected: 全部测试 PASS，无 `1337x` 模块导入错误。

- [ ] **Step 2: 运行 Python 编译检查**

```bash
.venv/Scripts/python.exe -m compileall -q crawl_limetorrents.py crawl_limetorrents_by_keys.py crawl_detail_limetorrents.py init_limetorrents_db.py tests
```

Expected: exit code 0。

- [ ] **Step 3: 抓取用户提供的分类示例页**

```bash
.venv/Scripts/python.exe crawl_limetorrents.py --category Movies --start-page 2 --max-pages 1
```

Expected:

- 日志显示解析约 40 条列表记录。
- `bt_limetorrents_spider_db.bt_info_list` 出现 `source="limetorrents"` 记录。
- St Vincent 示例记录的详情 URL 以 `torrent-19859670.html` 结尾。

- [ ] **Step 4: 抓取单关键词前两页**

```bash
.venv/Scripts/python.exe crawl_limetorrents.py --keyword "St Vincent" --max-pages 2
```

Expected:

- 不写入 `/leet2/` Sponsored Links。
- 重复 St Vincent 详情记录不重复插入。
- `keywords` 包含 `St Vincent`，`discovery_modes` 同时包含已有发现方式。

- [ ] **Step 5: 详情 dry-run**

```bash
.venv/Scripts/python.exe crawl_detail_limetorrents.py --keyword "St Vincent" --limit 1 --dry-run
```

Expected:

- 输出 Hash、文件数量和 tracker 数量。
- HTML 写入 `data/html/limetorrents/<md5>.html`。
- `bt_info_list.detail_status` 不变。
- `bt_info_detail` 不写入。

- [ ] **Step 6: 详情真实写入并抽样验证**

```bash
.venv/Scripts/python.exe crawl_detail_limetorrents.py --keyword "St Vincent" --limit 1 --concurrency 1
.venv/Scripts/python.exe -c "
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_limetorrents_spider_db']
d = c.bt_info_detail.find_one({'info_hash': '700D963C82A513317703A730DD3C030E19FFAD8E'})
assert d
assert d['resource_links']['magnet'].startswith('magnet:')
assert '.torrent' in d['resource_links']['torrent']
assert d['declared_file_count'] == 3
assert len(d['files']) == d['file_entry_count']
assert len(d['trackers']) == d['tracker_count']
print(d['name'], d['info_hash'], d['file_entry_count'], d['tracker_count'])
"
```

Expected: 命令成功并打印 St Vincent 详情摘要。

- [ ] **Step 7: 验证幂等重跑**

记录两个集合数量，重新运行相同列表和详情命令，再次检查：

```bash
.venv/Scripts/python.exe -c "
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_limetorrents_spider_db']
print('list', c.bt_info_list.count_documents({}))
print('detail', c.bt_info_detail.count_documents({}))
print('done', c.bt_info_list.count_documents({'detail_status': 'done'}))
"
```

Expected: 相同 URL 不产生重复文档；已完成详情不会被列表重抓重置为 pending。

- [ ] **Step 8: 更新项目说明**

将 `CLAUDE.md` 的项目概述、运行命令、数据库、脚本名、DOM 选择器、CLI 参数和状态机更新为 LimeTorrents 事实；删除夸克网盘和 1337x 专用说明。必须保留以下运行示例：

```text
.venv/Scripts/python.exe crawl_limetorrents.py --category Movies
.venv/Scripts/python.exe crawl_limetorrents.py --keyword "St Vincent"
.venv/Scripts/python.exe crawl_limetorrents_by_keys.py --search-category all
.venv/Scripts/python.exe crawl_detail_limetorrents.py --limit 10 --concurrency 2
.venv/Scripts/python.exe init_limetorrents_db.py
```

- [ ] **Step 9: 删除已被 pytest 覆盖的旧 root smoke 文件**

确认 `tests/` 已覆盖 checkpoint、空页和签名后：

```bash
git rm test_checkpoint.py test_empty_page.py test_signature.py
```

保留 `tes_playwright_attach.py` 仅当它仍是有效的通用浏览器诊断工具；若其中含 1337x URL，则更新为用户提供的 LimeTorrents 分类 URL。

- [ ] **Step 10: 再次运行完整验证**

```bash
.venv/Scripts/python.exe -m pytest -v
.venv/Scripts/python.exe -m compileall -q crawl_limetorrents.py crawl_limetorrents_by_keys.py crawl_detail_limetorrents.py init_limetorrents_db.py tests
git status --short
```

Expected: 测试与编译通过；`git status` 只显示本任务预期文档/清理改动。

- [ ] **Step 11: 提交收口改动**

```bash
git add -A
git commit -m "文档：完成 LimeTorrents 爬虫迁移说明"
```

- [ ] **Step 12: 执行质量审查**

按顺序调用：

1. `simplify`：检查重复、可读性和效率并应用修正。
2. `superpowers:requesting-code-review`：检查正确性、错误处理和数据一致性。
3. `superpowers:verification-before-completion`：重新执行测试、编译和真实冒烟证据。
4. `superpowers:finishing-a-development-branch`：以 `--no-ff` 合并 worktree 分支。

任何审查修正都必须重新运行受影响测试并用中文提交。

---

## 规格覆盖检查

- 分类浏览、默认 Movies：Tasks 2-4、10。
- 关键词搜索、默认 all、keys.txt：Tasks 2-5、10。
- 实际 Next page、失败页不推进：Tasks 3-4。
- `bt_info_list` 幂等与状态不重置：Task 4。
- 完整 Hash、magnet、torrent、stream、tracker：Task 6。
- 完整 files 合并详情、相关 torrent、评论数：Task 7。
- CAS 状态机、retry、dry-run、force、retry-failed：Task 8。
- `DocumentTooLarge` 明确失败、不截断：Task 8。
- 只创建两个集合和必要索引：Task 9。
- 离线 fixture、全套测试、真实 Chrome/Mongo 验收：Tasks 3、6-10。
- 旧 1337x 入口直接替换并清理：Tasks 1、9-10。
