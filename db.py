"""SQLite storage for the tracker.

Three tables:

  stories    one row per story that has ever appeared on the front page
  snapshots  a story's rank, score and comment count at a moment in time,
             written only when one of those values changed since the last run
  runs       one row per collector run, so the dashboard knows which moments
             were measured even when nothing changed

Timestamps are UTC ISO-8601 strings (2026-01-31T14:05:00+00:00). They all come
from now_iso(), so comparing them as text is the same as comparing them as times.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).with_name("tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY,      -- Hacker News item id
    title       TEXT NOT NULL,
    url         TEXT,
    author      TEXT,
    posted_at   INTEGER,                  -- unix time the story was submitted
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id     INTEGER NOT NULL REFERENCES stories(id),
    captured_at  TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    score        INTEGER NOT NULL,
    comments     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_story ON snapshots (story_id, id);

CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at             TEXT NOT NULL,
    stories_seen       INTEGER NOT NULL,
    snapshots_written  INTEGER NOT NULL
);
"""


# ------------------------------------------------------------------- helpers --

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(text):
    return datetime.fromisoformat(text)


def to_ms(text):
    """ISO timestamp -> milliseconds since the epoch (what JavaScript wants)."""
    return int(parse_iso(text).timestamp() * 1000)


def connect(path=DEFAULT_DB):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while the collector is writing.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# -------------------------------------------------------------------- writes --

def record_run(conn, ran_at, items):
    """Store one collector run. Returns how many snapshots were written.

    `items` is a list of dicts with the keys
    id, rank, title, url, author, posted_at, score, comments.

    A snapshot is only written when a story's rank, score or comment count
    differs from its previous snapshot, so quiet stories don't fill the table
    with identical rows.
    """
    written = 0
    with conn:  # one transaction: a run is stored completely or not at all
        for item in items:
            conn.execute(
                """
                INSERT INTO stories (id, title, url, author, posted_at, first_seen, last_seen)
                VALUES (:id, :title, :url, :author, :posted_at, :ran_at, :ran_at)
                ON CONFLICT (id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    last_seen = excluded.last_seen
                """,
                {**item, "ran_at": ran_at},
            )
            last = conn.execute(
                "SELECT rank, score, comments FROM snapshots"
                " WHERE story_id = ? ORDER BY id DESC LIMIT 1",
                (item["id"],),
            ).fetchone()
            current = (item["rank"], item["score"], item["comments"])
            if last is None or tuple(last) != current:
                conn.execute(
                    "INSERT INTO snapshots (story_id, captured_at, rank, score, comments)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (item["id"], ran_at, *current),
                )
                written += 1
        conn.execute(
            "INSERT INTO runs (ran_at, stories_seen, snapshots_written) VALUES (?, ?, ?)",
            (ran_at, len(items), written),
        )
    return written


# ------------------------------------------------------------------- queries --

