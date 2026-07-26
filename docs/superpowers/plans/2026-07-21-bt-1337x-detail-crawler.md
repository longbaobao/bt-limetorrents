# 1337x Detail Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Playwright-based crawler that fetches 1337x detail pages from URLs already collected in `bt_info_list`, parses them, and writes structured records to `bt_info_detail`. Supports batched resumable execution with per-record status tracking.

**Architecture:** Single async Python script `crawl_detail_1337x.py` that batches pending URLs from MongoDB, claims each via CAS (`findOneAndUpdate` pending→processing), runs `fetch_one` + `parse_detail` concurrently under a semaphore, and persists results. HTML cached at `data/html/<md5>.html`. Per-record status (`pending|processing|done|failed`) stored on `bt_info_list`.

**Tech Stack:** Python 3.11, Playwright (async API), pymongo, BeautifulSoup4, pytest, pytest-asyncio.

## Global Constraints

- All datetime fields stored as **`yyyy-mm-dd hh:mm:ss` strings** (not BSON datetime, not timestamps).
- DB: `bt_13337x_spider_db`. Collections: `bt_info_list` (source), `bt_info_detail` (target).
- Reuse `CDP_URL`, `MONGO_URI`, `DB_NAME`, `COLL_NAME` from `crawl_1337x.py` — do not duplicate.
- HTML cache path: `data/html/<md5(detail_url)>.html`.
- File encoding: UTF-8 throughout; set `sys.stdout.reconfigure(encoding="utf-8")` at script top.
- Browser: shared Chrome at `http://127.0.0.1:9222` (do NOT launch new instance).
- Status updates are **always single-record** `update_one`/`findOneAndUpdate`. Never `update_many` for status.
- Timeouts: `page.goto` 30s, `wait_for_selector` 30s, `run_one` total budget 60s.
- All public functions/types listed under "Interfaces" of later tasks are **exact contracts** — implement them as written.

---

## Task 1: Project Setup & Schema Migration

**Files:**
- Create: `.gitignore`
- Create: `migrate_detail_status.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `bt_info_list` documents all have `detail_status` field; `c_time` field is string (not BSON date); `bt_info_detail` collection has unique index on `detail_url`.

- [ ] **Step 1: Initialize git repo**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git init
git config user.email "dev@local"
git config user.name "dev"
```

- [ ] **Step 2: Create `.gitignore`**

Write to `D:/workspace/ai-workspace/bt-1337x/.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/html/*.html
data/html/_progress.log
!data/html/.gitkeep
!data/html/_progress.log
```

(`data/html/` contents ignored but directory kept via `.gitkeep`.)

- [ ] **Step 3: Add `.gitkeep` to keep directory**

```bash
touch D:/workspace/ai-workspace/bt-1337x/data/html/.gitkeep
```

- [ ] **Step 4: Add `pytest` and `pytest-asyncio` to `requirements.txt`**

Append to `D:/workspace/ai-workspace/bt-1337x/requirements.txt`:

```
pytest==8.4.2
pytest-asyncio==1.1.0
```

- [ ] **Step 5: Install new deps**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
uv pip install pytest==8.4.2 pytest-asyncio==1.1.0
```

Expected: `Installed 5 packages` (or similar; depends on existing dep tree).

- [ ] **Step 6: Write `migrate_detail_status.py`**

Create `D:/workspace/ai-workspace/bt-1337x/migrate_detail_status.py`:

```python
"""一次性迁移：bt_info_list 添加 detail_status 字段；c_time datetime → 字符串；
bt_info_detail 建唯一索引。幂等。"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from pymongo import MongoClient

sys.path.insert(0, ".")
from crawl_1337x import DB_NAME, MONGO_URI

COLL_LIST = "bt_info_list"
COLL_DETAIL = "bt_info_detail"


def main():
    client = MongoClient(MONGO_URI)
    coll_list = client[DB_NAME][COLL_LIST]
    coll_detail = client[DB_NAME][COLL_DETAIL]

    # 1) 添加 detail_status 字段（已存在的不动）
    r1 = coll_list.update_many(
        {"detail_status": {"$exists": False}},
        {"$set": {"detail_status": "pending"},
         "$unset": {"detail_started_at": "",
                    "detail_processed_at": "",
                    "detail_error": ""}}
    )
    print(f"[1] detail_status 字段补充: {r1.modified_count} 条")

    # 2) c_time 字段 datetime → 字符串
    r2 = coll_list.update_many(
        {"c_time": {"$type": "date"}},
        [{"$set": {"c_time": {
            "$dateToString": {"format": "%Y-%m-%d %H:%M:%S", "date": "$c_time"}
        }}}]
    )
    print(f"[2] c_time 转字符串: {r2.modified_count} 条")

    # 3) bt_info_detail 建唯一索引
    coll_detail.create_index("detail_url", unique=True)
    print(f"[3] bt_info_detail.detail_url 唯一索引已建")

    # 验证
    total = coll_list.count_documents({})
    pending = coll_list.count_documents({"detail_status": "pending"})
    sample = coll_list.find_one({}, {"detail_status": 1, "c_time": 1, "_id": 0})
    print(f"\n总: {total}, pending: {pending}")
    print(f"样本: {sample}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run migration**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python migrate_detail_status.py
```

Expected output:
```
[1] detail_status 字段补充: 1000 条
[2] c_time 转字符串: 1000 条
[3] bt_info_detail.detail_url 唯一索引已建
总: 1000, pending: 1000
样本: {'c_time': '2026-07-21 19:04:15', 'detail_status': 'pending'}
```

(If counts are 0 because migration already ran, that's also fine — script is idempotent.)

- [ ] **Step 8: Verify with pymongo**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
from pymongo import MongoClient
import sys
sys.stdout.reconfigure(encoding='utf-8')
c = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']['bt_info_list']
print('detail_status 分布:')
for s in ['pending', 'processing', 'done', 'failed']:
    print(f'  {s}: {c.count_documents({\"detail_status\": s})}')
print('c_time type:', type(c.find_one({}, {'c_time': 1, '_id': 0})['c_time']).__name__)
"
```

Expected:
```
detail_status 分布:
  pending: 1000
  processing: 0
  done: 0
  failed: 0
c_time type: str
```

- [ ] **Step 9: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add .gitignore data/html/.gitkeep requirements.txt migrate_detail_status.py
git commit -m "feat: 添加 detail_status 字段迁移脚本"
```

---

## Task 2: Pure Helpers (TDD)

**Files:**
- Create: `crawl_detail_1337x.py`
- Create: `tests/test_parse_relative_time.py`
- Create: `tests/test_extract_imdb_id.py`
- Create: `tests/test_html_cache_path.py`
- Create: `tests/test_now_str.py`

**Interfaces:**
- Produces (all in `crawl_detail_1337x.py`):
  - `parse_relative_time(s: str, ref_now: datetime) -> str`
    - Returns `yyyy-mm-dd hh:mm:ss` string, or empty string if unparseable/empty.
  - `extract_imdb_id(imdb_url: str | None) -> str | None`
    - Extracts `tt\d+` from URL; returns None if URL is None or no match.
  - `html_cache_path(detail_url: str) -> Path`
    - Returns `Path("data/html/<md5_hex>.html")`.
  - `now_str() -> str`
    - Returns `datetime.now().strftime("%Y-%m-%d %H:%M:%S")`.

- [ ] **Step 1: Write test for `parse_relative_time`**

Create `D:/workspace/ai-workspace/bt-1337x/tests/test_parse_relative_time.py`:

```python
"""测试 parse_relative_time：将 '4 years ago' 等相对时间转为 yyyy-mm-dd hh:mm:ss。"""
from datetime import datetime
import sys
sys.path.insert(0, ".")
from crawl_detail_1337x import parse_relative_time


REF = datetime(2026, 7, 21, 19, 0, 0)


def test_years_ago():
    assert parse_relative_time("4 years ago", REF) == "2022-07-21 00:00:00"


def test_hours_ago():
    assert parse_relative_time("11 hours ago", REF) == "2026-07-21 08:00:00"


def test_minutes_ago():
    assert parse_relative_time("30 minutes ago", REF) == "2026-07-21 18:30:00"


def test_days_ago():
    assert parse_relative_time("3 days ago", REF) == "2026-07-18 00:00:00"


def test_empty():
    assert parse_relative_time("", REF) == ""


def test_unparseable_returns_empty():
    assert parse_relative_time("yesterday", REF) == ""
    assert parse_relative_time("foo bar baz", REF) == ""


def test_singular_year():
    """'1 year ago' 单数也应解析"""
    assert parse_relative_time("1 year ago", REF) == "2025-07-21 00:00:00"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_parse_relative_time.py -v
```

Expected: `ModuleNotFoundError: No module named 'crawl_detail_1337x'` (or `ImportError` for `parse_relative_time`).

- [ ] **Step 3: Write minimal `crawl_detail_1337x.py` with `parse_relative_time`**

Create `D:/workspace/ai-workspace/bt-1337x/crawl_detail_1337x.py`:

```python
"""
1337x 详情页爬虫：从 bt_info_list 取 detail_url，抓 HTML 落本地，解析入库。

