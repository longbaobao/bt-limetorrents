"""Compare HTML returned by curl_cffi (chrome131) and DrissionPage for the same URL.

Saves both bodies to data/diag/<timestamp>/ and computes:
- byte-level size diff
- line-level common / diff (via difflib)
- which 'table2' / 'td.tdleft' / '-torrent-' / 'hb1' markers exist in each
"""
import json
import os
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

os.environ.setdefault("PATH", r"C:\Program Files\Git\mingw64\bin;" + os.environ.get("PATH", ""))

URL = "https://www.limetorrents.fun/browse-torrents/Movies/date/2/"
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = Path(__file__).resolve().parents[1] / "data" / "diag" / f"compare_{TS}"
OUT.mkdir(parents=True, exist_ok=True)


def markers(html: str) -> dict:
    text = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {
        "len_bytes": len(html),
        "len_text": len(text),
        "title": (re.search(r"<title>(.*?)</title>", html).group(1)
                 if re.search(r"<title>", html) else None),
        "class_table2_count": len(re.findall(r'class="table2"', html)),
        "td_tdleft_count": len(re.findall(r'td\.tdleft|class="tdleft', html)),
        "detail_links": len(re.findall(r"-torrent-\d+\.html", html)),
        "hb1_count": len(re.findall(r"\bhb1\b", html)),
        "st_vincent_present": "St Vincent" in html,
    }


def fetch_curl_cffi() -> tuple[str | None, str | None]:
    try:
        from curl_cffi import requests as creq

        r = creq.get(URL, impersonate="chrome131", timeout=30)
        return r.text, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def fetch_drission() -> tuple[str | None, str | None]:
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage

        opts = ChromiumOptions().auto_port(True)
        browser = ChromiumPage(opts)
        try:
            browser.get(URL)
            try:
                browser.wait_ele("css:table.table2", timeout=45)
            except Exception:
                pass
            return browser.html, None
        finally:
            browser.quit()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    summary: dict = {"url": URL, "started_at": TS}

    cc_body, cc_err = fetch_curl_cffi()
    dr_body, dr_err = fetch_drission()

    if cc_body is not None:
        (OUT / "curl_cffi.html").write_text(cc_body, encoding="utf-8")
    if dr_body is not None:
        (OUT / "drission.html").write_text(dr_body, encoding="utf-8")

    summary["curl_cffi"] = {"error": cc_err} if cc_err is None else {"error": cc_err}
    if cc_body is not None:
        summary["curl_cffi"] = markers(cc_body)
    summary["drission"] = {"error": dr_err} if dr_err is None else {"error": dr_err}
    if dr_body is not None:
        summary["drission"] = markers(dr_body)

    if cc_body and dr_body:
        # line-level diff
        cc_lines = cc_body.splitlines()
        dr_lines = dr_body.splitlines()
        sm = SequenceMatcher(None, cc_lines, dr_lines, autojunk=False)
        identical = sum(1 for tag, _, _, _, _ in sm.get_opcodes() if tag == "equal")
        summary["line_diff"] = {
            "cc_lines": len(cc_lines),
            "dr_lines": len(dr_lines),
            "identical_chunks": identical,
            "identical_line_count": sum(
                1 for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag == "equal"
                for _ in range(i1, i2)
            ),
        }

    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())