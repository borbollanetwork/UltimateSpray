#!/usr/bin/env python3
"""Scrape Google search results through an UltimateSpray proxy."""

import argparse
import sys
import threading
import time
from queue import Queue

from bs4 import BeautifulSoup

from ultimatespray.spray import RotatingProxy

add_lock = threading.Lock()
count_queue: Queue = Queue()
search_results: set = set()

parser = argparse.ArgumentParser(description="UltimateSpray Google scraper")
parser.add_argument("--proxy", action="append", required=True,
                    help="Proxy URL (repeat for a rotation pool)")
parser.add_argument("--search", required=True, help="Search term")
parser.add_argument("--pages", type=int, default=1000, help="Result pages (default 1000)")
args = parser.parse_args()

proxy = RotatingProxy(args.proxy)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.14; rv:65.0) "
        "Gecko/20100101 Firefox/65.0"
    )
}


def check_query(count: int, query: str) -> None:
    resp = proxy.get(f"/search?q={query}&start={count}&num=100", headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")
    with add_lock:
        for g in soup.find_all("div", class_="r"):
            link = g.find_all("a")[0]["href"]
            title = g.find_all("h3")[0]
            search_results.add(f"{title.text} ({link})")


def worker(query: str) -> None:
    while True:
        current = count_queue.get()
        try:
            check_query(current, query)
        finally:
            count_queue.task_done()


def main() -> int:
    for _ in range(100):
        t = threading.Thread(target=worker, args=(args.search,), daemon=True)
        t.start()

    start = time.time()
    count_queue.put(0)
    for count in range(1, args.pages + 1)[99::100]:
        count_queue.put(count)
    count_queue.join()

    for x in sorted(search_results):
        print(x)
    print(f"Results: {len(search_results)}")
    print(f"Execution time: {time.time() - start:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
