#!/usr/bin/env python3
"""Dashboard for the tracker.

    python app.py            then open http://127.0.0.1:5000
    python app.py --port 8000
"""

import argparse
import os
from datetime import timedelta
from urllib.parse import urlparse

from flask import Flask, abort, g, jsonify, render_template, request

import db

RANGES = {3: "3 hours", 12: "12 hours", 48: "48 hours"}
HN_ITEM = "https://news.ycombinator.com/item?id={}"


def describe_span(seconds):
    """3700 -> '1 h 1 min'"""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def domain_of(url):
    if not url:
        return "news.ycombinator.com"
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def create_app(db_path=None):
    app = Flask(__name__)
    db_path = db_path or os.environ.get("TRACKER_DB", db.DEFAULT_DB)

    def conn():
        if "conn" not in g:
            g.conn = db.connect(db_path)
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exc):
        connection = g.pop("conn", None)
        if connection is not None:
            connection.close()

    @app.template_filter("ago")
    def ago(iso_text):
        seconds = (db.parse_iso(db.now_iso()) - db.parse_iso(iso_text)).total_seconds()
        return "just now" if seconds < 60 else describe_span(seconds) + " ago"

    @app.template_filter("count")
    def count(number, singular, plural):
        return f"{number:,} {singular if number == 1 else plural}"

    @app.template_filter("day")
    def day(iso_text):
        return db.parse_iso(iso_text).strftime("%d %b %Y").lstrip("0")

    @app.route("/")
    def index():
        stories = db.front_page(conn())
        for story in stories:
            story["domain"] = domain_of(story["url"])
        return render_template("index.html", stories=stories, stats=db.stats(conn()), ranges=RANGES)

    @app.route("/api/ranks")
    def api_ranks():
        hours = request.args.get("hours", default=3, type=int)
        if hours not in RANGES:
            abort(400, "hours must be one of " + ", ".join(map(str, RANGES)))
        latest = db.stats(conn())["latest_run"]
        if not latest:
            return jsonify({"since": None, "until": None, "stories": []})
        since = (db.parse_iso(latest) - timedelta(hours=hours)).isoformat(timespec="seconds")
        return jsonify(db.rank_history(conn(), since))

    @app.route("/api/story/<int:story_id>")
    def api_story(story_id):
        story = db.story_detail(conn(), story_id)
        if story is None:
            abort(404)
        tracked = db.parse_iso(story["last_seen"]) - db.parse_iso(story["first_seen"])
        story.update(
            domain=domain_of(story["url"]),
            discussion=HN_ITEM.format(story_id),
            link=story["url"] or HN_ITEM.format(story_id),
            tracked_for=describe_span(tracked.total_seconds()),
        )
        return jsonify(story)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the tracker dashboard.")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port)
