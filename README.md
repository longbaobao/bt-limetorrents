# LimeTorrents 爬虫

> 基于 DrissionPage + headless Chrome 的 LimeTorrents 种子抓取器：分类浏览 + 关键词搜索 → 详情页解析 → 写入 MongoDB。支持 checkpoint 续跑、幂等 upsert、CAS 状态机和 dry-run。

## 目录

- [功能概览](#功能概览)
- [项目结构](#项目结构)
- [环境准备](#环境准备)
- [数据库初始化](#数据库初始化)
- [快速开始](#快速开始)
- [架构与数据流](#架构与数据流)
- [MongoDB Schema](#mongodb-schema)
- [CLI 参数](#cli-参数)
- [测试与编译](#测试与编译)
- [调度器与分页约定](#调度器与分页约定)
- [数据去重与不变量](#数据去重与不变量)
- [常见问题](#常见问题)
- [贡献指南](#贡献指南)
- [License](#license)

## 功能概览

- **分类浏览**：遍历 LimeTorrents 分类页（Movies / TV-shows / Music 等），按页翻页，原子 checkpoint，断点续爬。
- **关键词搜索**：根据关键词构造 `/search/{category}/{keyword}/` URL，自动跟随 `Next page` 链接，避免硬编码双斜杠。
- **详情抓取**：进入 `/torrent-19859670.html` 详情页，抓取 `info_hash`、magnet、torrent 下载链接、Stream、Tracker 列表（含每条 tracker 的 last_check / status / seeders / leechers）、完整文件树（目录 + 文件 + 视频 / NFO / 文档类型）、相关种子、评论数。
- **Related → bt_info_list**：详情解析出的 related 资源（取自 `Related torrents` 表）会被 `upsert_listing` 写回 `bt_info_list` 的 pending 队列，下一轮详情抓取时自动消费。
- **数据持久化**：列表 + 详情分别写入 `bt_limetorrents_spider_db` 的两个集合；HTML 缓存到 `data/html/limetorrents/<md5>.html`，便于复现与离线分析。
- **健壮性**：
  - `pending → processing → done | failed` CAS 状态机 + 启动时 orphan 恢复。
  - 失败页不推进 checkpoint（保留断点等下次重试）。
  - 文档超过 16 MB → `DocumentTooLarge` 显式 `failed`，**不切片不截断** `files` 数组，HTML 缓存保留待人工 review。
  - 单条 related upsert 抛异常时不影响主 detail 状态、不影响其他 related 写入。
  - `dry-run` 抓取并解析但不写库（related 仍写，可作为下一轮下游种源）。

## 项目结构

```text
bt-limetorrents/
├── crawl_limetorrents.py            # 列表入口（分类浏览 + 单关键词搜索）
├── crawl_limetorrents_by_keys.py    # 批量关键词 wrapper（subprocess 子进程）
├── crawl_detail_limetorrents.py     # 详情抓取 + 状态机
├── init_limetorrents_db.py          # 一次性建库 + 索引（幂等）
├── requirements.txt                 # 依赖快照（DrissionPage / pymongo / pytest ...）
├── CLAUDE.md                        # 内部项目说明（架构 + 字段约定）
├── docs/
│   └── superpowers/
│       ├── specs/2026-07-26-limetorrents-crawler-design.md
│       └── plans/2026-07-26-limetorrents-crawler.md
├── tests/
│   ├── conftest.py                  # fixture 读取 helper
│   ├── fixtures/
│   │   ├── limetorrents_browse_movies_page2.html
│   │   ├── limetorrents_search_st_vincent.html
│   │   ├── limetorrents_detail_st_vincent.html
│   │   └── capture_limetorrents_fixtures.py
│   ├── test_limetorrents_*.py       # 离线 pytest 套件（70+ tests）
│   └── ...
├── data/                            # 运行时数据（gitignore）
│   ├── keys.txt / keys-done.txt
│   ├── checkpoints/                 # 原子 JSON 续跑快照
│   └── html/limetorrents/           # HTML 缓存
└── schedule_job.py                  # 旧 1337x 调度器（保留）
```

## 环境准备

| 工具 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | `.venv/Scripts/python.exe` 强制使用项目虚拟环境 |
| uv | latest | 推荐 `uv venv .venv --python 3.11` 重建环境 |
| DrissionPage | 4.1.1.4 | 自带 Chromium，无需额外安装 |
| MongoDB | 4.4+ | 本机 `localhost:27017`（无密码） |

```bash
# 重建虚拟环境
uv venv .venv --python 3.11
uv pip install -r requirements.txt

# 安装 Chromium（DrissionPage 4.1.1.4 需要）
.venv/Scripts/python.exe -m playwright install chromium
```

## 数据库初始化

```bash
.venv/Scripts/python.exe init_limetorrents_db.py
```

幂等行为：多次运行只补齐缺失的 `detail_status` 字段，并创建索引：

- `bt_info_list`：`detail_url`(unique) / `detail_status` / `keywords` / `(category ASC, added_at DESC)`
- `bt_info_detail`：`detail_url`(unique) / `info_hash`

## 快速开始

```bash
# 1. 初始化数据库
.venv/Scripts/python.exe init_limetorrents_db.py

# 2. 浏览分类前 1 页（Movies 第 2 页）
.venv/Scripts/python.exe crawl_limetorrents.py --category Movies --start-page 2 --max-pages 1

# 3. 关键词搜索前 2 页
.venv/Scripts/python.exe crawl_limetorrents.py --keyword "St Vincent" --max-pages 2

# 4. 批量关键词 wrapper
echo -e "St Vincent\nHouse\nInception" > data/keys.txt
.venv/Scripts/python.exe crawl_limetorrents_by_keys.py --search-category all

# 5. 详情抓取（默认并发 2）
.venv/Scripts/python.exe crawl_detail_limetorrents.py --keyword "St Vincent" --limit 10 --concurrency 2

# 6. dry-run 验证解析（不写库）
.venv/Scripts/python.exe crawl_detail_limetorrents.py --limit 1 --dry-run

# 7. 把失败任务重置为 pending
.venv/Scripts/python.exe crawl_detail_limetorrents.py --retry-failed
```

## 架构与数据流

```text
┌─────────────────────────┐
│ 分类浏览 / 关键词搜索   │
│  crawl_limetorrents.py   │
└────────────┬────────────┘
             │ 解析 table.table2
             ▼
   bt_limetorrents_spider_db
   ├─ bt_info_list
   │  字段含 health/seeders/leechers
   │  detail_status: pending ──┐
   └─ bt_info_detail            │
                                ▼
              crawl_detail_limetorrents.py
              ├─ claim_one (CAS pending→processing)
              ├─ fetch_one + save_html_cache
              ├─ parse_detail → 写 bt_info_detail
              ├─ _persist_related_listings → upsert_listing
              └─ mark_done / mark_failed
                                │
                                ▼
              DocumentTooLarge → 立即 failed
              (不切片不截断 files)
```

## MongoDB Schema

### `bt_info_list`

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` | string | `md5(detail_url)` |
| `name`, `detail_url`, `torrent_url` | string | 标题 / 详情 / torrent 直链 |
| `category` | string | 浏览分类（Movies / TV-shows / Music …） |
| `added_text` | string | 站点原始 Added 文本 |
| `added_at` | string | `yyyy-mm-dd hh:mm:ss` 解析结果（解析失败时为空串） |
| `size` | string | 例：`2.8 GB` |
| `seeders`, `leechers` | int | 数值（脏数据→0） |
| `health` | int \| None | `td.tdright > div.hbN`（N ∈ [1, 10]，缺 / 越界时 None） |
| `observed_at` | string | 抓取时刻 |
| `source` | string | `limetorrents` |
| `keywords` | array | 累积该记录被哪些关键词发现 |
| `discovery_modes` | array | `browse` / `search` / `related` |
| `first_seen_at`, `last_seen_at` | string | 首次/最近发现 |
| `detail_status` | string | `pending` / `processing` / `done` / `failed` |
| `detail_started_at`, `detail_processed_at`, `detail_error` | string \| null | 状态时间戳 + 失败原因 |

### `bt_info_detail`

| 字段 | 类型 | 说明 |
|---|---|---|
| `_id` | string | `md5(detail_url)` |
| `name`, `detail_url`, `info_hash` | string | 标题 / 详情 / SHA-1 hash |
| `added_text`, `added_at` | string | 详情 Added |
| `category` | string | 浏览分类 |
| `total_size` | string | 详情 Total Size |
| `meta_description` | string | `<meta name="description">` |
| `resource_links` | object | `{magnet, torrent, stream}` |
| `trackers` | array | `{url, last_check_text, last_checked_at, status, seeders, leechers}` |
| `tracker_count`, `successful_tracker_count`, `failed_tracker_count` | int | |
| `files` | array | `{entry_index, path, size, entry_type, depth}` 完整文件树（含目录节点） |
| `declared_file_count`, `file_entry_count` | int | 页面声明 vs 实际数组长度 |
| `related_torrents` | array | 来自 "Related torrents" 表的摘要条目 |
| `comments_count` | int | 来自 `Comments (N Comments)` h2 |
| `html_cache_path` | string | `data/html/limetorrents/<md5>.html` |
| `source` | string | `limetorrents` |
| `parsed_at` | string | 抓取时刻 |

## CLI 参数

### `crawl_limetorrents.py`

```text
--keyword KW                       # 提供后进入搜索模式
--category CAT=Movies              # 浏览分类（默认 Movies）
--search-category CAT=all          # 搜索分类（默认 all）
--start-page N=1                   # 起始页
--max-pages N=0                    # 最大页数（0=持续到没有下一页）
--page-sleep S=1.0                 # 页间等待秒数
```

### `crawl_limetorrents_by_keys.py`

```text
--search-category CAT=all          # 搜索分类
--concurrency N=1                  # 1-16（受 CRAWL_LIMETORRENTS_CONCURRENCY env 影响）
```

### `crawl_detail_limetorrents.py`

```text
-c / --concurrency N=2             # 并发 tab 数
-b / --batch N=100                 # 每批从 MongoDB 取多少条
-p / --pace S=1.0                  # 批次间等待秒数
-l / --limit N=0                   # 最多处理多少条
-k / --keyword KW                  # 仅处理指定 keyword
--force                            # 无视 status 强制重跑（会输出影响数量）
--retry-failed                     # 把 failed → pending，保留 done
--dry-run                          # 抓取并解析，不写库不 claim
```

### 分类白名单

```text
browse category:   anime / applications / games / movies / music / tv-shows / other
                   （tv、TV shows 等价于 tv-shows）
search category:   上面 7 种 + all
```

## 测试与编译

```bash
# 跑全套离线测试（约 70+ tests，无需真实 Mongo/Chrome）
.venv/Scripts/python.exe -m pytest -v

# 编译检查
.venv/Scripts/python.exe -m compileall -q \
  crawl_limetorrents.py \
  crawl_limetorrents_by_keys.py \
  crawl_detail_limetorrents.py \
  init_limetorrents_db.py \
  tests
```

## 调度器与分页约定

- 每个 (mode, category, keyword) 三元组对应独立 checkpoint `data/checkpoints/limetorrents-<md5>.json`。
- 失败页不推进 checkpoint；只有当 `replace_one` 成功后才 `save_checkpoint`。
- wrapper 仅在子进程返回 `0` 时把关键词追加 `data/keys-done.txt`；失败保留等下次续跑。
- 关键词搜索分页不依赖 URL 模板双斜杠，而是从页面 `Next page` 链接里读真实 `href`。

## 数据去重与不变量

- 列表与详情共用 `md5(detail_url)` 作为 `_id`；`upsert_listing` / `replace_one(..., upsert=True)` 联合保证幂等。
- `upsert_listing` 的 `$setOnInsert` 把 `detail_status` 初始化为 `pending`，**不重置**已有的 `done` / `failed`；列表重抓会刷新动态字段（seeds / leechers / health / last_seen_at）。
- 同一详情被多关键词发现时，`keywords` 与 `discovery_modes` 用 `$addToSet` 累积，不重复插入。
- `DocumentTooLarge` 显式 `failed`，**不切片不截断** `files`，HTML 缓存保留待人工 review。
- 单条 related upsert 抛异常被 `try/except` 隔离，不影响主 detail 状态、不影响其他 related。

## 常见问题

| 问题 | 排查 |
|---|---|
| `ModuleNotFoundError: No module named 'drissionpage'` | 用 `uv pip install -r requirements.txt` 重建环境，并确认使用 `.venv/Scripts/python.exe` |
| `playwright._impl._errors.Error: Chromium not found` | `uv pip install playwright` 后 `.venv/Scripts/python.exe -m playwright install chromium` |
| 列表页只 0 条 / 落到详情页空白 | 检查 `crawl_limetorrents` 是否命中 Cloudflare 软墙；确认 `fetch_with_cf_bypass` 等待 `table.table2` |
| 详情跑得很慢 | 调高 `--concurrency`；但默认 2 防止被限流；可在 `crawl_limetorrents_by_keys.py` 设置 `CRAWL_LIMETORRENTS_CONCURRENCY=4` |
| 列表抓完 detail 后 `done=0` | 检查 `claim_one` 是否在 `--dry-run` 模式下被跳过；非 dry-run 时状态会被 CAS 抢占 |

## 贡献指南

1. **TDD 优先**：先写 `tests/test_limetorrents_*.py` 测试，再补实现；运行 `pytest` 必须 0 回归。
2. **新增列表字段**：
   - 在 `crawl_limetorrents.parse_result_row` 加键 + `extract_*` helper；
   - 在 `tests/test_limetorrents_listing.py` 加 fixture guard；
   - 字段语义写入 `CLAUDE.md` 的「MongoDB Schema」段。
3. **新增详情字段**：在 `crawl_detail_limetorrents.parse_detail` dict 加键 + 解析 helper + 测试；无需改 schema。
4. **新增搜索来源**：复刻 `parse_result_row` 字段名 → 走 `upsert_listing` 幂等 upsert → checkpoint 文件名 hash 与现有一致 → MongoDB schema 不变。
5. **依赖变更**：同步 `requirements.txt`（`uv pip freeze > requirements.txt`）。
6. **Commit 规范**：中文 commit message，例如 `功能：在列表解析中新增 health 字段`。

## License

仅供学习与研究使用，请勿用于任何侵犯版权或违反当地法律的用途。

---

如需了解更多实现细节，参考：

- `CLAUDE.md` — 内部架构 + 字段约定
- `docs/superpowers/specs/2026-07-26-limetorrents-crawler-design.md` — 设计规格
- `docs/superpowers/plans/2026-07-26-limetorrents-crawler.md` — 实施计划
