# 从真实 LimeTorrents 抓取三个列表/详情 fixture HTML。
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
