"""Tests. Run them with:  python -m unittest -v

None of these touch the network: the database tests use an in-memory SQLite
database and the dashboard tests use Flask's built-in test client.
"""

import os
import tempfile
import unittest

import collector
import db
from app import create_app, describe_span, domain_of


def story(story_id, rank, score=10, comments=2, title=None):
    return {
        "id": story_id, "rank": rank, "title": title or f"Story {story_id}",
        "url": f"https://example.com/{story_id}", "author": "someone",
        "posted_at": 1_700_000_000, "score": score, "comments": comments,
    }


T1 = "2026-01-01T10:00:00+00:00"
T2 = "2026-01-01T10:10:00+00:00"
T3 = "2026-01-01T10:20:00+00:00"
T_LATER = "2026-01-01T11:30:00+00:00"


class ChangeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_first_run_writes_a_snapshot_for_every_story(self):
        written = db.record_run(self.conn, T1, [story(1, 1), story(2, 2)])
        self.assertEqual(written, 2)
        self.assertEqual(self.count("snapshots"), 2)

    def test_an_unchanged_story_is_not_written_again(self):
        db.record_run(self.conn, T1, [story(1, 1)])
        written = db.record_run(self.conn, T2, [story(1, 1)])
        self.assertEqual(written, 0)
        self.assertEqual(self.count("snapshots"), 1)

    def test_an_unchanged_run_is_still_recorded(self):
        db.record_run(self.conn, T1, [story(1, 1)])
        db.record_run(self.conn, T2, [story(1, 1)])
        self.assertEqual(self.count("runs"), 2)
        last_seen = self.conn.execute("SELECT last_seen FROM stories WHERE id = 1").fetchone()[0]
        self.assertEqual(last_seen, T2)

    def test_only_the_story_that_changed_is_written(self):
        db.record_run(self.conn, T1, [story(1, 1), story(2, 2)])
        written = db.record_run(self.conn, T2, [story(1, 1), story(2, 2, score=25)])
        self.assertEqual(written, 1)

    def test_a_rank_change_alone_counts_as_a_change(self):
        db.record_run(self.conn, T1, [story(1, 1)])
        self.assertEqual(db.record_run(self.conn, T2, [story(1, 4)]), 1)


class FrontPageTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_empty_database(self):
        self.assertEqual(db.front_page(self.conn), [])

    def test_only_the_latest_run_in_rank_order(self):
        db.record_run(self.conn, T1, [story(1, 1), story(2, 2)])
        db.record_run(self.conn, T2, [story(3, 1), story(2, 2)])   # story 1 dropped off
        page = db.front_page(self.conn)
        self.assertEqual([s["id"] for s in page], [3, 2])

    def test_movement_is_measured_against_an_hour_ago(self):
        db.record_run(self.conn, T1, [story(1, 8), story(2, 1)])
        db.record_run(self.conn, T_LATER, [story(1, 3), story(2, 5), story(9, 7)])
        page = {s["id"]: s for s in db.front_page(self.conn)}
        self.assertEqual(page[1]["moved"], 5)      # climbed from 8 to 3
        self.assertEqual(page[2]["moved"], -4)     # fell from 1 to 5
        self.assertIsNone(page[9]["moved"])        # new within the hour

    def test_points_gained_since_first_seen(self):
        db.record_run(self.conn, T1, [story(1, 1, score=10)])
        db.record_run(self.conn, T2, [story(1, 1, score=45)])
        self.assertEqual(db.front_page(self.conn)[0]["gained"], 35)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_quiet_runs_are_filled_back_in(self):
        db.record_run(self.conn, T1, [story(1, 2)])
        db.record_run(self.conn, T2, [story(1, 2)])   # unchanged, so no snapshot
        db.record_run(self.conn, T3, [story(1, 1)])
        history = db.rank_history(self.conn, T1)
        ranks = [p["rank"] for p in history["stories"][0]["points"]]
        self.assertEqual(ranks, [2, 2, 1])

    def test_a_line_stops_when_the_story_leaves_the_front_page(self):
        db.record_run(self.conn, T1, [story(1, 1), story(2, 2)])
        db.record_run(self.conn, T2, [story(2, 1)])
        db.record_run(self.conn, T3, [story(2, 1)])
        by_id = {s["id"]: s for s in db.rank_history(self.conn, T1)["stories"]}
        self.assertEqual(len(by_id[1]["points"]), 1)
        self.assertEqual(len(by_id[2]["points"]), 3)

    def test_long_windows_are_thinned_but_keep_the_latest_run(self):
        for minute in range(50):
            db.record_run(self.conn, f"2026-01-01T10:{minute:02d}:00+00:00", [story(1, 1, score=minute)])
        history = db.rank_history(self.conn, T1, max_steps=10)
        points = history["stories"][0]["points"]
        self.assertLessEqual(len(points), 10)
        self.assertEqual(points[-1]["t"], db.to_ms("2026-01-01T10:49:00+00:00"))

    def test_story_detail_extends_to_the_last_time_it_was_seen(self):
        db.record_run(self.conn, T1, [story(1, 1)])
        db.record_run(self.conn, T2, [story(1, 1)])
        detail = db.story_detail(self.conn, 1)
        self.assertEqual(detail["points"][-1]["t"], db.to_ms(T2))

    def test_story_detail_for_an_unknown_story(self):
        self.assertIsNone(db.story_detail(self.conn, 404))


