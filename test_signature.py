"""LimeTorrents 列表爬虫重构后的签名冒烟测试。

重构后:
- main(argv) 接收 argparse argv,自启 Chrome,并负责 quit()
- parse_args(argv) 双模式参数(浏览 / 关键词),分类与搜索分类分开
- parse_listing(html, *, mode, category, keyword) 解析 table.table2

直接跑:python test_signature.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import inspect

import crawl_limetorrents as ck

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

    print()
    if failures:
        print(f"❌ {len(failures)} 个断言失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
