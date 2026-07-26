"""LimeTorrents 列表爬虫重构后的签名冒烟测试。

重构后:
- main(argv) 接收 argparse argv,自启 Chrome,并负责 quit()
- parse_args(argv) 双模式参数(浏览 / 关键词),分类与搜索分类分开
- parse_listing(html, *, mode, category, keyword) 解析 table.table2

详情爬虫 (crawl_detail_limetorrents):
- main(argv) -> int 接收 argv 自启 Chrome 并返回 exit code
- parse_args(argv) 单参签名
- run_one(tab, doc, coll_list, coll_detail, dry_run=False) -> str
- claim_one / mark_done / mark_failed 状态机
- html_cache_path / fetch_one / save_html_cache / build_pending_query

直接跑:python test_signature.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import inspect

import crawl_limetorrents as ck
import crawl_detail_limetorrents as dk

failures = []


def check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        failures.append(msg)


def main():
    # 1. main/parse_args/parse_listing 都存在且 callable
    check(callable(ck.main), "main 可调用")
    check(callable(ck.parse_args), "parse_args 可调用")
    check(callable(ck.parse_listing), "parse_listing 可调用")

    # 2. main 签名: 1 个参数 argv
    sig = inspect.signature(ck.main)
    params = list(sig.parameters.keys())
    check(params == ["argv"], f"main(argv) 单参签名: {params}")

    # 3. parse_args 签名: 1 个参数 argv
    sig = inspect.signature(ck.parse_args)
    params = list(sig.parameters.keys())
    check(params == ["argv"], f"parse_args(argv) 单参签名: {params}")

    # 4. parse_listing 签名: html + mode/category/keyword 等 keyword-only
    sig = inspect.signature(ck.parse_listing)
    params = list(sig.parameters.keys())
    check(
        params == ["html", "mode", "category", "keyword", "ref_now"],
        f"parse_listing 关键字参数: {params}",
    )

    # 5. main() 自启 Chrome,finally 调 quit(),不再依赖外部 page
    src = inspect.getsource(ck.main)
    check("ChromiumPage(" in src, "main() 创建 ChromiumPage(自启 Chrome)")
    check("browser.quit()" in src, "main() 在 finally 调 browser.quit()")
    check("auto_port(True)" in src, "main() 用 auto_port 启独立 Chrome")

    # 6. checkpoint 三个函数都是新的 3 参签名
    sig = inspect.signature(ck.load_checkpoint)
    params = list(sig.parameters.keys())
    check(
        params == ["mode", "category", "keyword"],
        f"load_checkpoint(mode, category, keyword): {params}",
    )
    sig = inspect.signature(ck.clear_checkpoint)
    params = list(sig.parameters.keys())
    check(
        params == ["mode", "category", "keyword"],
        f"clear_checkpoint(mode, category, keyword): {params}",
    )
    sig = inspect.signature(ck.save_checkpoint)
    params = list(sig.parameters.keys())
    check(params == ["state"], f"save_checkpoint(state) 单参: {params}")

    # 7. upsert_listing 已是幂等接口
    sig = inspect.signature(ck.upsert_listing)
    params = list(sig.parameters.keys())
    check(
        params == ["coll", "item"],
        f"upsert_listing(coll, item): {params}",
    )

    # 8. crawl_detail_limetorrents: main(argv) 单参并自启 Chrome
    check(callable(dk.main), "detail.main 可调用")
    check(callable(dk.parse_args), "detail.parse_args 可调用")
    check(callable(dk.run_one), "detail.run_one 可调用")
    sig = inspect.signature(dk.main)
    params = list(sig.parameters.keys())
    check(params == ["argv"], f"detail.main(argv) 单参签名: {params}")
    src = inspect.getsource(dk.main)
    check("ChromiumPage(" in src, "detail.main() 创建 ChromiumPage(自启 Chrome)")
    check("browser.quit()" in src, "detail.main() 在 finally 调 browser.quit()")

    # 9. detail.parse_args(argv) 单参签名
    sig = inspect.signature(dk.parse_args)
    params = list(sig.parameters.keys())
    check(params == ["argv"], f"detail.parse_args(argv) 单参签名: {params}")

    # 10. detail.run_one(tab, doc, coll_list, coll_detail, dry_run=False)
    sig = inspect.signature(dk.run_one)
    params = list(sig.parameters.keys())
    check(
        params == ["tab", "doc", "coll_list", "coll_detail", "dry_run"],
        f"detail.run_one(tab, doc, coll_list, coll_detail, dry_run): {params}",
    )

    # 11. 状态机三件套
    sig = inspect.signature(dk.claim_one)
    params = list(sig.parameters.keys())
    check(params == ["coll_list", "doc_id"], f"detail.claim_one(coll_list, doc_id): {params}")
    sig = inspect.signature(dk.mark_done)
    params = list(sig.parameters.keys())
    check(params == ["coll_list", "doc_id"], f"detail.mark_done(coll_list, doc_id): {params}")
    sig = inspect.signature(dk.mark_failed)
    params = list(sig.parameters.keys())
    check(
        params == ["coll_list", "doc_id", "error_msg"],
        f"detail.mark_failed(coll_list, doc_id, error_msg): {params}",
    )

    # 12. 缓存与查询
    sig = inspect.signature(dk.html_cache_path)
    params = list(sig.parameters.keys())
    check(params == ["detail_url"], f"detail.html_cache_path(detail_url): {params}")
    sig = inspect.signature(dk.fetch_one)
    params = list(sig.parameters.keys())
    check(params == ["tab", "url"], f"detail.fetch_one(tab, url): {params}")
    sig = inspect.signature(dk.save_html_cache)
    params = list(sig.parameters.keys())
    check(
        params == ["detail_url", "html"],
        f"detail.save_html_cache(detail_url, html): {params}",
    )
    sig = inspect.signature(dk.build_pending_query)
    params = list(sig.parameters.keys())
    check(params == ["keyword"], f"detail.build_pending_query(keyword): {params}")

    # 13. 详情爬虫不再含 1337x 字样
    src = inspect.getsource(dk)
    check("1337x" not in src, "crawl_detail_limetorrents 无 1337x 字样")

    print()
    if failures:
        print(f"❌ {len(failures)} 个断言失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