class ParseItemTests(unittest.TestCase):
    def test_a_normal_story(self):
        raw = {"id": 7, "title": "Hello", "url": "https://a.dev", "by": "pg",
               "time": 1, "score": 12, "descendants": 3, "type": "story"}
        item = collector.parse_item(raw, rank=4)
        self.assertEqual((item["rank"], item["score"], item["comments"]), (4, 12, 3))

    def test_missing_fields_get_defaults(self):
        item = collector.parse_item({"id": 7, "title": "Who is hiring?", "type": "job"}, rank=1)
        self.assertIsNone(item["url"])
        self.assertEqual(item["comments"], 0)

    def test_unusable_items_are_skipped(self):
        self.assertIsNone(collector.parse_item(None, 1))
        self.assertIsNone(collector.parse_item({"id": 1, "title": "x", "dead": True}, 1))
        self.assertIsNone(collector.parse_item({"id": 1, "deleted": True}, 1))


class DashboardTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.client = create_app(self.path).test_client()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)

    def seed(self):
        conn = db.connect(self.path)
        db.record_run(conn, T1, [story(1, 1, title="<b>Tricky</b> title")])
        db.record_run(conn, T2, [story(1, 1, score=30, title="<b>Tricky</b> title")])
        conn.close()

    def test_empty_database_shows_setup_instructions(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Nothing collected yet", response.data)

    def test_page_lists_stories_and_escapes_titles(self):
        self.seed()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;b&gt;Tricky&lt;/b&gt; title", response.data)
        self.assertNotIn(b"<b>Tricky</b>", response.data)

    def test_ranks_endpoint(self):
        self.seed()
        data = self.client.get("/api/ranks?hours=3").get_json()
        self.assertEqual(len(data["stories"]), 1)
        self.assertEqual(self.client.get("/api/ranks?hours=5").status_code, 400)

    def test_story_endpoint(self):
        self.seed()
        data = self.client.get("/api/story/1").get_json()
        self.assertEqual(data["gained"], 20)
        self.assertEqual(data["domain"], "example.com")
        self.assertEqual(self.client.get("/api/story/999").status_code, 404)


class FormattingTests(unittest.TestCase):
    def test_describe_span(self):
        self.assertEqual(describe_span(30), "under a minute")
        self.assertEqual(describe_span(59 * 60), "59 min")
        self.assertEqual(describe_span(3600), "1 h")
        self.assertEqual(describe_span(3700), "1 h 1 min")
        self.assertEqual(describe_span(26 * 3600), "1 d 2 h")

    def test_domain_of(self):
        self.assertEqual(domain_of("https://www.example.com/a"), "example.com")
        self.assertEqual(domain_of(None), "news.ycombinator.com")


if __name__ == "__main__":
    unittest.main()
