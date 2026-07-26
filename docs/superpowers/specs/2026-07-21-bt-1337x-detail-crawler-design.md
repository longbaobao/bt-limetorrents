# 1337x 详情页爬虫设计

**日期**：2026-07-21
**状态**：已批准，待实施
**关联**：`crawl_1337x.py`（列表爬虫，已存在）

## 背景

`bt_13337x_spider_db.bt_info_list` 现有 1000 条搜索结果记录，每条带 `detail_url` 指向 1337x 详情页。本设计新增一个详情爬虫脚本，把详情页内容（资源元数据、下载链接、标签、INFOHASH、IMDB 等）抓下来、解析、入库。

## 目标

- 从 `bt_info_list` 批量取 `detail_url`，抓取详情页 HTML 落本地 `data/html/<md5>.html`
- 解析详情页为结构化文档，写入新集合 `bt_13337x_spider_db.bt_info_detail`
- 在 `bt_info_list` 上记录每条 URL 的处理状态（per-record 更新，非批量）
- 支持重跑：跳过已处理的、修复 parser 后可强制重跑

## 非目标

- 不爬取详情页内"其它关联 torrent 网站"链接的二级页面
- 不下载 torrent 文件或 magnet 内容
- 不实现 dead-letter 独立集合（失败状态直接落在 list 表里）
- 不做 IMDB 二次爬取或信息扩展

## 架构 & 数据流

```
                ┌──────────────────────────────┐
                │ bt_13337x_spider_db           │
                │   bt_info_list (持续增长)      │
                │     + detail_status           │
                │   bt_info_detail  (目标)       │
                └──────────────────────────────┘
                            ▲ 分批 cursor
                            │ find({detail_status:"pending"}).sort(_id).limit(BATCH)
                            │
   ┌────────────────────────┴─────────────────────────┐
   │  crawl_detail_1337x.py                           │
   │  ┌────────────────────────────────────────────┐  │
   │  │ main loop (async)                          │  │
   │  │  loop:                                     │  │
   │  │    batch = coll.find(pending).sort(_id)    │  │
   │  │            .limit(BATCH)                   │  │
   │  │    for doc in batch:                       │  │
   │  │      claim(doc)  # CAS: status=processing  │  │
   │  │      asyncio.gather(*claimed)              │  │
   │  │    if batch.empty: exit                    │  │
   │  └─────────────┬──────────────────────────────┘  │
   │                │                                  │
   │  ┌─────────────▼─────────────┐  ┌─────────────┐  │
   │  │ fetch_one(url, page)      │  │ parse(html) │  │
   │  └───────────────────────────┘  └─────────────┘  │
   │                              ┌──────────▼──────┐  │
   │                              │ status = done / │  │
   │                              │  failed + 时间   │  │
   │                              └─────────────────┘  │
   └───────────────────────────────────────────────────┘
                │
                ▼
        ┌──────────────────┐
        │ data/html/*.html │
        └──────────────────┘
```

### 关键不变量

- HTML 缓存 + `bt_info_detail` 记录 **同时存在** → 视为已处理（重跑跳过）
- 任一缺失 → 重试（不区分哪个先写）
- 失败 N 次后 → `detail_status="failed"`，不自动重试（人工介入）
- 每次单条 `update_one`，无 `update_many` 批量写状态

### 状态机

```
pending ──claim──▶ processing ──成功──▶ done
                          │
                          └──失败──▶ failed
```

启动时把卡在 `processing` 的孤儿记录重置回 `pending`（幂等恢复）。

## 数据模型

### `bt_info_list` 新增字段

```json
{
  "_id": "<md5(detail_url)>",
  "name": "...",
  "detail_url": "...",
  "seeds": 7545,
  "leechers": 2764,
  "size": "1.6 GB",
  "list_time": "2022-10-21 00:00:00",
  "uploader": "TGxGoodies",
  "keyword": "House",
  "source": "1337x",
  "c_time": "2026-07-21 19:04:15",

  "detail_status": "pending",
  "detail_started_at": null,
  "detail_processed_at": null,
  "detail_error": null
}
```