def stats(conn):
    row = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM runs)      AS runs,
               (SELECT COUNT(*) FROM stories)   AS stories,
               (SELECT COUNT(*) FROM snapshots) AS snapshots,
               (SELECT MIN(ran_at) FROM runs)   AS first_run,
               (SELECT MAX(ran_at) FROM runs)   AS latest_run
        """
    ).fetchone()
    return dict(row)


def front_page(conn):
    """Stories seen in the latest run, in rank order, with how far each has moved."""
    info = stats(conn)
    if not info["latest_run"]:
        return []
    hour_ago = (parse_iso(info["latest_run"]) - timedelta(hours=1)).isoformat(timespec="seconds")

    rows = conn.execute(
        """
        SELECT s.id, s.title, s.url, s.author, s.posted_at, s.first_seen, s.last_seen,
               cur.rank, cur.score, cur.comments,
               first.rank  AS first_rank,
               first.score AS first_score,
               (SELECT MIN(rank) FROM snapshots WHERE story_id = s.id) AS best_rank,
               (SELECT rank FROM snapshots
                 WHERE story_id = s.id AND captured_at <= :hour_ago
                 ORDER BY id DESC LIMIT 1) AS rank_hour_ago
        FROM stories s
        JOIN snapshots cur   ON cur.id   = (SELECT MAX(id) FROM snapshots WHERE story_id = s.id)
        JOIN snapshots first ON first.id = (SELECT MIN(id) FROM snapshots WHERE story_id = s.id)
        WHERE s.last_seen = :latest
        ORDER BY cur.rank
        """,
        {"hour_ago": hour_ago, "latest": info["latest_run"]},
    ).fetchall()

    stories = []
    for row in rows:
        story = dict(row)
        if story["rank_hour_ago"] is not None:
            story["moved"] = story["rank_hour_ago"] - story["rank"]   # positive = climbed
        elif story["first_seen"] == info["first_run"]:
            # Tracking started less than an hour ago: compare with where it began.
            story["moved"] = story["first_rank"] - story["rank"]
        else:
            story["moved"] = None                                      # new within the hour
        story["gained"] = story["score"] - story["first_score"]
        stories.append(story)
    return stories


def rank_history(conn, since, max_steps=120):
    """Rank of every story at every run since `since`, for the rank chart.

    Snapshots only exist where something changed, so this fills the quiet runs
    back in: a story's rank at a run is its most recent snapshot at or before it.
    Long windows are thinned to about `max_steps` runs to keep the chart light.
    """
    runs = [r["ran_at"] for r in conn.execute(
        "SELECT ran_at FROM runs WHERE ran_at >= ? ORDER BY ran_at", (since,))]
    if not runs:
        return {"since": None, "until": None, "stories": []}

    step = -(-len(runs) // max_steps)          # ceiling division
    sampled = runs[::-1][::step][::-1]         # thin, always keeping the latest run

    rows = conn.execute(
        """
        SELECT s.id, s.title, s.first_seen, s.last_seen, p.captured_at, p.rank
        FROM stories s
        JOIN snapshots p ON p.story_id = s.id
        WHERE s.last_seen >= ?
        ORDER BY s.id, p.id
        """,
        (since,),
    ).fetchall()

    grouped = {}
    for row in rows:
        story = grouped.setdefault(row["id"], {
            "id": row["id"], "title": row["title"],
            "first_seen": row["first_seen"], "last_seen": row["last_seen"], "snaps": [],
        })
        story["snaps"].append((row["captured_at"], row["rank"]))

    stories = []
    for story in grouped.values():
        points, i, rank = [], 0, None
        for ran_at in sampled:
            while i < len(story["snaps"]) and story["snaps"][i][0] <= ran_at:
                rank = story["snaps"][i][1]
                i += 1
            if rank is not None and story["first_seen"] <= ran_at <= story["last_seen"]:
                points.append({"t": to_ms(ran_at), "rank": rank})
        if points:
            stories.append({"id": story["id"], "title": story["title"], "points": points})

    return {"since": to_ms(sampled[0]), "until": to_ms(sampled[-1]), "stories": stories}


def story_detail(conn, story_id):
    story = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
    if story is None:
        return None
    snaps = conn.execute(
        "SELECT captured_at, rank, score, comments FROM snapshots WHERE story_id = ? ORDER BY id",
        (story_id,),
    ).fetchall()

    points = [{"t": to_ms(s["captured_at"]), "rank": s["rank"],
               "score": s["score"], "comments": s["comments"]} for s in snaps]
    # The last snapshot is when something last changed; the story may have been
    # seen, unchanged, after that. Extend the line to the last time it was seen.
    if snaps and story["last_seen"] > snaps[-1]["captured_at"]:
        points.append({**points[-1], "t": to_ms(story["last_seen"])})

    result = dict(story)
    result.update(
        points=points,
        rank=snaps[-1]["rank"],
        score=snaps[-1]["score"],
        comments=snaps[-1]["comments"],
        best_rank=min(s["rank"] for s in snaps),
        gained=snaps[-1]["score"] - snaps[0]["score"],
    )
    return result
