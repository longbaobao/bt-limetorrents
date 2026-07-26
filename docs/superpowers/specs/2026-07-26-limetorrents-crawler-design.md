# LimeTorrents 列表、关键词与详情爬虫设计

**日期**：2026-07-26  
**状态**：已批准，待实施  
**目标站点**：`https://www.limetorrents.fun/`  
**数据库**：`bt_limetorrents_spider_db`

## 1. 背景

项目现有实现面向 1337x，包含单关键词列表爬虫、批量关键词调度器、详情页爬虫、MongoDB 状态机、HTML 缓存、断点续跑和离线解析测试。本次将目标站点直接替换为 LimeTorrents，不保留旧的 1337x 入口。

目标站点结构已通过真实浏览器验证：

- 分类列表示例：`https://www.limetorrents.fun/browse-torrents/Movies/date/2/`
- 关键词搜索首页：`https://www.limetorrents.fun/search/all/St-Vincent/`
- 详情示例：`https://www.limetorrents.fun/St-Vincent-2014-1080p-PTV-WEB-DL-AAC-2-0-H-264-PiRaTeS-torrent-19859670.html`
- 列表和搜索结果使用 `table.table2`
- 搜索页的 `table.table3` 是 Sponsored Links，必须忽略
- 详情主体使用 `div.torrentinfo`
- 页面内直接 POST `/post/search.php` 会触发 Cloudflare `403`，必须使用真实浏览器导航到规范搜索 URL

## 2. 目标

1. 支持按分类浏览 LimeTorrents，分类可配置，默认 `Movies`。
2. 支持单关键词搜索，搜索分类可配置，默认 `all`。
3. 支持从 `data/keys.txt` 批量执行关键词搜索。
4. 列表和详情保持两阶段处理，均支持中断恢复和幂等重跑。
5. 解析详情页的 Hash、下载链接、tracker、完整文件清单和相关 torrent。
6. 将数据写入 `bt_limetorrents_spider_db`。
7. 直接改名并替换现有 1337x 脚本，不保留旧站点实现。

## 3. 非目标

- 不下载 `.torrent` 文件。
- 不调用 magnet 链接或下载 torrent 内容。
- 不访问相关 torrent 的二级详情页。
- 不抓取 LimeTorrents 站外广告落地页。
- 不绕过登录、验证码或访问控制。
- 不为未来未知站点设计通用插件系统。
- 不建立独立 `bt_info_files` 集合；文件清单合并到 `bt_info_detail.files`。

## 4. 方案选择

采用“聚焦替换”方案：复用现有 checkpoint、批量关键词、MongoDB CAS 状态机、重试和 HTML 缓存结构，仅替换站点 URL、DOM 解析、数据模型、脚本名称和测试。

未采用的方案：

- 站点适配器重构：当前只有 LimeTorrents，抽象收益不足以覆盖改动成本。
- 单脚本整合：会让列表、批量关键词和详情状态机职责混杂，并破坏现有调度方式。

## 5. 文件与职责

| 现有文件 | 目标文件 | 职责 |
|---|---|---|
| `crawl_1337x_by_key.py` | `crawl_limetorrents.py` | 分类浏览或单关键词搜索；分页、checkpoint、列表解析和 upsert |
| `crawl_1337x_by_keys.py` | `crawl_limetorrents_by_keys.py` | 读取 `data/keys.txt`，并行调用单关键词入口，维护 done 文件 |
| `crawl_detail_1337x.py` | `crawl_detail_limetorrents.py` | 从列表集合领取 pending 记录，抓取、缓存、解析和写入详情 |
| `migrate_1337x.py`、`migrate_detail_status.py` | `init_limetorrents_db.py` | 幂等创建索引并补齐必要状态字段 |

现有 1337x 专用测试和 fixture 同步替换为 LimeTorrents 版本。

## 6. 总体架构

```text
分类浏览 / 单关键词搜索 / keys.txt 批量搜索
                     │
                     ▼
          crawl_limetorrents.py
          ├─ 真实 Chromium 导航
          ├─ table.table2 列表解析
          ├─ checkpoint
          └─ 幂等 upsert
                     │
                     ▼
 bt_limetorrents_spider_db.bt_info_list
          detail_status = pending
                     │
                     ▼
       crawl_detail_limetorrents.py
          ├─ CAS claim
          ├─ 详情 HTML 缓存
          ├─ div.torrentinfo 解析
          ├─ trackers / files / related
          └─ retry + 状态更新
                     │
                     ▼
 bt_limetorrents_spider_db.bt_info_detail
```

