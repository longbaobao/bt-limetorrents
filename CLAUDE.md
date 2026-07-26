# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 DrissionPage + headless Chrome 的 LimeTorrents 种子爬虫。通过 LimeTorrents 分类浏览页与关键词搜索页抓取种子条目（标题、分类、大小、做种/下载数、添加时间、详情链接、torrent 直链），再进入详情页抓取 info_hash、magnet、torrent 直链、文件树、Trackers、相关种子与评论数。列表与详情分别写入 MongoDB 的两个集合,支持 checkpoint 续跑、幂等 upsert、CAS 状态机和 dry-run。DrissionPage 自启独立 headless Chrome（每个子脚本独占 user-data-dir 与端口），不接管外部浏览器。

## 运行

无打包、无构建系统。直接以脚本方式执行。**所有 Python 命令使用 `.venv/Scripts/python.exe`**（Windows 下 Python 3.11 已装依赖）。

最小命令示例：

```text
.venv/Scripts/python.exe crawl_limetorrents.py --category Movies
.venv/Scripts/python.exe crawl_limetorrents.py --keyword "St Vincent"
.venv/Scripts/python.exe crawl_limetorrents_by_keys.py --search-category all
.venv/Scripts/python.exe crawl_detail_limetorrents.py --limit 10 --concurrency 2
.venv/Scripts/python.exe init_limetorrents_db.py
```

其他常见调用：

- `.venv/Scripts/python.exe crawl_limetorrents.py --category Music --start-page 1 --max-pages 3` — 浏览指定分类前 3 页。
- `.venv/Scripts/python.exe crawl_limetorrents.py --keyword "St Vincent" --max-pages 2` — 关键词搜索前 2 页（已存在的 URL 不重复插入）。
- `.venv/Scripts/python.exe crawl_limetorrents.py --keyword "House" --search-category tv-shows` — 限定搜索分类。
- `.venv/Scripts/python.exe crawl_detail_limetorrents.py --keyword "St-Vincent" --limit 20` — 仅处理指定 keyword 的详情。
- `.venv/Scripts/python.exe crawl_detail_limetorrents.py --retry-failed` — 把 failed → pending 重抓。
- `.venv/Scripts/python.exe crawl_detail_limetorrents.py --force --concurrency 4` — 强制重跑所有记录（无视 status）。
- `.venv/Scripts/python.exe crawl_detail_limetorrents.py --keyword "X" --limit 1 --dry-run` — 解析不写库,验证结构。
- `.venv/Scripts/python.exe -m pytest -v` — 跑全套离线 pytest。
- `.venv/Scripts/python.exe -m compileall -q crawl_limetorrents.py crawl_limetorrents_by_keys.py crawl_detail_limetorrents.py init_limetorrents_db.py tests` — 编译检查。

依赖见 `requirements.txt`（`uv pip install -r requirements.txt`）。`DrissionPage` 4.1.1.4 用了 `auto_port(True)` 强制独立 Chrome 进程,绕开 Windows 上 `.headless(True)` 不监听 ws endpoint 的 bug。

## 架构与数据流

```
crawl_limetorrents.main(argv)               # 列表入口(浏览 / 搜索 双模式)
    │
    ├─ parse_args(argv)                       # CLI 解析(--keyword 决定 search / browse)
    ├─ load_checkpoint(mode, category, keyword)  # 读 data/checkpoints/limetorrents-<md5>.json
    ├─ build_browse_url(category, page)       # /browse-torrents/<Cat>/date/<page>/
    ├─ build_search_url(category, kw, page)   # /search/<cat-or-all>/<slug>/
    │
    ├─ ChromiumPage(ChromiumOptions().auto_port(True))   # DrissionPage 自启 headless Chrome
    │
    ├─ 循环:
    │     ├─ fetch_with_cf_bypass(tab, url, "css:table.table2", max_wait=45)
    │     │     # 处理 Cloudflare 5秒盾(请稍候 / Just a moment / cf_chl_opt …)
    │     ├─ has_result_table(html)           # 确认页面含 table.table2 才推进
    │     ├─ parse_listing(html, mode, category, keyword)   # 解析结果行(忽略 table.table3)
    │     ├─ detect_next_url(html, url)       # 找 "next page" 链接(用实际 href 拼接)
    │     └─ upsert_listing(coll, item)       # 幂等 upsert(by _id=md5(detail_url))
    │
    └─ save_checkpoint / clear_checkpoint     # 原子写 checkpoint,完成后清理

crawl_limetorrents_by_keys.main(argv)        # 多 key 并发 wrapper
    │
    ├─ load_keys()  ← data/keys.txt
    ├─ load_done()  ← data/keys-done.txt
    ├─ ThreadPoolExecutor(concurrency).submit(run_one, key)
    │     └─ subprocess.run(["crawl_limetorrents.py", "--keyword", key, ...])
    └─ append_done(key) on rc=0               # 失败不写 done → 下次续跑

crawl_detail_limetorrents.main(argv)         # 详情入口
    │
    ├─ parse_args(argv)                       # --limit / --concurrency / --dry-run / --force / --retry-failed
    ├─ rescue_orphaned_processing()           # 启动时把卡 processing 的孤儿恢复为 pending
    ├─ build_pending_query(keyword)           # 默认 detail_status=pending
    │
    ├─ 循环:
    │     ├─ claim_one → CAS 把 pending → processing
    │     ├─ run_one(tab, doc, ..., dry_run):
    │     │     ├─ fetch_one(tab, url)         # CSS 目标:div.torrentinfo
    │     │     ├─ save_html_cache(url, html)  # data/html/limetorrents/<md5>.html
    │     │     ├─ parse_detail(html, url)
    │     │     │     ├─ parse_basic_info   # info_hash / added_at / category / total_size / stream
    │     │     │     ├─ parse_trackers    # table3 (Trackers List)
    │     │     │     ├─ parse_files       # .fileline + 目录栈 → (entries, declared_count)
    │     │     │     ├─ parse_related_torrents / parse_comments_count
    │     │     └─ upsert_detail(coll_detail, parsed)
    │     ├─ mark_done(doc_id) | mark_failed(doc_id, error)
    │     └─ DocumentTooLarge → 立即 failed,文件不截断
    │
    └─ 退出前 reverse rescue_orphaned_processing() → 本次未完成的 processing 回滚为 pending

init_limetorrents_db.main()                  # 一次性建库 + 索引(可重跑)
    └─ bt_info_list 补齐 detail_status 字段 + 4 个索引
    └─ bt_info_detail 建 detail_url(unique) + info_hash 索引
```