所有时间字段为字符串 `yyyy-mm-dd hh:mm:ss`，不用 BSON datetime、不用时间戳。

### `bt_info_detail` 集合（新）

```json
{
  "_id": "<md5(detail_url)>",
  "detail_url": "https://1337x.to/torrent/5006555/...",
  "name": "The.Night.House.2021.720p.AMZN.WEBRip.800MB.x264-GalaxyRG[TGx]",

  "category": "Movies",
  "type": "HD",
  "language": "English",
  "total_size": "797.4 MB",
  "uploaded_by": "TGxGoodies",
  "downloads": 19122,
  "last_checked": "2026-07-21 17:00:00",
  "date_uploaded": "2022-07-15 00:00:00",
  "seeders": 482,
  "leechers": 67,

  "resource_links": {
    "magnet": "magnet:?xt=urn:btih:...",
    "torrent": "https://.../torrent/5006555.torrent",
    "itorrents": "https://itorrents.org/torrent/.../download",
    "torrage": "https://torrage.info/torrent.php?h=...",
    "btcache": "https://btcache.me/torrent/...",
    "stream": "https://..."
  },

  "cover_url": "https://...",
  "title": "THE NIGHT HOUSE",
  "genre": "HORROR THRILLER",
  "description": "Reeling from the unexpected death...",
  "rating": 5,                              // int 0-5，无评分时 null

  "tags": ["Galaxy", "TGx", "HD", "Quality", "WEBRip"],
  "info_hash": "B39082830C114113C9674FF944F98FED1E199880",

  "imdb_url": "https://www.imdb.com/title/tt9731534",
  "imdb_id": "tt9731534",

  "related_sites": [
    {"name": "TorrentGalaxy", "url": "https://torrentgalaxy.to"}
  ],

  "c_time": "2026-07-21 19:30:00",
  "source": "1337x"
}
```

索引：`{_id}`（主键自带）+ `{detail_url}` 唯一索引。

## 关键组件 & 函数签名

```
crawl_detail_1337x.py
│
├── 常量
│   CDP_URL, MONGO_URI, DB_NAME, COLL_LIST, COLL_DETAIL
│   HTML_DIR = "data/html"
│   BATCH = 200
│   MAX_RETRIES = 3
│   RETRY_BACKOFF = (2, 4, 8)
│
├── 纯函数（无副作用，可单测）
│   parse_relative_time(s: str, ref_now: datetime) -> str
│       "4 years ago" + ref_now -> "2022-07-15 00:00:00"
│
│   parse_detail(html: str, detail_url: str) -> dict | raises ParseError
│       单一职责：HTML -> 字段字典
│       失败抛 ParseError（被 caller 决定重试或标 failed）
│
│   html_cache_path(detail_url: str) -> Path
│       md5(detail_url).hex() + ".html"
│
│   extract_imdb_id(imdb_url: str | None) -> str | None
│       "https://www.imdb.com/title/tt9731534" -> "tt9731534"
│
├── DB 层（单条操作，CAS 安全）
│   claim_one(coll_list, doc_id) -> dict | None
│       findOneAndUpdate({_id, status:"pending"} -> "processing")
│       成功返回 claimed 文档；被抢走返回 None
│
│   mark_done(coll_list, doc_id, c_time_str)
│       update_one -> status:"done" + processed_at
│
│   mark_failed(coll_list, doc_id, error_msg)
│       update_one -> status:"failed" + error + processed_at
│
│   upsert_detail(coll_detail, doc)
│       replace_one({_id}, doc, upsert=True)
│
├── 浏览器层
│   fetch_one(page, url) -> str
│       goto + wait selector + page.content()
│       失败抛 PlaywrightTimeoutError
│
│   save_html_cache(detail_url, html)
│       Path("data/html/<md5>.html").write_text(html, encoding="utf-8")
│
├── 编排层（async）
│   async run_one(ctx, doc, coll_list, coll_detail) -> "done" | "failed"
│       MAX_RETRIES 次重试循环
│
│   async run_batch(ctx, claimed_docs, concurrency) -> Stats
│       Semaphore(concurrency) + asyncio.gather(run_one, ...)
│
└── async main()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        # 启动恢复
        rescue_orphaned_processing(coll_list)
        loop:
            cursor = coll_list.find({"detail_status": "pending"})
                              .sort("_id").limit(BATCH)
            claimed = [claim_one(d) for d in cursor if claim_one(d)]
            if not claimed: break
            await run_batch(ctx, claimed, args.concurrency)
            time.sleep(args.pace)
        print(f"完成。{stats}")
```