列表发现与详情处理解耦：列表抓取可以持续增长，详情脚本按 pending 状态逐批消费。

## 7. URL 与 CLI 设计

### 7.1 分类浏览

URL：

```text
https://www.limetorrents.fun/browse-torrents/{category}/date/{page}/
```

示例：

```bash
python crawl_limetorrents.py --category Movies
python crawl_limetorrents.py --category TV-shows --start-page 2 --max-pages 10
```

参数：

- `--category`：浏览分类，默认 `Movies`
- `--start-page`：起始页，默认 `1`
- `--max-pages`：本次最多处理页数，`0` 表示持续到没有下一页
- `--page-sleep`：页间等待秒数

浏览分类只接受站点已验证值：`Anime`、`Applications`、`Games`、`Movies`、`Music`、`TV-shows`、`Other`。输入不在集合中时 CLI 直接报错，不发起请求。

### 7.2 单关键词搜索

首页 URL：

```text
https://www.limetorrents.fun/search/{search_category}/{keyword_slug}/
```

示例：

```bash
python crawl_limetorrents.py --keyword "St Vincent"
python crawl_limetorrents.py --keyword "St Vincent" --search-category Movies
```

参数：

- `--keyword`：提供后进入关键词模式
- `--search-category`：默认 `all`；可选 `all` 或上述浏览分类对应的站点值
- `--start-page`、`--max-pages`、`--page-sleep` 与分类模式一致

`keyword_slug` 构造规则固定为：去除首尾空白、将连续空白折叠为单个连字符、保留已有连字符，再使用 `urllib.parse.quote` 对其余路径字符做 UTF-8 URL 编码。空关键词在 CLI 校验阶段拒绝。

关键词仅用于构造首页 URL。后续页直接跟随页面中的 `Next page` 链接，不依赖当前站点搜索分页路径中的双斜杠细节。

### 7.3 批量关键词

```bash
python crawl_limetorrents_by_keys.py --search-category all
```

- 从 `data/keys.txt` 读取关键词。
- 从 `data/keys-done.txt` 排除已完成关键词。
- 只有子进程返回码为 `0` 才追加 done。
- 单个关键词失败不阻断其他关键词。
- 子进程超时后保留 checkpoint，不写虚假完成状态。

### 7.4 详情处理

```bash
python crawl_detail_limetorrents.py
python crawl_detail_limetorrents.py --limit 1 --dry-run
python crawl_detail_limetorrents.py --limit 20 --concurrency 2
python crawl_detail_limetorrents.py --retry-failed
python crawl_detail_limetorrents.py --force
```

参数沿用现有详情脚本：

- `--concurrency`：默认 `2`
- `--batch`
- `--pace`
- `--limit`
- `--keyword`
- `--force`
- `--retry-failed`
- `--dry-run`

参数语义：

- `--keyword` 查询 `bt_info_list.keywords` 数组，仅处理由该关键词发现的记录。
- `--retry-failed` 只将当前过滤范围内的 `failed` 重置为 `pending`，保留 `done`。
- `--force` 将当前过滤范围内的所有记录重置为 `pending`；未给 `--keyword` 时作用于全表，并在执行前打印影响数量。
- `--dry-run` 抓取并解析 HTML、写入本地缓存和输出解析摘要，但不 claim、不写详情集合、不更新状态。

## 8. 列表解析

### 8.1 目标 DOM

- 结果表：`table.table2`
- 行：`table.table2 tr`，跳过含 `th` 的表头
- 详情链接：href 匹配 `-torrent-\d+\.html`
- torrent 链接：href 含 `.torrent`
- 列：
  - `td.tdleft`：torrent 链接和详情链接
  - 第一个 `td.tdnormal`：Added
  - 第二个 `td.tdnormal`：Size
  - `td.tdseed`：Seed
  - `td.tdleech`：Leech

关键词搜索页还包含 `table.table3` Sponsored Links；解析器不得读取该表。

### 8.2 Added 与分类

浏览页 Added 示例：

```text
7 hours ago
```

搜索页 Added 示例：

```text
17 days ago - in Music
```

解析器同时保存：

- `added_text`：站点原始文本
- `added_at`：以抓取时刻为参考转换成 `yyyy-mm-dd hh:mm:ss`
- `category`：优先从 `- in <Category>` 提取；浏览页缺失时使用 CLI 分类

支持 minutes、hours、days、weeks、months、years、Today、Yesterday 和站点出现的绝对日期格式。无法解析时保留 `added_text`，将 `added_at` 设为空字符串，不丢弃记录。