## 关键约定

- **数据指纹**：列表与详情共用 `md5(detail_url)` 作为 `_id`。详情也走 `_id=md5(detail_url)`,所以同一 URL 的详情 `replace_one(..., upsert=True)` 自动覆盖(幂等)。
- **跨模式去重**:`upsert_listing` 用 `$addToSet` 累积 `keywords` 与 `discovery_modes`,同一详情被多 key / 多 mode 发现不重复插入。已 done 的详情不会被列表重抓重置为 pending(`upsert` 不会改 `first_seen_at` / `detail_status`)。
- **状态机**:`detail_status ∈ {pending, processing, done, failed}`。`claim_one` 是 CAS(`pending → processing`),`mark_done / mark_failed` 处理后状态。`DocumentTooLarge` 不切片不截断,直接 failed。
- **列表解析选择器**:`table.table2`(真实结果);`table.table3` 是 Sponsored Links 必须忽略。详情链接 regex: `-torrent-\d+\.html`。
- **搜索 URL 时间过滤**:无,搜索结果按 site 默认顺序。
- **MongoDB**:`mongodb://localhost:27017/` → DB `bt_limetorrents_spider_db` → collections `bt_info_list` / `bt_info_detail`。
  - `bt_info_list` 字段:`_id / name / detail_url / torrent_url / category / added_text / added_at / size / seeders / leechers / observed_at / source / discovery_mode / keywords / discovery_modes / first_seen_at / last_seen_at / detail_status / detail_started_at / detail_processed_at / detail_error`
  - `bt_info_detail` 字段:`_id / detail_url / name / info_hash / added_text / added_at / category / total_size / meta_description / resource_links{magnet,torrent,stream} / trackers[] / tracker_count / successful_tracker_count / failed_tracker_count / files[] / declared_file_count / file_entry_count / related_torrents[] / comments_count / html_cache_path / source / parsed_at`
  - 索引:`bt_info_list` 上 `detail_url(unique)` / `detail_status` / `keywords` / `(category, added_at DESC)`;`bt_info_detail` 上 `detail_url(unique)` / `info_hash`。
- **Checkpoint**:每个 (mode, category, keyword) 独立 `data/checkpoints/limetorrents-<md5>.json`,原子写(.tmp + replace)。失败页不推进 checkpoint,留给 wrapper 重试。
- **CLI**:
  - `crawl_limetorrents.py [--keyword KW] [--category CAT=Movies] [--search-category CAT=all] [--start-page N=1] [--max-pages N=0] [--page-sleep S=1.0]`
  - `crawl_limetorrents_by_keys.py [--search-category CAT=all] [-c N=1]`(N 受环境变量 `CRAWL_LIMETORRENTS_CONCURRENCY` 影响)
  - `crawl_detail_limetorrents.py [-c N=2] [-b N=100] [-p S=1.0] [-l N=0] [-k KW] [--force] [--retry-failed] [--dry-run]`

## 分类与搜索词合法值

- 浏览分类(`--category`):`anime / applications / games / movies / music / tv-shows / other`(`tv` / `TV shows` 等价于 `tv-shows`)。`normalize_category` 严格匹配,无效值抛 `ValueError`。
- 搜索分类(`--search-category`):除上面 7 个浏览分类外,允许 `all`(其余一律拒绝)。

## 已知 TODO / 占位实现

- `crawl_detail_limetorrents.run_one` 中 `DocumentTooLarge` 已显式失败但不切片不截断,这是**预期**行为(失败后保留原 `files`,方便人工 review)。
- `data/keys-done.txt` 累计成功 key;`data/keys.txt` 失败 key 不写 done,下次 wrapper 重跑自动从中断页续爬(checkpoint 落盘)。

## 修改时的注意

- 修改 `crawl_limetorrents.py` 的选择器或 regex:跑 `tests/test_limetorrents_listing.py` 等离线测试对照 fixture 看匹配数;真实验证用 `crawl_limetorrents.py --max-pages 1` 跑单页。
- 新增搜索来源:需要 (1) 复刻 `parse_result_row` 字段名 / 字段约定;(2) 走 `upsert_listing` 幂等 upsert;(3) checkpoint 文件名 hash 与现有一致(无需改);(4) MongoDB schema 不变。
- 新增详情字段:在 `parse_detail` dict 里加,pytest 加一条覆盖断言,无需改 schema(只往里加字段不破坏既有 doc)。
- 项目无锁文件(`uv pip freeze > requirements.txt` 是当前约定的快照方式);依赖变更请同步刷新 `requirements.txt`。