**共享常量化**：从 `crawl_1337x.py` import `DB_NAME`、`COLL_LIST` 等，不重复定义。

## 错误处理 & 重试

### 重试策略

`run_one` 内 `MAX_RETRIES=3` for 循环，失败按 `RETRY_BACKOFF=(2, 4, 8)` 秒 sleep。

```python
async def run_one(ctx, doc, coll_list, coll_detail) -> str:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            html = await fetch_one(page, doc["detail_url"])
            save_html_cache(doc["detail_url"], html)
            parsed = parse_detail(html, doc["detail_url"])
            upsert_detail(coll_detail, parsed)
            mark_done(coll_list, doc["_id"], now_str())
            return "done"
        except (PWTimeout, PlaywrightError) as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF[attempt - 1])
        except ParseError as e:
            last_err = f"ParseError: {e}"
            break  # 解析错重试无意义
    mark_failed(coll_list, doc["_id"], last_err)
    return "failed"
```

### 失败分类

| 异常 | 类别 | 行为 |
|---|---|---|
| `PWTimeout` | 网络/页面 | 重试 + backoff |
| `playwright._impl._errors.Error` | 浏览器 | 重试 + backoff |
| `ParseError` | 解析 | 不重试，直接 failed |
| 其他 `Exception` | 未知 | 重试 1 次，仍失败则 failed |

### Crash 恢复（孤儿 `processing`）

`main()` 开头执行一次：

```python
rescued = coll_list.update_many(
    {"detail_status": "processing"},
    {"$set": {"detail_status": "pending"},
     "$unset": {"detail_started_at": ""}}
)
if rescued.modified_count:
    logger.info(f"恢复 {rescued.modified_count} 条卡在 processing 的孤儿")
```

幂等，每次启动都会跑。

### Playwright Timeout

| 动作 | 超时 | 实现方式 | 失败时 |
|---|---|---|---|
| `page.goto(url)` | 30s | `page.goto(url, timeout=30000)` | 触发 PWTimeout → 重试 |
| `wait_for_selector("div.torrent-detail")` | 30s | `page.wait_for_selector(..., timeout=30000)` | 触发 PWTimeout → 重试 |
| 整条 `run_one` | 60s 总预算 | `asyncio.wait_for(run_one(...), 60)` | `asyncio.TimeoutError` 算一次失败 |

### 进度日志

每 batch 结束追加一行到 `data/html/_progress.log`：

```
[batch N] done=X failed=Y skipped=Z elapsed=Ts
```

## CLI 参数 & 配置

```python
parser.add_argument("-c", "--concurrency", type=int, default=4)
parser.add_argument("-b", "--batch", type=int, default=200)
parser.add_argument("-p", "--pace", type=float, default=1.0)
parser.add_argument("-l", "--limit", type=int, default=0)
parser.add_argument("-k", "--keyword", type=str, default=None)
parser.add_argument("--force", action="store_true")
parser.add_argument("--dry-run", action="store_true")
```

### `--force` 语义

```python
if args.force:
    coll_list.update_many(
        {},
        {"$set": {"detail_status": "pending"},
         "$unset": {"detail_started_at": "",
                    "detail_processed_at": "",
                    "detail_error": ""}}
    )
    query = {"detail_url": {"$exists": True}}
else:
    query = {"detail_status": "pending"}
```