### 8.3 `bt_info_list` 数据模型

```json
{
  "_id": "<md5(detail_url)>",
  "name": "St Vincent 2014 1080p ...",
  "detail_url": "https://www.limetorrents.fun/...-torrent-19859670.html",
  "torrent_url": "https://itorrents.net/torrent/700D...torrent?...",
  "category": "Movies",
  "added_text": "7 hours ago",
  "added_at": "2026-07-26 10:00:00",
  "size": "2.8 GB",
  "seeders": 3,
  "leechers": 24,
  "keywords": ["St Vincent"],
  "discovery_modes": ["browse", "search"],
  "source": "limetorrents",
  "first_seen_at": "2026-07-26 17:00:00",
  "last_seen_at": "2026-07-26 17:00:00",
  "detail_status": "pending",
  "detail_started_at": null,
  "detail_processed_at": null,
  "detail_error": null
}
```

### 8.4 幂等 upsert

以 `_id = md5(detail_url)` 和唯一索引 `detail_url` 去重。

重复发现时：

- `$set` 更新 name、torrent_url、category、added、size、seeders、leechers、last_seen_at 和 source。
- `$addToSet` 累积 `keywords` 与 `discovery_modes`。
- `$setOnInsert` 设置 first_seen_at、detail_status=`pending` 和初始状态字段。
- 不把已有 `done`、`processing` 或 `failed` 重置为 `pending`。

## 9. 分页与 checkpoint

每个查询使用独立 checkpoint，键由查询模式、分类和关键词共同决定。文件写入 `data/checkpoints/limetorrents-<md5(query_key)>.json`，其中 `query_key` 为规范化后的 `query_type|category|keyword`。checkpoint 使用临时文件写完后原子替换，避免进程中断留下半截 JSON。

```json
{
  "query_type": "browse",
  "category": "Movies",
  "keyword": null,
  "current_page": 2,
  "next_url": "https://www.limetorrents.fun/browse-torrents/Movies/date/3/",
  "updated_at": "2026-07-26 17:00:00"
}
```

规则：

1. 当前页成功加载、确认存在结果结构并完成 MongoDB upsert 后，才推进 checkpoint。
2. 页面超时、Cloudflare 挑战未解除、结果表缺失或数据库写入失败时，不推进 checkpoint。
3. 查询完成后清除 checkpoint。
4. `--start-page` 只在不存在 checkpoint 时生效；已有 checkpoint 优先续跑。
5. 搜索分页保存页面实际返回的 `Next page` href，避免自行推导双斜杠 URL。

这修复了现有 1337x 实现中“失败页仍推进 checkpoint”可能造成永久漏数的问题。

## 10. 详情解析

### 10.1 页面识别

- 成功选择器：`div.torrentinfo`
- 标题：`h1`
- 基本信息：`div.torrentinfo > table` 的前四行
- 页面摘要：`meta[name='description']`

无法找到详情主体时抛出 `ParseError`。

### 10.2 基本字段

示例页面包含：

- Torrent Hash：`700D963C82A513317703A730DD3C030E19FFAD8E`
- Torrent Added：`7 hours ago in Movies`
- Torrent Size：`2.8 GB`
- Stream：`https://www.limemovies.org/`

解析器保存原始时间和绝对时间，并从 Added 行提取分类。

### 10.3 下载链接

- Magnet：`div.downloadarea a[href^='magnet:']`
- Torrent：`div.downloadarea a[href*='.torrent']`
- Stream：基本信息表中 `Stream` 行的链接

仅存储 URL，不点击或下载。

### 10.4 Tracker 列表

定位文本为 `Trackers List` 的 `h2`，解析其后容器中的 `table.table3`：

```json
{
  "url": "udp://open.stealth.si:80/announce",
  "last_check_text": "7 hours ago",
  "last_checked_at": "2026-07-26 10:00:00",
  "status": "success",
  "seeders": 3,
  "leechers": 16
}
```

保存全部 tracker 行，并计算 `tracker_count`、`successful_tracker_count` 和 `failed_tracker_count`。

### 10.5 文件清单

- 文件区域标题：`Torrent File Content (<N> files)`
- 条目节点：`.fileline`
- 通过图标 class 区分 directory、video、nfo、document 和普通 file
- 保存页面顺序、路径、大小、类型和层级

目录和文件都进入 `files` 数组：

```json
{
  "entry_index": 0,
  "path": "www.UIndex.org - St Vincent 2014 ...",
  "size": "",
  "entry_type": "directory",
  "depth": 0
}
```

