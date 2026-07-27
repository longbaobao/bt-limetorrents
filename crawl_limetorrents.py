"""
LimeTorrents 列表与关键词爬虫：分类浏览或单关键词搜索，分页翻页、checkpoint
续跑、列表解析、幂等 upsert。DrissionPage 拉自己的 Chrome,每个子脚本独立管理
浏览器生命周期。

成功加载且含真实结果表(table.table2)后再推进 checkpoint；失败页不写
checkpoint,以免 wrapper 重试时丢失中间进度。

Headless 模式：DrissionPage 4.1.1.4 的 .headless(True) 在 Windows 上有 bug
(传 --headless=new,Chrome 不监听 ws endpoint,DrissionPage 连不上报 404)。
变通方案:用 set_argument('--headless')(老式 flag),Chrome 会监听 ws,能正常
启 headless 无窗口。.set_headless() 旧 API 在 4.1.1.4 不存在。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import argparse
import json
import re
import time
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin

from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup
from pymongo import MongoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

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
    # 站点在 Related 单元格偶尔会写 "Anime." / "Movies ;" 等带末尾标点的别名,
    # 先 collapse 空白 → trim 标点 → 再 strip 残余空白,确保查表时 key 严格匹配 BROWSE_CATEGORIES。
    cleaned = re.sub(r"\s+", " ", value.strip())
    cleaned = re.sub(r"[.,;:!\?\"]+$", "", cleaned).strip().lower()
    if allow_all and cleaned == "all":
        return "all"
    try:
        return BROWSE_CATEGORIES[cleaned]
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

# 旧 Playwright 时代用的 CDP 端口常量,留作向后兼容(给 crawl_detail_1337x.py 复用),
# 本脚本已不再使用(DrissionPage 用 auto_port 自启)。如果 detail crawler 也迁走,即可删除。
CDP_URL = "http://127.0.0.1:9222"

# 单页之间间隔（秒），礼貌爬取
PAGE_SLEEP = 1.0

# 断点续爬 checkpoint 目录:每个 (mode, category, keyword) 一个 JSON。
# 子进程被 wrapper 超时 kill 后,重试可从中断页继续,而不是重头爬(避免大 key 永远超时无进展)。
CHECKPOINT_DIR = Path("data/checkpoints")


def _query_key(mode: str, category: str, keyword: str | None) -> str:
    return f"{mode}|{category}|{keyword or ''}"


def checkpoint_path(mode: str, category: str, keyword: str | None) -> Path:
    """(mode, category, keyword) → checkpoint 文件路径, md5 摘要防冲突 / 防非法字符。"""
    digest = hashlib.md5(_query_key(mode, category, keyword).encode("utf-8")).hexdigest()
    return CHECKPOINT_DIR / f"limetorrents-{digest}.json"


def load_checkpoint(mode: str, category: str, keyword: str | None) -> dict | None:
    """读取 checkpoint。无 checkpoint 返回 None。"""
    path = checkpoint_path(mode, category, keyword)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取 checkpoint 失败({path.name}): {e}，当作无 checkpoint 从头开始")
        return None


def save_checkpoint(state: dict) -> None:
    """原子写 checkpoint(先写 .tmp 再 replace),防止子进程被 kill 时留下半截损坏文件。

    state 必须包含 query_type/category/keyword(决定文件名);current_page/next_url/
    updated_at 是主流程需要读回的字段。
    """
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(
        state["query_type"],
        state["category"],
        state.get("keyword"),
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)  # 同盘原子替换


def clear_checkpoint(mode: str, category: str, keyword: str | None) -> None:
    """全部爬完后删除 checkpoint。"""
    checkpoint_path(mode, category, keyword).unlink(missing_ok=True)


def upsert_listing(coll, item: dict) -> bool:
    """将列表抓取的结果幂等写入 MongoDB。

    - $set 覆盖 name/torrent_url/category/added/size/seeders/leechers/source/last_seen_at
      (用最新观察值刷新列表字段)。
    - $setOnInsert 设定首次见到时的 first_seen_at + detail_status=pending +
      状态字段(避免覆盖已 processing/done/failed 的记录)。
    - $addToSet 累积 keywords 和 discovery_modes(同一 detail 可能被多 key/多 mode 发现)。

    注意：`keywords` 字段同一 update 中只能被一个 operator 操作。
    - 优先用 $addToSet(累积关键词)。
    - 仅当本次不提供 keyword(related 路径)时,在 $setOnInsert 中初始化为空数组,
      否则 $set 不会触碰 keywords,避免与 $addToSet 冲突。
    - 调用方传入 item['keywords'] 会被丢弃(本函数不直接信 caller 的 keywords),
      统一从 item.get('keyword') 单数源派生。

    返回 True 表示本次为 insert, False 表示命中已有记录。
    """
    stored = {
        key: value
        for key, value in item.items()
        if key
        not in {
            "keyword",
            "keywords",
            "discovery_mode",
            "observed_at",
        }
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

    update = {
        "$set": stored,
        "$setOnInsert": set_on_insert,
        "$addToSet": add_to_set,
    }
    result = coll.update_one({"_id": item["_id"]}, update, upsert=True)
    return result.upserted_id is not None


# ============================================================================
# LimeTorrents 列表解析 (Task 3)
# 列表/搜索页结构：
#   table.table2 → 真实结果行(每行: td.tdleft 含 name/detail/torrent 链接,
#                td.tdnormal x2 = Added/Size, td.tdseed, td.tdleech, td.tdright Health)
#   table.table3 → Sponsored Links (必须忽略)
# 详情链接正则: -torrent-<数字>.html
# Torrent 直链: .torrent (实际是 itorrents.net/HASH.torrent 形式)
# ============================================================================

DETAIL_HREF_RE = re.compile(r"-torrent-\d+\.html(?:$|\?)", re.IGNORECASE)


def _as_int(text: str) -> int:
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else 0


def extract_health(td) -> int | None:
    r"""从列表行 td.tdright 提取健康度 hbN 整数。

    缺 div / class 名不匹配 hb(\d+) / N 不在 [1, 10] 均返回 None，
    原样保留空值，绝不重置为 0 或越界。
    """
    if td is None:
        return None
    health_div = td.select_one('div[class*="hb"]')
    if health_div is None:
        return None
    for class_name in health_div.get("class", []):
        match = re.fullmatch(r"hb(\d+)", class_name)
        if match:
            value = int(match.group(1))
            return value if 1 <= value <= 10 else None
    return None


def extract_added_category(text: str, fallback: str) -> tuple[str, str]:
    """把 'Added' 单元格文本切成 (added_text, category)。

    搜索结果 added_text 形如 '10 hours ago - in Movies';
    浏览页形如 '10 hours ago'(无 in 子串)→ 保留原文 + fallback。
    """
    match = re.search(r"\s+-?\s*in\s+(.+?)\s*$", text, re.IGNORECASE)
    if not match:
        return text.strip(), fallback
    category = normalize_category(match.group(1), allow_all=False)
    added_text = text[:match.start()].strip()
    return added_text, category


def parse_result_row(row, *, fallback_category: str, ref_now: datetime) -> dict | None:
    """解析 LimeTorrents 单行结果。

    返回 None 表示该行无效(没有 -torrent-N.html 详情链接;
    通常是表头 Sponsored 行的 td.tdleft 或损坏结构)。
    """
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
    health_cell = row.select_one("td.tdright")
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
        "health": extract_health(health_cell),
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
    """解析 LimeTorrents 浏览/搜索列表(LimeTorrents 列表层级,与 1337x parse_listing 不同)。

    只走 table.table2;Sponsored 的 table.table3 自动忽略。
    """
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
    """LimeTorrents 列表页是否含真实结果表(table.table2)。"""
    return BeautifulSoup(html, "html.parser").select_one("table.table2") is not None


def detect_next_url(html: str, current_url: str) -> str | None:
    """从分页栏找 Next page 链接,基于当前页 URL 拼接绝对地址。"""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select("a[href]"):
        if link.get_text(" ", strip=True).lower() == "next page":
            return urljoin(current_url, link["href"])
    return None


def load_page_with_retry(tab, url: str, page_num: int, retries: int = 3) -> str | None:
    """带重试地加载页面，超出重试次数返回 None（让上层跳过）。

    DrissionPage API(传入的 tab 实际就是 ChromiumPage,本身即一个 tab):
      tab.get(url)               — 导航
      tab.wait.load_start()      — 等同 Playwright wait_until="domcontentloaded"
      tab.ele(sel, timeout=N)    — 等同 Playwright wait_for_selector(sel, timeout=N*1000)
      tab.html                   — 等同 page.content()

    Cloudflare 5秒盾由 fetch_with_cf_bypass 内置处理 (见下), 无需手动 retry。

    目标选择器 "css:table.table2" — 只看结果表(LimeTorrents 列表),不看 Sponsored。
    没有结果表骨架(CF 软墙/未渲染)由 has_result_table() 在外层判失败。
    """
    try:
        return fetch_with_cf_bypass(tab, url, "css:table.table2", max_wait=45)
    except Exception as e:
        logger.warning(f"第 {page_num} 页加载失败: {type(e).__name__}: {str(e)[:80]}")
        return None


# Cloudflare 5秒盾特征字符串（CDN 在中国镜像成中文 "请稍候…"）
_CF_CHALLENGE_MARKERS = (
    "请稍候",
    "Just a moment",
    "cf_chl_opt",
    "challenge-form",
    "Checking your browser",
)


def fetch_with_cf_bypass(tab, url: str, target_selector: str, max_wait: int = 45) -> str:
    """访问 URL, 自动处理 Cloudflare 5秒盾, 轮询直到目标元素出现或超时。

    策略:
      1. tab.get(url) 触发导航
      2. wait.load_start 等 DOMContentLoaded
      3. 检查 HTML 是否含 Cloudflare 挑战页特征 (5秒盾)
         - 是: 等 5s 后重 fetch (challenge JS 通常 5s 后自动 redirect)
      4. 检查目标元素是否出现
         - 否: 等 3s 后重 fetch (页面可能还在加载)
      5. 出现 → 返回 html
      6. max_wait 秒后仍未达成 → 抛 TimeoutError

    ⚠️ HEADLESS 限制: Cloudflare bot 检测对 headless Chrome 极度激进, 会持续返回
    盾页 (即使每次 fetch 都等 5s), 此 helper 无法绕过。要爬 1337x 必须用
    visible Chrome 模式 (不传 --headless), 或预热 Chrome profile 注入 cf_clearance
    cookie 后再 headless。详见 crawl_1337x_by_key.py 顶部 docstring。

    Raises: TimeoutError (目标元素未出现) / 原始 DrissionPage 异常。
    """
    from DrissionPage.errors import ElementNotFoundError, PageDisconnectedError

    deadline = time.time() + max_wait
    attempts = 0
    last_stage = "init"
    while time.time() < deadline:
        attempts += 1
        try:
            tab.get(url)
            tab.wait.load_start()
            html = tab.html
            # 检测 Cloudflare 5秒盾中间页
            if any(m in html for m in _CF_CHALLENGE_MARKERS):
                last_stage = "cf_shield"
                logger.info(
                    f"  fetch 第 {attempts} 次: 遇 Cloudflare 5秒盾,等 5s 后重试"
                )
                time.sleep(5)
                continue
            # 检测目标元素
            try:
                tab.ele(target_selector, timeout=8)
                last_stage = "ok"
                return html
            except ElementNotFoundError:
                last_stage = "no_target"
                logger.info(
                    f"  fetch 第 {attempts} 次: 未找到 {target_selector},等 3s 后重试"
                )
                time.sleep(3)
                continue
        except PageDisconnectedError as e:
            last_stage = "page_disconnected"
            logger.warning(f"  fetch 第 {attempts} 次: 页面断开 {e}, 等 2s 后重试")
            time.sleep(2)
            continue
        except Exception as e:
            last_stage = f"{type(e).__name__}"
            logger.warning(
                f"  fetch 第 {attempts} 次: {type(e).__name__}: {str(e)[:80]}, 等 3s 后重试"
            )
            time.sleep(3)
            continue

    raise TimeoutError(
        f"fetch 等 {max_wait}s 仍未拿到 {target_selector} (尝试 {attempts} 次, 最后阶段: {last_stage})"
    )


def main(argv: list[str] | None = None) -> int:
    """双模式主循环:浏览(--category)或搜索(--keyword)。

    关键不变量: 失败页(超时/CF 未解除/缺 table.table2/0 items + 有 next_url)
    不推进 checkpoint — 重试从同一页继续,确保不丢数据。

    返回 0=完成, 2=初始页失败, 3=空中间页。
    """
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
    if not url:
        logger.error(
            f"{mode} 模式无起始 url: mode={mode} category={category} "
            f"page={page_number} keyword={args.keyword!r}"
        )
        return 2

    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLL_NAME]
    logger.info(f"MongoDB 已连接: {MONGO_URI}{DB_NAME}.{COLL_NAME}")

    processed_pages = 0
    browser = ChromiumPage(ChromiumOptions().auto_port(True))
    try:
        while url:
            html = load_page_with_retry(browser, url, page_number)
            if html is None or not has_result_table(html):
                logger.warning(
                    f"第 {page_number} 页失败(加载={html is None} 或无结果表),"
                    f"不推进 checkpoint,留给上层重试"
                )
                return 2
            items = parse_listing(
                html,
                mode=mode,
                category=category,
                keyword=args.keyword,
            )
            next_url = detect_next_url(html, url)
            if not items and next_url:
                logger.warning(
                    f"第 {page_number} 页解析出 0 条但仍有 next_url,疑似站点改版,"
                    f"不推进 checkpoint"
                )
                return 3
            new_count = 0
            for item in items:
                if upsert_listing(coll, item):
                    new_count += 1
            logger.info(
                f"[{mode}/{category}] 第 {page_number} 页解析 {len(items)} 条"
                f" 新写入 {new_count} 条"
            )
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
                logger.info(
                    f"已达 --max-pages={args.max_pages} 上限,保留 checkpoint 供下次续跑"
                )
                return 0
            if next_url is None:
                logger.info(
                    f"{mode}/{category} 已无 next_url,全部爬完,清除 checkpoint"
                )
                clear_checkpoint(mode, category, args.keyword)
                return 0
            url = next_url
            page_number += 1
            time.sleep(args.page_sleep)
        logger.info(
            f"{mode}/{category} 主循环正常退出,清除 checkpoint"
        )
        clear_checkpoint(mode, category, args.keyword)
        return 0
    finally:
        try:
            browser.quit()
            logger.info("Chrome 已关闭")
        except Exception as e:
            logger.warning(f"关闭 Chrome 异常: {type(e).__name__}: {e}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 参数解析。

    mode 决策: 提供 --keyword → search,否则 browse。
    浏览分类默认 Movies;搜索分类默认 all(允许 all,不允许其他无效分类)。
    """
    parser = argparse.ArgumentParser(
        description="LimeTorrents 列表与关键词爬虫",
    )
    parser.add_argument(
        "--keyword",
        help="提供后进入关键词搜索模式;省略则进入分类浏览模式",
    )
    parser.add_argument(
        "--category",
        default="Movies",
        help="浏览模式下的分类,默认 Movies",
    )
    parser.add_argument(
        "--search-category",
        default="all",
        help="搜索模式下的分类,默认 all",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="起始页码,默认 1;仅无 checkpoint 时生效",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="本次最多处理的页数,0=直到没有下一页",
    )
    parser.add_argument(
        "--page-sleep",
        type=float,
        default=PAGE_SLEEP,
        help="页间间隔秒数",
    )
    args = parser.parse_args(argv)
    if args.start_page < 1:
        parser.error("--start-page 必须大于等于 1")
    if args.max_pages < 0:
        parser.error("--max-pages 必须大于等于 0")
    if args.page_sleep < 0:
        parser.error("--page-sleep 不能为负")
    args.category = normalize_category(args.category)
    args.search_category = normalize_category(args.search_category, allow_all=True)
    if args.keyword is not None:
        # 立刻原地规范化,后续 build_search_url 拿到的就是 slug,
        # 避免空格/URL 不安全字符在路径里被二次误判。
        slug = slugify_keyword(args.keyword)
        # slugify_keyword 已把连续空白折叠为 "-";若 keyword 内含多个空格,
        # 这里能立刻捕获 " " 没被折叠的退化结果。
        assert " " not in slug, f"slug 不应含未折叠空格: {slug!r}"
        assert slug, "slug 不可为空"
        args.keyword = slug
    return args


if __name__ == "__main__":
    sys.exit(main())