`--force` 用于 parser bug 修复后批量重跑。

### 典型使用

```bash
# 首次跑全量
python crawl_detail_1337x.py

# 测试前 10 条
python crawl_detail_1337x.py --limit 10 --concurrency 1

# parser 改完后强制重跑某 keyword
python crawl_detail_1337x.py --keyword House --force

# 只解析不写
python crawl_detail_1337x.py --limit 5 --dry-run
```

## 测试策略

只测纯函数。编排层（async / DB / Playwright）通过手动跑 + MongoDB 抽样验证。

### 测试框架

```bash
uv pip install pytest pytest-asyncio
```

### 文件结构

```
tests/
├── fixtures/
│   ├── detail_night_house.html    # The.Night.House 完整页面
│   ├── detail_no_imdb.html        # 无 IMDB 链接
│   ├── detail_minimal.html        # 最小字段
│   └── detail_broken.html         # 故意损坏（ParseError 测试）
├── test_parse_detail.py
├── test_parse_relative_time.py
└── test_extract_imdb_id.py
```

### 关键用例

- `parse_relative_time`：years/hours/minutes/days ago、空、不可解析
- `parse_detail`：完整页面、无 IMDB、最小字段、损坏抛 ParseError
- `extract_imdb_id`：普通、带 query 参数、None、非法 URL

### Fixture 采集

首次跑成功后从 `data/html/<md5>.html` 复制 3-4 个有差异的到 `tests/fixtures/`。

## 迁移计划（一次性）

执行顺序（脚本顶部加 migration 块，或单独的 `migrate_detail_status.py`）：

```python
# 1. bt_info_list 新增 detail_status 字段
coll_list.update_many(
    {"detail_status": {"$exists": False}},
    {"$set": {"detail_status": "pending"},
     "$unset": {"detail_started_at": "",
                "detail_processed_at": "",
                "detail_error": ""}}
)

# 2. c_time 字段 datetime → 字符串
coll_list.update_many(
    {"c_time": {"$type": "date"}},
    [{"$set": {"c_time": {
        "$dateToString": {"format": "%Y-%m-%d %H:%M:%S", "date": "$c_time"}
    }}}]
)

# 3. bt_info_detail 建索引
coll_detail.create_index("detail_url", unique=True)
```

幂等，重复执行不报错。

## 决策记录

| 决策点 | 选择 | 备选 |
|---|---|---|
| 脚本形态 | 单脚本 fetch+parse | 两脚本 pipeline |
| DB/集合命名 | `bt_13337x_spider_db.bt_info_list/detail` | 沿用 `pan_spider_db.ResToDoItem1337x` |
| "未处理" 定义 | HTML 缓存 + detail 记录都存在 | 仅 detail / 仅缓存 / 不过滤 |
| 范围 | 全量 | 按 seeds / 时间过滤 |
| 下载链接存储 | typed 嵌套对象 | 数组 / 仅 magnet |
| 日期格式 | `yyyy-mm-dd hh:mm:ss` 字符串 | BSON datetime / 时间戳 |
| HTML 缓存命名 | `<md5>.html` | 裸 md5 |
| 失败处理 | 重试 N 次 + 状态字段 | dead-letter 集合 |
| 并发 | 支持并行 + 可配置 | 串行 |
| 状态记录位置 | `bt_info_list.detail_status` | 独立状态集合 |
| 状态更新粒度 | 单条 update_one | 批量 update_many |

## 实施步骤（概要）

1. 写 `crawl_detail_1337x.py` 主脚本
2. 写 `migrate_detail_status.py` 一次性迁移
3. 准备 tests/fixtures（从一次成功运行的 `data/html/` 复制）
4. 写 pytest 测试
5. 用 `--limit 5 --dry-run` 跑通解析
6. 用 `--limit 10` 跑通 fetch+save
7. 全量跑（1000 条 × 2 并发 ≈ 15-20 分钟）
8. 抽样核对 `bt_info_detail` 数据