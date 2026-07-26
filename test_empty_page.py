"""LimeTorrents 列表空结果页判定的冒烟测试。

针对 bug: Cloudflare 软墙 / 未渲染返回的空表格骨架会被当成"有效页面"误判爬完。
修复: has_result_table() 判定页面是否含 table.table2;parse_listing 在没有真实
行时返回 []。失败页不推进 checkpoint,由 wrapper 重试。

直接跑:python test_empty_page.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import crawl_limetorrents as ck

failures = []


def check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        failures.append(msg)


def main():
    # 1. 完全无结果表 → has_result_table False
    check(ck.has_result_table("<html></html>") is False, "无 table.table2 → False")

    # 2. 只有表头 → has_result_table True,parse_listing 出 0 条
    only_header = "<table class='table2'><tr><th>Torrent Name</th></tr></table>"
    check(ck.has_result_table(only_header) is True, "仅表头骨架 → has_result_table True")
    items = ck.parse_listing(only_header, mode="browse", category="Movies")
    check(items == [], "仅表头 → parse_listing 0 条")

    # 3. 真实 fixture 必须能解析出 items(防回归)
    from tests.conftest import fixture
    real = fixture("limetorrents_browse_movies_page2.html")
    check(ck.has_result_table(real) is True, "真实浏览页 → has_result_table True")
    items = ck.parse_listing(real, mode="browse", category="Movies")
    check(items and len(items) >= 35, f"真实浏览页解析 ≥35 条(实际 {len(items)})")

    print()
    if failures:
        print(f"❌ {len(failures)} 个断言失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