连接本地 9222 调试端口的 Chrome（不新开进程），复用现有 context。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

CDP_URL = "http://127.0.0.1:9222"
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "bt_13337x_spider_db"
COLL_LIST = "bt_info_list"
COLL_DETAIL = "bt_info_detail"
HTML_DIR = Path("data/html")

BATCH = 200
MAX_RETRIES = 3
RETRY_BACKOFF = (2, 4, 8)  # 秒
RUN_ONE_BUDGET = 60  # 秒


def now_str() -> str:
    """当前时间 → 'yyyy-mm-dd hh:mm:ss'。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def html_cache_path(detail_url: str) -> Path:
    """data/html/<md5_hex>.html"""
    return HTML_DIR / (hashlib.md5(detail_url.encode()).hexdigest() + ".html")


def parse_relative_time(s: str, ref_now: datetime) -> str:
    """'4 years ago' / '11 hours ago' / '30 minutes ago' / '3 days ago'
    → '<ref_now - delta>' 格式化为 'yyyy-mm-dd hh:mm:ss'。
    无法解析或空字符串返回空串。"""
    if not s:
        return ""
    s = s.strip()
    m = re.match(r"(\d+)\s+(year|years|month|months|day|days|hour|hours|minute|minutes)\s+ago", s, re.IGNORECASE)
    if not m:
        return ""
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("year"):
        delta = timedelta(days=365 * n)
    elif unit.startswith("month"):
        delta = timedelta(days=30 * n)
    elif unit.startswith("day"):
        delta = timedelta(days=n)
    elif unit.startswith("hour"):
        delta = timedelta(hours=n)
    elif unit.startswith("minute"):
        delta = timedelta(minutes=n)
    else:
        return ""
    target = ref_now - delta
    return target.strftime("%Y-%m-%d %H:%M:%S")


def extract_imdb_id(imdb_url: str | None) -> str | None:
    """从 'https://www.imdb.com/title/tt9731534' 提取 'tt9731534'。
    None 或无匹配返回 None。"""
    if not imdb_url:
        return None
    m = re.search(r"(tt\d+)", imdb_url)
    return m.group(1) if m else None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_parse_relative_time.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Write test for `extract_imdb_id`**

Create `D:/workspace/ai-workspace/bt-1337x/tests/test_extract_imdb_id.py`:

```python
"""测试 extract_imdb_id：从 imdb URL 提取 ttXXXXXXX。"""
import sys
sys.path.insert(0, ".")
from crawl_detail_1337x import extract_imdb_id


def test_normal():
    assert extract_imdb_id("https://www.imdb.com/title/tt9731534") == "tt9731534"


def test_with_query():
    assert extract_imdb_id("https://www.imdb.com/title/tt1234567/?ref_=fn") == "tt1234567"


def test_with_trailing_slash():
    assert extract_imdb_id("https://www.imdb.com/title/tt9999999/") == "tt9999999"


def test_none():
    assert extract_imdb_id(None) is None


def test_invalid():
    assert extract_imdb_id("not a url") is None
    assert extract_imdb_id("https://example.com/no/imdb/here") is None
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_extract_imdb_id.py -v
```

Expected: all 5 tests PASS (implementation already there from Step 3).

- [ ] **Step 7: Write test for `html_cache_path`**

Create `D:/workspace/ai-workspace/bt-1337x/tests/test_html_cache_path.py`:

```python
"""测试 html_cache_path：data/html/<md5>.html。"""
import sys
sys.path.insert(0, ".")
from pathlib import Path
from crawl_detail_1337x import html_cache_path


def test_returns_path_with_md5_and_html():
    url = "https://1337x.to/torrent/5006555/The-Night-House/"
    p = html_cache_path(url)
    assert isinstance(p, Path)
    assert p.parent.name == "html"
    assert p.suffix == ".html"
    # md5 hex is 32 chars
    assert len(p.stem) == 32


def test_deterministic():
    """同一 URL 多次调用返回相同路径。"""
    url = "https://1337x.to/torrent/123/"
    assert html_cache_path(url) == html_cache_path(url)


def test_different_urls_different_paths():
    assert html_cache_path("https://a/") != html_cache_path("https://b/")
```

- [ ] **Step 8: Run test to verify it passes**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_html_cache_path.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 9: Write test for `now_str`**

Create `D:/workspace/ai-workspace/bt-1337x/tests/test_now_str.py`:

```python
"""测试 now_str：返回 yyyy-mm-dd hh:mm:ss 格式。"""
import re
import sys
sys.path.insert(0, ".")
from crawl_detail_1337x import now_str


def test_format():
    s = now_str()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s), f"格式不符: {s!r}"
```

- [ ] **Step 10: Run all helper tests**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_parse_relative_time.py tests/test_extract_imdb_id.py tests/test_html_cache_path.py tests/test_now_str.py -v
```

Expected: 7 + 5 + 3 + 1 = **16 tests pass**.

- [ ] **Step 11: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add crawl_detail_1337x.py tests/
git commit -m "feat: 添加纯函数 helpers（parse_relative_time, extract_imdb_id, html_cache_path, now_str）"
```

---

## Task 3: Capture HTML Fixtures

**Files:**
- Create: `tests/fixtures/capture_fixtures.py`
- Create: `tests/fixtures/detail_night_house.html` (captured)
- Create: `tests/fixtures/detail_no_imdb.html` (captured)
- Create: `tests/fixtures/detail_minimal.html` (captured)
- Create: `tests/fixtures/detail_broken.html` (hand-crafted, for ParseError test)

**Interfaces:**
- Consumes: `crawl_1337x.py` (imports `CDP_URL`, existing list collection with `detail_url`).
- Produces: 3 real HTML files in `tests/fixtures/` (≥1 with all fields, 1 without IMDB, 1 minimal) plus 1 hand-crafted broken HTML.

- [ ] **Step 1: Write fixture capture script**

Create `D:/workspace/ai-workspace/bt-1337x/tests/fixtures/capture_fixtures.py`:

```python
"""从 bt_info_list 选 3 个有差异的详情页，访问后保存到 tests/fixtures/。
一次性脚本，跑完即可。"""
import sys
sys.path.insert(0, "../..")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

from playwright.sync_api import sync_playwright
from pymongo import MongoClient

from crawl_1337x import (
    CDP_URL, MONGO_URI, DB_NAME, COLL_LIST, html_cache_path
)

FIX_DIR = Path(__file__).parent


def main():
    coll = MongoClient(MONGO_URI)[DB_NAME][COLL_LIST]

    # 1) 优先挑 "The.Night.House"（截图里那条，所有字段齐全）
    night_house = coll.find_one({"name": {"$regex": "Night.House", "$options": "i"}})
    if not night_house:
        print("WARN: 找不到 Night.House，跳过完整 fixture")
    targets = []

    if night_house:
        targets.append(("detail_night_house.html", night_house["detail_url"]))

    # 2) 找一条没有 IMDB 的（heuristic: 详情页解析后 imdb_url 为 None，
    #    这里先用 detail_url 看起来不像电影/剧的：选无 imdb 通常是软件/游戏，
    #    但 1337x 多数有 imdb。先随机抓 3 条，捕获后人工核对）
    for doc in coll.find({"name": {"$not": {"$regex": "Night.House", "$options": "i"}}}).limit(5):
        targets.append((f"detail_sample_{doc['_id'][:8]}.html", doc["detail_url"]))

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        for name, url in targets:
            print(f"抓取 {url} -> {name}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("div.torrent-detail, div.box-info-heading", timeout=30000)
            html = page.content()
            (FIX_DIR / name).write_text(html, encoding="utf-8")
            print(f"  保存 {len(html)} 字节")
        page.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run capture script**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python tests/fixtures/capture_fixtures.py
```

Expected: prints 抓取进度和保存字节数。Saved files appear in `tests/fixtures/`.

- [ ] **Step 3: Verify captured files**

```bash
ls -la D:/workspace/ai-workspace/bt-1337x/tests/fixtures/
```

Expected: at least 3 `.html` files plus `capture_fixtures.py`.

- [ ] **Step 4: Hand-pick which files become the canonical fixtures**

Inspect each captured HTML to determine which to keep. Open one in a browser:

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
from pathlib import Path
for f in sorted(Path('tests/fixtures').glob('detail_*.html')):
    html = f.read_text(encoding='utf-8')
    has_imdb = 'imdb.com/title/tt' in html
    has_infohash = 'INFOHASH' in html.upper()
    has_resource = 'magnet:?' in html
    print(f'{f.name}: imdb={has_imdb}, infohash={has_infohash}, magnet={has_resource}, size={len(html)}')
"
```

Expected: a quick overview of what each fixture contains.

- [ ] **Step 5: Rename to canonical names**

Pick the 3 files matching the following criteria and rename:
- One with IMDB + INFOHASH + magnet → `detail_night_house.html` (or keep current)
- One without IMDB → `detail_no_imdb.html`
- One minimal (smallest file, fewest fields) → `detail_minimal.html`

```bash
cd D:/workspace/ai-workspace/bt-1337x/tests/fixtures/
# Example (adjust based on actual captured files):
mv detail_sample_abcdef01.html detail_no_imdb.html
mv detail_sample_abcdef02.html detail_minimal.html
# Delete other detail_sample_*.html files
rm detail_sample_*.html 2>/dev/null || true
ls
```

Expected: 3 fixture files with canonical names.

- [ ] **Step 6: Hand-craft `detail_broken.html` (for ParseError test)**

Create `D:/workspace/ai-workspace/bt-1337x/tests/fixtures/detail_broken.html`:

```html
<!DOCTYPE html>
<html>
<head><title>not a 1337x page</title></head>
<body><p>This page is missing all torrent-detail structure.</p></body>
</html>
```

- [ ] **Step 7: Verify fixtures**

```bash
cd D:/workspace/ai-workspace/bt-1337x
ls tests/fixtures/*.html
```

Expected: 4 fixture files (3 captured + 1 hand-crafted).

- [ ] **Step 8: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add tests/fixtures/capture_fixtures.py tests/fixtures/*.html
git commit -m "test: 添加 4 个 HTML fixture（含 3 个真实页面 + 1 个手写损坏页）"
```

---

## Task 4: parse_detail + ParseError (TDD)

**Files:**
- Modify: `crawl_detail_1337x.py` (add `ParseError`, `parse_detail`)
- Create: `tests/test_parse_detail.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces (in `crawl_detail_1337x.py`):
  - `class ParseError(Exception)` — raised when detail page structure unrecognizable.
  - `parse_detail(html: str, detail_url: str) -> dict`
    - Returns dict matching schema in spec section "bt_info_detail".
    - Raises `ParseError` if essential structure missing (no `div.torrent-detail` or `div.box-info-heading`).

- [ ] **Step 1: Write `conftest.py` with fixture paths**

Create `D:/workspace/ai-workspace/bt-1337x/tests/conftest.py`:

```python
"""共享 fixtures：HTML 文件路径。"""
from pathlib import Path

FIX_DIR = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX_DIR / name).read_text(encoding="utf-8")
```

- [ ] **Step 2: Write failing test for `parse_detail` (full page)**

Create `D:/workspace/ai-workspace/bt-1337x/tests/test_parse_detail.py`:

```python
"""测试 parse_detail：HTML → 结构化字典。"""
import pytest
import sys
sys.path.insert(0, ".")
from crawl_detail_1337x import parse_detail, ParseError
from conftest import fixture


NIGHT_HOUSE_URL = "https://1337x.to/torrent/5006555/The-Night-House-2021-720p-AMZN-WEBRip-800MB-x264-GalaxyRG-TGx/"


def test_full_page_basic_fields():
    """完整页面：title, genre, magnet, infohash, imdb 都有"""
    html = fixture("detail_night_house.html")
    d = parse_detail(html, NIGHT_HOUSE_URL)
    assert d["detail_url"] == NIGHT_HOUSE_URL
    assert d["title"] == "THE NIGHT HOUSE"
    assert d["genre"] == "HORROR THRILLER"
    assert d["category"] == "Movies"
    assert d["language"] == "English"
    assert isinstance(d["seeders"], int) and d["seeders"] > 0
    assert isinstance(d["leechers"], int) and d["leechers"] >= 0
    assert d["info_hash"] and len(d["info_hash"]) >= 32
    assert d["imdb_id"] is not None
    assert d["imdb_id"].startswith("tt")


def test_full_page_resource_links():
    """完整页面：resource_links 是嵌套 dict，至少 magnet 字段"""
    html = fixture("detail_night_house.html")
    d = parse_detail(html, NIGHT_HOUSE_URL)
    assert "magnet" in d["resource_links"]
    assert d["resource_links"]["magnet"].startswith("magnet:")


def test_full_page_tags_array():
    """tags 是字符串数组"""
    html = fixture("detail_night_house.html")
    d = parse_detail(html, NIGHT_HOUSE_URL)
    assert isinstance(d["tags"], list)
    assert len(d["tags"]) > 0
    assert all(isinstance(t, str) for t in d["tags"])


def test_full_page_date_format():
    """date_uploaded / last_checked 是 yyyy-mm-dd hh:mm:ss 字符串"""
    html = fixture("detail_night_house.html")
    d = parse_detail(html, NIGHT_HOUSE_URL)
    import re
    fmt = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
    if d["date_uploaded"]:
        assert re.match(fmt, d["date_uploaded"]), f"date_uploaded: {d['date_uploaded']!r}"
    if d["last_checked"]:
        assert re.match(fmt, d["last_checked"]), f"last_checked: {d['last_checked']!r}"


def test_full_page_c_time_present():
    html = fixture("detail_night_house.html")
    d = parse_detail(html, NIGHT_HOUSE_URL)
    assert "c_time" in d
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", d["c_time"])


def test_no_imdb():
    """无 IMDB 时 imdb_url 和 imdb_id 都是 None"""
    html = fixture("detail_no_imdb.html")
    d = parse_detail(html, "https://1337x.to/torrent/xxx/")
    assert d["imdb_url"] is None
    assert d["imdb_id"] is None


def test_minimal_page_no_crash():
    """最小字段页面也不崩（title / magnet 至少要有）"""
    html = fixture("detail_minimal.html")
    d = parse_detail(html, "https://1337x.to/torrent/xxx/")
    assert d["title"]  # 至少有 title
    assert d["detail_url"]


def test_broken_page_raises():
    """完全损坏的页面抛 ParseError"""
    html = fixture("detail_broken.html")
    with pytest.raises(ParseError):
        parse_detail(html, "https://1337x.to/torrent/xxx/")
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_parse_detail.py -v
```

Expected: `ImportError: cannot import name 'parse_detail' from 'crawl_detail_1337x'`.

- [ ] **Step 4: Implement `ParseError` and `parse_detail`**

Append to `D:/workspace/ai-workspace/bt-1337x/crawl_detail_1337x.py`:

```python
from bs4 import BeautifulSoup


class ParseError(Exception):
    """详情页结构无法识别时抛出。被 run_one 捕获并标 failed。"""


def _text(row) -> str:
    """从 bs4 tag 提取 strip 后的文本。row 可能是 None。"""
    return row.get_text(" ", strip=True) if row else ""


def parse_detail(html: str, detail_url: str) -> dict:
    """1337x 详情页 HTML → 结构化字典。

    关键 DOM 选择器（1337x 当前 DOM）：
      ul.list li      - 元数据行（左列）
      ul.list   - 同一 ul 里的右列（按出现顺序匹配）
      div.torrent-info ul li - 标签
      a[href*='magnet:'] - magnet 链接
      a[href*='imdb.com/title/'] - IMDB 链接
      h1 (页面主标题) - 资源名
      div.torrent-desc div - 描述
      div.torrent-rating - 评分（可能为空）
    """
    soup = BeautifulSoup(html, "html.parser")

    # 基本结构校验
    if not soup.select_one("div.torrent-detail, div.box-info-heading"):
        raise ParseError(f"详情页结构无法识别: {detail_url}")

    # 标题
    title_el = soup.select_one("div.torrent-detail h1, h1")
    title = _text(title_el)

    # 元数据块：1337x 用 ul.list > li 形式，左列 + 右列交替
    meta = {}
    rows = soup.select("div.torrent-detail ul.list li")
    for li in rows:
        spans = li.select("span")
        if len(spans) >= 2:
            key = spans[0].get_text(strip=True).rstrip(":").strip()
            val = spans[1].get_text(strip=True)
            meta[key.lower()] = val

    # 类型映射
    seeders = int(meta.get("seeders", "0") or 0) if meta.get("seeders", "0").isdigit() else 0
    leechers = int(meta.get("leechers", "0") or 0) if meta.get("leechers", "0").isdigit() else 0
    downloads = int(meta.get("downloads", "0") or 0) if meta.get("downloads", "0").isdigit() else 0

    # 相对时间 → 绝对时间
    ref_now = datetime.now()
    date_uploaded = parse_relative_time(meta.get("date uploaded", ""), ref_now)
    last_checked = parse_relative_time(meta.get("last checked", ""), ref_now)

    # 标签
    tag_links = soup.select("div.torrent-info ul li a")
    tags = [a.get_text(strip=True) for a in tag_links if a.get_text(strip=True)]

    # INFOHASH — 1337x 显示为 "INFOHASH: <hash>"
    info_hash = ""
    for el in soup.select("div.torrent-info"):
        text = el.get_text(" ", strip=True)
        m = re.search(r"INFOHASH[:\s]+([A-Fa-f0-9]{32,40})", text)
        if m:
            info_hash = m.group(1)
            break

    # 资源链接
    resource_links = {}
    magnet_a = soup.select_one("a[href^='magnet:']")
    if magnet_a:
        resource_links["magnet"] = magnet_a["href"]
    torrent_a = soup.select_one("a[href*='/torrent/'][href$='/torrent/']")  # 1337x 自己的 .torrent 下载
    if torrent_a and torrent_a.get("href"):
        resource_links["torrent"] = torrent_a["href"]
    # 镜像
    for mirror_name, keyword in [
        ("itorrents", "itorrents.org"),
        ("torrage", "torrage.info"),
        ("btcache", "btcache.me"),
    ]:
        m = soup.select_one(f"a[href*='{keyword}']")
        if m and m.get("href"):
            resource_links[mirror_name] = m["href"]
    # Stream (PLAY NOW)
    stream_a = soup.select_one("a.stream, a[href*='stream'], a.btn-stream")
    if stream_a and stream_a.get("href"):
        resource_links["stream"] = stream_a["href"]

    # IMDB
    imdb_a = soup.select_one("a[href*='imdb.com/title/']")
    imdb_url = imdb_a["href"] if imdb_a else None
    imdb_id = extract_imdb_id(imdb_url)

    # Cover / Genre / Description / Rating
    cover_a = soup.select_one("div.torrent-detail img, img.poster")
    cover_url = cover_a["src"] if cover_a and cover_a.get("src") else None

    genre_el = soup.select_one("div.torrent-detail p strong, div.torrent-detail .genre")
    genre = _text(genre_el).replace("&nbsp;", " ").strip()

    desc_el = soup.select_one("div.torrent-desc, div#description")
    description = _text(desc_el)

    rating = None
    rating_el = soup.select_one("div.torrent-rating")
    if rating_el:
        # 1337x 显示如 "5.0" 或 "5"
        rt = _text(rating_el)
        try:
            rating = int(float(rt))
        except ValueError:
            rating = None

    # 相关站点
    related_sites = []
    for a in soup.select("div.torrent-detail a[href^='http']"):
        href = a.get("href", "")
        if "imdb.com" in href or "1337x.to" in href:
            continue
        name = _text(a)
        if name and href:
            related_sites.append({"name": name, "url": href})

    return {
        "_id": hashlib.md5(detail_url.encode()).hexdigest(),
        "detail_url": detail_url,
        "name": title,
        "category": meta.get("category", ""),
        "type": meta.get("type", ""),
        "language": meta.get("language", ""),
        "total_size": meta.get("total size", ""),
        "uploaded_by": meta.get("uploaded by", ""),
        "downloads": downloads,
        "last_checked": last_checked,
        "date_uploaded": date_uploaded,
        "seeders": seeders,
        "leechers": leechers,
        "resource_links": resource_links,
        "cover_url": cover_url,
        "title": title,
        "genre": genre,
        "description": description,
        "rating": rating,
        "tags": tags,
        "info_hash": info_hash,
        "imdb_url": imdb_url,
        "imdb_id": imdb_id,
        "related_sites": related_sites,
        "c_time": now_str(),
        "source": "1337x",
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/test_parse_detail.py -v
```

Expected: all 8 tests PASS. (If a few fail due to fixture DOM variations, adjust parser selectors in `parse_detail` to match actual DOM structure — re-run until all green.)

- [ ] **Step 6: Run full test suite**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
pytest tests/ -v
```

Expected: 16 + 8 = **24 tests pass**.

- [ ] **Step 7: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add crawl_detail_1337x.py tests/test_parse_detail.py tests/conftest.py
git commit -m "feat: 实现 parse_detail 解析器 + ParseError 异常"
```

---

## Task 5: DB Layer

**Files:**
- Modify: `crawl_detail_1337x.py` (add DB functions)

**Interfaces:**
- Produces (in `crawl_detail_1337x.py`):
  - `claim_one(coll_list, doc_id) -> dict | None`
    - CAS `findOneAndUpdate({_id: id, detail_status: "pending"}, {$set: {detail_status: "processing", detail_started_at: now_str()}}, return_document=AFTER)`
    - Returns claimed doc, or None if not pending (someone else claimed).
  - `mark_done(coll_list, doc_id) -> None`
    - `update_one({_id: id}, {$set: {detail_status: "done", detail_processed_at: now_str()}, $unset: {detail_started_at: "", detail_error: ""}})`
  - `mark_failed(coll_list, doc_id, error_msg: str) -> None`
    - `update_one({_id: id}, {$set: {detail_status: "failed", detail_processed_at: now_str(), detail_error: error_msg}, $unset: {detail_started_at: ""}})`
  - `upsert_detail(coll_detail, doc: dict) -> None`
    - `replace_one({_id: doc["_id"]}, doc, upsert=True)`
  - `rescue_orphaned_processing(coll_list) -> int`
    - `update_many({detail_status: "processing"}, {$set: {detail_status: "pending"}, $unset: {detail_started_at: ""}})`
    - Returns modified_count.

- [ ] **Step 1: Append DB layer functions to `crawl_detail_1337x.py`**

Append to `D:/workspace/ai-workspace/bt-1337x/crawl_detail_1337x.py`:

```python
from pymongo import ReturnDocument


def claim_one(coll_list, doc_id: str) -> dict | None:
    """CAS: 把 pending 的 doc 抢占为 processing。返回 claimed 文档；被抢走返回 None。"""
    claimed = coll_list.find_one_and_update(
        {"_id": doc_id, "detail_status": "pending"},
        {"$set": {"detail_status": "processing", "detail_started_at": now_str()}},
        return_document=ReturnDocument.AFTER,
    )
    return claimed


def mark_done(coll_list, doc_id: str) -> None:
    """成功完成 → done。"""
    coll_list.update_one(
        {"_id": doc_id},
        {"$set": {"detail_status": "done", "detail_processed_at": now_str()},
         "$unset": {"detail_started_at": "", "detail_error": ""}},
    )


def mark_failed(coll_list, doc_id: str, error_msg: str) -> None:
    """失败 → failed。保留 error 信息供排查。"""
    coll_list.update_one(
        {"_id": doc_id},
        {"$set": {"detail_status": "failed",
                  "detail_processed_at": now_str(),
                  "detail_error": error_msg},
         "$unset": {"detail_started_at": ""}},
    )


def upsert_detail(coll_detail, doc: dict) -> None:
    """覆盖或插入详情文档。"""
    coll_detail.replace_one({"_id": doc["_id"]}, doc, upsert=True)


def rescue_orphaned_processing(coll_list) -> int:
    """启动时恢复：卡在 processing 的孤儿 → pending。返回恢复数量。"""
    r = coll_list.update_many(
        {"detail_status": "processing"},
        {"$set": {"detail_status": "pending"},
         "$unset": {"detail_started_at": ""}},
    )
    return r.modified_count
```

- [ ] **Step 2: Smoke test DB layer**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pymongo import MongoClient
from crawl_detail_1337x import (
    MONGO_URI, DB_NAME, COLL_LIST, COLL_DETAIL,
    claim_one, mark_done, mark_failed, upsert_detail, rescue_orphaned_processing
)

coll_list = MongoClient(MONGO_URI)[DB_NAME][COLL_LIST]
coll_detail = MongoClient(MONGO_URI)[DB_NAME][COLL_DETAIL]

# 1) rescue
n = rescue_orphaned_processing(coll_list)
print(f'rescued: {n}')

# 2) claim a doc
doc = coll_list.find_one({'detail_status': 'pending'})
test_id = doc['_id']
print(f'test doc: {test_id[:12]}...')
claimed = claim_one(coll_list, test_id)
assert claimed is not None, 'claim failed'
assert claimed['detail_status'] == 'processing'
print('claim_one OK')

# 3) claim same again → None (already processing)
again = claim_one(coll_list, test_id)
assert again is None, 'second claim should fail'
print('CAS conflict OK')

# 4) mark_done
mark_done(coll_list, test_id)
after = coll_list.find_one({'_id': test_id})
assert after['detail_status'] == 'done'
print('mark_done OK')

# 5) mark_failed (pick another)
doc2 = coll_list.find_one({'detail_status': 'pending'})
claim_one(coll_list, doc2['_id'])
mark_failed(coll_list, doc2['_id'], 'test error')
after2 = coll_list.find_one({'_id': doc2['_id']})
assert after2['detail_status'] == 'failed'
assert after2['detail_error'] == 'test error'
print('mark_failed OK')

# 6) upsert_detail
detail_doc = {'_id': test_id, 'test': True, 'detail_url': doc['detail_url']}
upsert_detail(coll_detail, detail_doc)
got = coll_detail.find_one({'_id': test_id})
assert got['test'] is True
# cleanup
coll_detail.delete_one({'_id': test_id})
print('upsert_detail OK')

# cleanup: reset test docs
coll_list.update_one({'_id': test_id}, {'\$set': {'detail_status': 'pending'},
                                         '\$unset': {'detail_started_at': '', 'detail_processed_at': '', 'detail_error': ''}})
coll_list.update_one({'_id': doc2['_id']}, {'\$set': {'detail_status': 'pending'},
                                             '\$unset': {'detail_started_at': '', 'detail_processed_at': '', 'detail_error': ''}})
print('cleanup OK')
"
```

Expected:
```
rescued: 0
test doc: <md5_prefix>...
claim_one OK
CAS conflict OK
mark_done OK
mark_failed OK
upsert_detail OK
cleanup OK
```

(Replace `\$` with `$` if running directly in shell; the `\$` is for escaping in the inline Python.)

- [ ] **Step 3: Verify list state unchanged**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']['bt_info_list']
print('pending:', c.count_documents({'detail_status': 'pending'}))
print('done:', c.count_documents({'detail_status': 'done'}))
print('failed:', c.count_documents({'detail_status': 'failed'}))
"
```

Expected:
```
pending: 1000
done: 0
failed: 0
```

(The smoke test cleaned up the two test docs back to `pending`.)

- [ ] **Step 4: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add crawl_detail_1337x.py
git commit -m "feat: 添加 DB 层（claim_one, mark_done/failed, upsert_detail, rescue）"
```

---

## Task 6: Browser Layer

**Files:**
- Modify: `crawl_detail_1337x.py` (add browser/cache functions)

**Interfaces:**
- Produces (in `crawl_detail_1337x.py`):
  - `async fetch_one(page, url: str) -> str`
    - `page.goto(url, timeout=30000, wait_until="domcontentloaded")`
    - `await page.wait_for_selector("div.torrent-detail, div.box-info-heading", timeout=30000)`
    - Returns `await page.content()`.
    - Raises `playwright.async_api.TimeoutError` on timeout (caught by `run_one`).
  - `save_html_cache(detail_url: str, html: str) -> None`
    - Writes `html_cache_path(detail_url)` with UTF-8 encoding.
    - Creates `HTML_DIR` if missing.

- [ ] **Step 1: Append browser/cache functions**

Append to `D:/workspace/ai-workspace/bt-1337x/crawl_detail_1337x.py`:

```python
from playwright.async_api import async_playwright, TimeoutError as PWTimeout


async def fetch_one(page, url: str) -> str:
    """访问详情页并返回 HTML 字符串。超时抛 PWTimeout。"""
    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_selector(
        "div.torrent-detail, div.box-info-heading", timeout=30000
    )
    return await page.content()


def save_html_cache(detail_url: str, html: str) -> None:
    """写本地 HTML 缓存。HTML_DIR 不存在则建。"""
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    path = html_cache_path(detail_url)
    path.write_text(html, encoding="utf-8")
```

- [ ] **Step 2: Smoke test browser layer (1 URL)**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
import sys, asyncio
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from playwright.async_api import async_playwright
from crawl_detail_1337x import (
    CDP_URL, fetch_one, save_html_cache, parse_detail, COLL_LIST, COLL_DETAIL
)
from pymongo import MongoClient

async def main():
    coll = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']['bt_info_list']
    doc = coll.find_one({'name': {'\$regex': 'Night.House', '\$options': 'i'}})
    url = doc['detail_url']
    print(f'URL: {url}')
    async with async_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_URL)
        ctx = b.contexts[0]
        page = ctx.new_page()
        html = await fetch_one(page, url)
        print(f'fetched {len(html)} bytes')
        save_html_cache(url, html)
        path = sys.path[0] and (__import__('pathlib').Path('data/html') / (url.encode().__hash__() and '') )
        # 简单验证
        from pathlib import Path
        from crawl_detail_1337x import html_cache_path
        cache = html_cache_path(url)
        assert cache.exists(), f'cache missing: {cache}'
        print(f'cached at: {cache}')
        # 解析
        d = parse_detail(html, url)
        print(f'parsed title: {d[\"title\"]}')
        print(f'magnet starts: {d[\"resource_links\"].get(\"magnet\", \"(none)\")[:40]}')
        await page.close()

asyncio.run(main())
"
```

Expected:
```
URL: https://1337x.to/torrent/5006555/...
fetched <N> bytes
cached at: data/html/<md5>.html
parsed title: THE NIGHT HOUSE
magnet starts: magnet:?xt=urn:btih:...
```

(Replace `\$` → `$` if pasting directly into shell.)

- [ ] **Step 3: Verify cache file**

```bash
ls D:/workspace/ai-workspace/bt-1337x/data/html/
```

Expected: at least one `<md5>.html` file.

- [ ] **Step 4: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add crawl_detail_1337x.py
git commit -m "feat: 添加 async fetch_one + save_html_cache"
```

---

## Task 7: Orchestration, main() & End-to-End

**Files:**
- Modify: `crawl_detail_1337x.py` (add run_one, run_batch, main, argparse)

**Interfaces:**
- Produces (in `crawl_detail_1337x.py`):
  - `async run_one(page, doc, coll_list, coll_detail) -> "done" | "failed"`
    - Retry loop (MAX_RETRIES times) with `RETRY_BACKOFF` seconds.
    - On success: `save_html_cache`, `upsert_detail`, `mark_done`. Returns "done".
    - On exhausted retries: `mark_failed`. Returns "failed".
    - `ParseError` → no retry, immediate mark_failed.
  - `async run_batch(pages: list, docs: list, coll_list, coll_detail) -> tuple[int, int]`
    - Creates one page per claimed doc.
    - `await asyncio.gather(*[run_one(p, d, ...) for p, d in zip(pages, docs)])`
    - Returns `(done_count, failed_count)`.
  - `async main() -> None`
    - argparse (see CLI args below).
    - Connect Playwright via CDP.
    - Call `rescue_orphaned_processing` once.
    - Loop: query pending cursor, claim batch, run_batch, log progress.
    - Honor `--limit`, `--keyword`, `--force`, `--dry-run`.
  - CLI args (argparse):
    - `-c / --concurrency` (int, default 4)
    - `-b / --batch` (int, default 200)
    - `-p / --pace` (float, default 1.0)
    - `-l / --limit` (int, default 0 = unlimited)
    - `-k / --keyword` (str, default None = all)
    - `--force` (store_true)
    - `--dry-run` (store_true)

- [ ] **Step 1: Append orchestration and main**

Append to `D:/workspace/ai-workspace/bt-1337x/crawl_detail_1337x.py`:

```python
import asyncio
import argparse
import time
import logging
from playwright.async_api import PlaywrightError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def run_one(page, doc: dict, coll_list, coll_detail, dry_run: bool = False) -> str:
    """处理单条：fetch + parse + save。返回 'done' 或 'failed'。

    - PWTimeout / PlaywrightError → 重试 MAX_RETRIES 次
    - ParseError → 不重试，直接 failed
    - 其他异常 → 重试 1 次
    """
    doc_id = doc["_id"]
    url = doc["detail_url"]
    last_err = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            html = await fetch_one(page, url)
            save_html_cache(url, html)
            parsed = parse_detail(html, url)
            if not dry_run:
                upsert_detail(coll_detail, parsed)
                mark_done(coll_list, doc_id)
            return "done"
        except PWTimeout as e:
            last_err = f"PWTimeout: {e}"
            logger.warning(f"[{doc_id[:8]}] attempt {attempt}/{MAX_RETRIES} timeout")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF[attempt - 1])
        except PlaywrightError as e:
            last_err = f"PlaywrightError: {e}"
            logger.warning(f"[{doc_id[:8]}] attempt {attempt}/{MAX_RETRIES} browser err")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF[attempt - 1])
        except ParseError as e:
            last_err = f"ParseError: {e}"
            logger.error(f"[{doc_id[:8]}] parse failed: {e}")
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.warning(f"[{doc_id[:8]}] attempt {attempt}/{MAX_RETRIES} unknown: {last_err}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF[attempt - 1])

    if not dry_run:
        mark_failed(coll_list, doc_id, last_err or "unknown")
    return "failed"


async def run_batch(ctx, docs: list, coll_list, coll_detail, concurrency: int, dry_run: bool) -> tuple[int, int]:
    """开 N 个 page 并行跑一批 docs。返回 (done, failed)。"""
    sem = asyncio.Semaphore(concurrency)

    async def one(doc):
        async with sem:
            page = await ctx.new_page()
            try:
                return await run_one(page, doc, coll_list, coll_detail, dry_run=dry_run)
            finally:
                await page.close()

    results = await asyncio.gather(*[one(d) for d in docs], return_exceptions=True)
    done = sum(1 for r in results if r == "done")
    failed = sum(1 for r in results if r == "failed")
    exceptions = sum(1 for r in results if isinstance(r, Exception))
    if exceptions:
        logger.error(f"  {exceptions} tasks raised exceptions")
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"  exc: {type(r).__name__}: {r}")
    return done, failed


def parse_args():
    p = argparse.ArgumentParser(description="1337x 详情页爬虫")
    p.add_argument("-c", "--concurrency", type=int, default=4, help="并行 page 数")
    p.add_argument("-b", "--batch", type=int, default=BATCH, help="每批从 MongoDB 取多少条")
    p.add_argument("-p", "--pace", type=float, default=1.0, help="批次间停顿秒数")
    p.add_argument("-l", "--limit", type=int, default=0, help="最多处理多少条（0=不限）")
    p.add_argument("-k", "--keyword", type=str, default=None, help="只处理指定 keyword")
    p.add_argument("--force", action="store_true", help="无视 status 强制重跑")
    p.add_argument("--dry-run", action="store_true", help="只解析不写")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    client = MongoClient(MONGO_URI)
    coll_list = client[DB_NAME][COLL_LIST]
    coll_detail = client[DB_NAME][COLL_DETAIL]

    # --force: 重置全部为 pending
    if args.force:
        r = coll_list.update_many(
            {},
            {"$set": {"detail_status": "pending"},
             "$unset": {"detail_started_at": "",
                        "detail_processed_at": "",
                        "detail_error": ""}},
        )
        logger.info(f"--force: 重置 {r.modified_count} 条 → pending")

    # 启动恢复孤儿
    rescued = rescue_orphaned_processing(coll_list)
    if rescued:
        logger.info(f"恢复 {rescued} 条卡在 processing 的孤儿")

    # 构造 query
    query: dict = {"detail_status": "pending"}
    if args.keyword:
        query["keyword"] = args.keyword

    total_done = 0
    total_failed = 0
    total_processed = 0

    async with async_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]

        batch_idx = 0
        while True:
            # 分批取
            cursor = coll_list.find(query).sort("_id").limit(args.batch)
            batch = []
            for doc in cursor:
                if args.limit and total_processed >= args.limit:
                    break
                claimed = claim_one(coll_list, doc["_id"])
                if claimed:
                    batch.append(claimed)

            if not batch:
                logger.info("没有更多 pending 记录，退出")
                break

            batch_idx += 1
            t0 = time.time()
            logger.info(f"[batch {batch_idx}] 拿到 {len(batch)} 条，开始处理")
            done, failed = await run_batch(ctx, batch, coll_list, coll_detail,
                                           args.concurrency, args.dry_run)
            elapsed = time.time() - t0
            total_done += done
            total_failed += failed
            total_processed += len(batch)

            # 进度日志
            log_line = f"[batch {batch_idx}] done={done} failed={failed} elapsed={elapsed:.1f}s"
            logger.info(log_line)
            (HTML_DIR / "_progress.log").open("a", encoding="utf-8").write(
                log_line + "\n"
            )

            if args.limit and total_processed >= args.limit:
                logger.info(f"达到 --limit={args.limit}，停止")
                break

            time.sleep(args.pace)

    logger.info(
        f"完成。done={total_done} failed={total_failed} "
        f"total={total_processed}"
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Dry run with --limit 5**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python crawl_detail_1337x.py --limit 5 --concurrency 2 --dry-run
```

Expected: 5 docs processed, no DB writes, HTML files saved to `data/html/`. Logs show `[batch 1] done=5 failed=0 elapsed=...s`.

- [ ] **Step 3: Verify dry-run side effects**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']['bt_info_list']
print('pending:', c.count_documents({'detail_status': 'pending'}))
print('done:', c.count_documents({'detail_status': 'done'}))
print('failed:', c.count_documents({'detail_status': 'failed'}))
import pathlib
print('cache files:', len(list(pathlib.Path('data/html').glob('*.html'))))
"
```

Expected: `pending: 1000` (dry-run 不改状态); `done: 0`; `failed: 0`; cache files > 0.

- [ ] **Step 4: Real run with --limit 10**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python crawl_detail_1337x.py --limit 10 --concurrency 2
```

Expected: 10 docs done, status updated, `bt_info_detail` populated.

- [ ] **Step 5: Verify DB state**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']
print('bt_info_list pending:', c.bt_info_list.count_documents({'detail_status': 'pending'}))
print('bt_info_list done:', c.bt_info_list.count_documents({'detail_status': 'done'}))
print('bt_info_detail total:', c.bt_info_detail.count_documents({}))
d = c.bt_info_detail.find_one({})
if d:
    print('sample fields:', sorted(d.keys())[:15])
    print('sample title:', d.get('title'))
    print('magnet:', d.get('resource_links', {}).get('magnet', '(none)')[:50])
    print('imdb_id:', d.get('imdb_id'))
"
```

Expected:
```
bt_info_list pending: 990
bt_info_list done: 10
bt_info_detail total: 10
sample fields: ['_id', 'category', 'c_time', 'cover_url', 'date_uploaded', 'description', 'detail_url', 'downloads', 'genre', 'imdb_id', 'imdb_url', 'info_hash', 'language', 'last_checked', 'leechers']
sample title: <actual title>
magnet: magnet:?xt=urn:btih:...
imdb_id: tt...
```

- [ ] **Step 6: Test resume (run again, should skip done)**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python crawl_detail_1337x.py --limit 10 --concurrency 2
```

Expected: prints "没有更多 pending 记录，退出" immediately (because all 10 are now `done`).

- [ ] **Step 7: Run full 1000 (background, big task)**

Run full crawl in background. Estimate: ~15-20 min for 1000 records at concurrency 4.

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
nohup python crawl_detail_1337x.py --concurrency 4 > _full_run.log 2>&1 &
echo "PID: $!"
```

Expected: returns immediately with a PID. Logs accumulate in `_full_run.log`.

- [ ] **Step 8: Monitor progress (while running)**

```bash
cd D:/workspace/ai-workspace/bt-1337x
tail -f _full_run.log
# Ctrl-C to stop tailing (does not stop the script)
```

Expected: periodic `[batch N] done=X failed=Y elapsed=Ts` lines.

- [ ] **Step 9: Wait for completion and check final state**

```bash
# Wait for python crawl_detail_1337x.py process to finish
while pgrep -f "crawl_detail_1337x.py" > /dev/null; do sleep 30; echo "still running..."; done
echo "done"
tail -20 D:/workspace/ai-workspace/bt-1337x/_full_run.log
```

Expected:
```
done
...
完成。done=950 failed=50 total=1000
```
(Numbers approximate; some failures expected due to network/rate limiting.)

- [ ] **Step 10: Verify final state**

```bash
cd D:/workspace/ai-workspace/bt-1337x
source .venv/Scripts/activate
python -c "
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']
for s in ['pending', 'processing', 'done', 'failed']:
    print(f'bt_info_list.{s}:', c.bt_info_list.count_documents({'detail_status': s}))
print('bt_info_detail total:', c.bt_info_detail.count_documents({}))

# 抽样 3 条详情
print('\\n详情样例:')
for d in c.bt_info_detail.aggregate([{'\$sample': {'size': 3}}]):
    print(f'  {d[\"title\"]} | {d[\"category\"]} | {d[\"date_uploaded\"]} | se/le={d[\"seeders\"]}/{d[\"leechers\"]}')
    print(f'    imdb={d[\"imdb_id\"]} tags={d[\"tags\"][:5]}...')
    print(f'    magnet={d[\"resource_links\"].get(\"magnet\", \"(none)\")[:60]}...')
"
```

Expected: list状态按预期分布；`bt_info_detail` ≈ done 数；详情文档字段齐全。

- [ ] **Step 11: Cleanup (remove test artifacts)**

```bash
cd D:/workspace/ai-workspace/bt-1337x
rm _full_run.log 2>/dev/null || true
# Reset the 10 dry-run + 10 limit-test docs back to pending (optional, for clean re-run test)
source .venv/Scripts/activate
python -c "
from pymongo import MongoClient
c = MongoClient('mongodb://localhost:27017/')['bt_13337x_spider_db']
# (Skip if you want to keep results; only run if you want a clean slate)
# c.bt_info_list.update_many({'detail_status': 'done'}, {'\$set': {'detail_status': 'pending'}, '\$unset': {'detail_started_at': '', 'detail_processed_at': '', 'detail_error': ''}})
# c.bt_info_detail.drop()
print('done')
"
```

- [ ] **Step 12: Commit**

```bash
cd D:/workspace/ai-workspace/bt-1337x
git add crawl_detail_1337x.py
git commit -m "feat: 添加 run_one / run_batch / main() + argparse + 端到端验证"
```

---

## Self-Review

**Spec coverage check:**

- ✅ Batched querying (cursor + limit + sort) — Task 7 main loop
- ✅ Per-record status updates — Tasks 5, 7
- ✅ `bt_info_list` schema additions — Task 1 migrate
- ✅ `bt_info_detail` schema — Task 4 parse_detail
- ✅ `c_time` string conversion — Task 1 migrate
- ✅ HTML cache at `data/html/<md5>.html` — Task 6
- ✅ Parsed as typed nested object — Task 4 resource_links
- ✅ Date format `yyyy-mm-dd hh:mm:ss` — Tasks 2, 4
- ✅ Skip already-processed on re-run — Task 5 claim_one + Task 7 main loop
- ✅ `--force` to reset all — Task 7 argparse + main
- ✅ `--dry-run` — Task 7
- ✅ Configurable concurrency — Task 7 argparse
- ✅ Retry with backoff — Task 7 run_one
- ✅ Failed → status only, no separate dead-letter — Tasks 5, 7
- ✅ Crash recovery (orphan processing) — Tasks 5, 7
- ✅ Playwright timeouts (30s goto/wait, 60s total) — Task 6 fetch_one + Task 7 run_one wrapper (note: total budget not explicitly enforced with asyncio.wait_for — gap below)
- ✅ Progress log to `_progress.log` — Task 7
- ✅ Tests for pure functions — Tasks 2, 4
- ✅ Fixtures — Task 3

**Gap found**: spec says `run_one` 60s total budget via `asyncio.wait_for`. Current impl in Task 7 doesn't enforce that. Fix in Task 7 implementation:

```python
# Wrap run_one call inside run_batch's `one`:
async def one(doc):
    async with sem:
        page = await ctx.new_page()
        try:
            return await asyncio.wait_for(
                run_one(page, doc, coll_list, coll_detail, dry_run=dry_run),
                timeout=RUN_ONE_BUDGET,
            )
        except asyncio.TimeoutError:
            mark_failed(coll_list, doc["_id"], f"run_one exceeded {RUN_ONE_BUDGET}s budget")
            return "failed"
        finally:
            await page.close()
```

This wraps the `run_one` call inside `run_batch` (Task 7) — adjust Step 1 if needed.

**Placeholder scan**: No "TBD" / "TODO" / "implement later" found.

**Type consistency**: All function signatures used in later tasks match definitions in earlier tasks. `claim_one`, `mark_done`, `mark_failed`, `upsert_detail`, `rescue_orphaned_processing`, `fetch_one`, `save_html_cache`, `parse_detail`, `html_cache_path`, `extract_imdb_id`, `parse_relative_time`, `now_str` all consistent.

**Apply the `asyncio.wait_for` fix above before implementing Task 7 Step 1.**