```json
{
  "entry_index": 1,
  "path": "www.UIndex.org - .../St Vincent 2014 ...mkv",
  "size": "2.8 GB",
  "entry_type": "video",
  "depth": 1
}
```

同时保存：

- `declared_file_count`：页面标题声明的文件数量
- `file_entry_count`：包含目录节点在内的实际数组长度

不建立 `bt_info_files` 集合。

### 10.6 相关 torrent

定位文本为 `Related torrents` 的 `h2`，解析其后的 `table.table2`，保存：

- name
- detail_url
- added_text
- added_at
- category
- size
- seeders
- leechers

不继续访问相关详情页。

### 10.7 评论计数

从文本形如 `Comments (0 Comments)` 的 `h2` 提取 `comments_count`。本次只保存评论数量，不解析评论正文；示例页面没有可用于验证评论结构的实际评论。

### 10.8 `bt_info_detail` 数据模型

```json
{
  "_id": "<md5(detail_url)>",
  "detail_url": "https://www.limetorrents.fun/...-torrent-19859670.html",
  "name": "St Vincent 2014 1080p ...",
  "info_hash": "700D963C82A513317703A730DD3C030E19FFAD8E",
  "category": "Movies",
  "added_text": "7 hours ago in Movies",
  "added_at": "2026-07-26 10:00:00",
  "total_size": "2.8 GB",
  "meta_description": "Download St Vincent ...",
  "resource_links": {
    "magnet": "magnet:?xt=urn:btih:...",
    "torrent": "https://itorrents.net/torrent/...",
    "stream": "https://www.limemovies.org/"
  },
  "trackers": [],
  "tracker_count": 0,
  "successful_tracker_count": 0,
  "failed_tracker_count": 0,
  "files": [],
  "declared_file_count": 3,
  "file_entry_count": 4,
  "related_torrents": [],
  "comments_count": 0,
  "html_cache_path": "data/html/limetorrents/<md5>.html",
  "source": "limetorrents",
  "parsed_at": "2026-07-26 17:00:00"
}
```

详情以 `_id` replace/upsert，重跑不会生成重复文档。

## 11. HTML 缓存

路径：

```text
data/html/limetorrents/<md5(detail_url)>.html
```

写入顺序：

1. 页面加载成功。
2. 立即将 HTML 写入缓存。
3. 解析 HTML。
4. upsert `bt_info_detail`。
5. 将 `bt_info_list.detail_status` 标为 `done`。

解析或数据库写入失败时，HTML 缓存仍保留，便于复现和修复 parser。

## 12. 状态机与并发

```text
pending ──CAS claim──▶ processing ──成功──▶ done
                              └──失败──▶ failed
```

- `claim_one` 使用 `find_one_and_update({_id, detail_status:'pending'})`。
- 启动时将遗留 `processing` 恢复为 `pending`。
- 状态更新使用单条 `update_one`，不批量覆盖正在被其他进程处理的记录。
- 默认详情并发为 `2`，可通过 CLI 调整。
- 每个并发任务使用独立 tab，并在 `finally` 中关闭。
- 主进程退出时关闭 DrissionPage Chromium。

## 13. 错误处理与重试

### 13.1 页面错误

- Cloudflare 挑战：等待后重新导航。
- 目标选择器缺失：视为页面未成功加载。
- 浏览器断开、网络错误和超时：最多重试 3 次。
- 退避：2、4、8 秒。
- 重试耗尽：列表任务返回非零且不推进 checkpoint；详情任务标记 `failed`。

### 13.2 解析错误

- 列表页缺少 `table.table2`，或命中 Cloudflare 挑战标记：按加载失败处理，不推进 checkpoint。
- 存在结构正确的 `table.table2`、没有结果行且没有 `Next page`：视为正常空结果/查询结束。
- 存在结果表但行结构全部不可识别：按 `ParseError` 处理，避免把站点改版误判为空结果。
- 详情主体缺失：抛 `ParseError`，不重复请求相同 HTML，直接标记失败。
- 单个 tracker、文件或相关 torrent 行解析失败：记录警告并继续其他行；核心字段无法识别时整条失败。

核心字段定义：

- 列表：name、detail_url
- 详情：name、detail_url、info_hash

magnet 或 torrent 链接缺失不是结构错误，字段保存为空。

### 13.3 MongoDB 文档大小

用户明确要求把完整文件清单合并到 `bt_info_detail`，不建立独立文件集合。

因此：

