#!/usr/bin/env python3
"""Collect the Hacker News front page and store it in SQLite.

    python collector.py                 collect once
    python collector.py --every 10      keep collecting every 10 minutes (Ctrl+C to stop)

Uses the official Hacker News API: https://github.com/HackerNews/API
"""

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import db

API_BASE = os.environ.get("HN_API_BASE", "https://hacker-news.firebaseio.com/v0")
USER_AGENT = "hn-frontpage-tracker/1.0 (personal project)"

log = logging.getLogger("collector")


def fetch_json(session, url, retries=3, timeout=15):
    """GET a JSON document, retrying with a growing pause if the request fails."""
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            log.warning("%s - retrying in %ss", exc, wait)
            time.sleep(wait)


def parse_item(raw, rank):
    """Turn an API item into the row we store. Returns None for anything unusable."""
    if not raw or raw.get("deleted") or raw.get("dead") or not raw.get("title"):
        return None
    return {
        "id": raw["id"],
        "rank": rank,
        "title": raw["title"],
        "url": raw.get("url"),                   # Ask HN posts have no url
        "author": raw.get("by"),
        "posted_at": raw.get("time"),
        "score": raw.get("score", 0),
        "comments": raw.get("descendants", 0),   # job posts have no comments
    }


def fetch_front_page(session, limit=30, base=None):
    """The top `limit` stories, in rank order."""
    base = base or API_BASE
    ids = fetch_json(session, f"{base}/topstories.json")[:limit]
    # One request per story, so fetch a few at a time rather than one by one.
    with ThreadPoolExecutor(max_workers=8) as pool:
        raw_items = list(pool.map(lambda i: fetch_json(session, f"{base}/item/{i}.json"), ids))
    items = (parse_item(raw, rank) for rank, raw in enumerate(raw_items, start=1))
    return [item for item in items if item]


def collect_once(conn, session, limit=30):
    ran_at = db.now_iso()
    items = fetch_front_page(session, limit)
    written = db.record_run(conn, ran_at, items)
    log.info("%d stories on the front page, %d changed", len(items), written)
    return written


def main():
    parser = argparse.ArgumentParser(description="Collect the Hacker News front page into SQLite.")
    parser.add_argument("--db", default=str(db.DEFAULT_DB), help="database file (default: tracker.db)")
    parser.add_argument("--limit", type=int, default=30, help="how many stories to track (default: 30)")
    parser.add_argument("--every", type=float, metavar="MINUTES",
                        help="keep running, collecting every MINUTES minutes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    conn = db.connect(args.db)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    if not args.every:
        collect_once(conn, session, args.limit)
        return

    log.info("Collecting every %g minutes. Press Ctrl+C to stop.", args.every)
    try:
        while True:
            try:
                collect_once(conn, session, args.limit)
            except Exception as exc:  # a failed run shouldn't end a long-running collector
                log.error("Run failed: %s", exc)
            time.sleep(args.every * 60)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
