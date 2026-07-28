"""Reproduce concurrent 3-worker scenario with shared CurlCffiBackend."""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["PATH"] = r"C:\Program Files\Git\mingw64\bin;" + os.environ.get("PATH", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from request_client import CurlCffiBackend  # noqa: E402

backend = CurlCffiBackend(impersonate="chrome131", timeout=20)
print("warmup done")

def fetch(url):
    try:
        return ("ok", len(backend.fetch(url)))
    except Exception as e:
        return ("err", f"{type(e).__name__}: {str(e)[:80]}")


URL = "https://www.limetorrents.fun/browse-torrents/Movies/date/2/"
N = 30
WORKERS = 3

t0 = time.time()
errors = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futures = [ex.submit(fetch, URL) for _ in range(N)]
    for i, f in enumerate(futures):
        status, payload = f.result()
        if status == "err":
            errors.append((i, payload))
            print(f"req {i:02d}: ERR {payload}")
print(f"\n{N} requests, {len(errors)} errors, elapsed {time.time()-t0:.1f}s")