- 不截断 `files`。
- 不隐藏或吞掉 `pymongo.errors.DocumentTooLarge`。
- 文档超过 MongoDB 16 MB 时，详情状态标为 `failed`，错误信息明确包含文档过大原因。
- HTML 缓存完整保留，便于后续人工决定压缩或拆表。

### 13.4 中断

- `Ctrl+C` 不写 done。
- 当前页未完成时不推进 checkpoint。
- 已 claim 但未完成的详情在下次启动时由 orphan recovery 恢复为 pending。
- 批量关键词 wrapper 只把返回码 0 的关键词写入 done 文件。

## 14. MongoDB 索引与初始化

`init_limetorrents_db.py` 幂等执行：

### `bt_info_list`

- `_id`：MongoDB 主键
- `detail_url`：唯一索引
- `detail_status`：普通索引
- `keywords`：普通多键索引
- `{category: 1, added_at: -1}`：复合索引

### `bt_info_detail`

- `_id`：MongoDB 主键
- `detail_url`：唯一索引
- `info_hash`：普通索引，不设唯一约束

初始化脚本只补充缺失的 `detail_status`，不覆盖已有状态。

## 15. 测试策略

遵循 TDD：先写失败测试，再写最小实现，最后重构。

### 15.1 Fixture

```text
tests/fixtures/
├── limetorrents_browse_movies_page2.html
├── limetorrents_search_st_vincent.html
├── limetorrents_detail_st_vincent.html
├── limetorrents_detail_minimal.html
├── limetorrents_detail_no_magnet.html
├── limetorrents_empty_results.html
└── limetorrents_cloudflare.html
```

真实 fixture 由用户提供的两个示例页面和实际关键词搜索页保存；单元测试离线运行，不依赖实时站点。

### 15.2 纯函数测试

- `build_browse_url`
- `build_search_url`
- `slugify_keyword`
- `parse_relative_time`
- `parse_listing`
- `detect_next_url`
- `parse_detail`
- `parse_trackers`
- `parse_files`
- `parse_related_torrents`

### 15.3 关键用例

- 浏览页约 40 条结果可解析。
- 搜索页忽略 Sponsored Links `table.table3`。
- 详情链接与 `.torrent` 链接正确区分。
- 搜索结果从 Added 文本解析分类。
- 无法解析时间时保留原始文本。
- Next page 使用实际 href。
- 详情 Hash 为 `700D963C82A513317703A730DD3C030E19FFAD8E`。
- 示例详情解析 magnet、torrent、tracker 和 3 个声明文件。
- 无 magnet 页面仍可解析。
- 损坏详情页抛 `ParseError`。
- 列表重复 upsert 不重置 detail_status。
- 失败页不推进 checkpoint。
- 超大详情文档不静默截断。

### 15.4 集成冒烟

```bash
python crawl_limetorrents.py --category Movies --start-page 2 --max-pages 1
python crawl_limetorrents.py --keyword "St Vincent" --max-pages 2
python crawl_detail_limetorrents.py --limit 1 --dry-run
python crawl_detail_limetorrents.py --limit 1
python crawl_detail_limetorrents.py --limit 1
```

## 16. 验收标准

1. 分类示例页成功解析列表结果并写入 `bt_info_list`。
2. 关键词搜索不混入 Sponsored Links。
3. 示例详情正确解析 Hash、magnet、torrent、tracker、文件清单和相关 torrent。
4. `files` 完整存入 `bt_info_detail`，不存在 `bt_info_files` 集合。
5. 重跑不产生重复列表或详情文档。
6. 列表重抓不会重置已完成详情状态。
7. 页面失败不会推进 checkpoint。
8. 批量关键词只有成功任务会写入 done 文件。
9. 自动化测试全部通过。
10. 实际 Chrome 冒烟验证通过，MongoDB 抽样字段与页面一致。

## 17. 已确认决策

| 决策点 | 选择 |
|---|---|
| 数据库 | `bt_limetorrents_spider_db` |
| 浏览分类 | 可配置，默认 `Movies` |
| 关键词分类 | 可配置，默认 `all` |
| 列表与详情 | 两阶段可续跑 |
| 详情粒度 | 完整详情 |
| 旧脚本 | 直接改名替换，不保留 1337x |
| 实现路线 | 聚焦替换，不做通用适配器 |
| 文件清单 | 合并到 `bt_info_detail.files` |
| 超大文档 | 明确失败，不静默截断 |
| 时间格式 | `yyyy-mm-dd hh:mm:ss` 字符串，并保留原始文本 |
| 浏览器 | DrissionPage + 真实 Chromium 导航 |
| 默认详情并发 | 2 |
