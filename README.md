# Hacker News front page, over time

The Hacker News front page shows where a story is right now. This project records
where every story *has been*: a collector saves each story's rank, points and
comment count on a schedule, and a small web dashboard charts how stories climb
and fall.

![The dashboard](docs/screenshot.png)

<!-- Once the repo is on GitHub, uncomment this and fill in your username and repo name:
![tests](https://github.com/YOUR-USERNAME/YOUR-REPO/actions/workflows/tests.yml/badge.svg)
-->

## How it works

```
Hacker News API  ->  collector.py  ->  SQLite (tracker.db)  ->  app.py (Flask)  ->  browser
```

**Collector** (`collector.py`). Reads the top 30 stories from the
[official Hacker News API](https://github.com/HackerNews/API). Each story is its
own request, so they're fetched a few at a time with a thread pool, with retries
and backoff for failed requests. It can run once or stay running with
`--every MINUTES`, and a failed run is logged without stopping the loop.

**Storage** (`db.py`). Three SQLite tables: `stories`, `snapshots` and `runs`.
A snapshot is written only when a story's rank, points or comments changed since
its last one, so quiet stories don't fill the table with identical rows. Every
run is still recorded in `runs`, which lets the dashboard rebuild the full
timeline: a story's rank at any run is its most recent snapshot at or before it.
Each run is a single transaction, so it's stored completely or not at all.

**Dashboard** (`app.py`, `templates/`, `static/`). A Flask app with one
server-rendered page and two JSON endpoints that feed the charts:

| Route | Returns |
| --- | --- |
| `/` | the page: current front page, movement in the last hour |
| `/api/ranks?hours=3` | every story's rank over the window (3, 12 or 48 hours) |
| `/api/story/<id>` | one story's points and comments over time |

The rank chart draws one line per story with rank 1 at the top. Selecting a line
or a title highlights that story everywhere and charts its points and comments.
The selection is kept in the URL, so a link to a story survives a reload.

## Run it

Requires Python 3.9 or newer.

```bash
pip install -r requirements.txt
```

Start the collector and leave it running:

```bash
python collector.py --every 10
```

In a second terminal, start the dashboard and open http://127.0.0.1:5000

```bash
python app.py
```

The table fills after the first run. The charts get interesting after half an
hour or so, once stories have had time to move.

### Collecting without keeping a terminal open

`python collector.py` with no options collects once and exits, which is what a
scheduler wants.

**Windows (Task Scheduler).** Create a basic task that repeats every 10 minutes
with the action *Start a program*: program `python`, arguments `collector.py`,
and *Start in* set to this folder.

**macOS / Linux (cron).** Run `crontab -e` and add:

```
*/10 * * * * cd /path/to/this/folder && python3 collector.py
```

## Tests

```bash
python -m unittest -v
```

The tests cover change detection, the front-page and history queries, item
parsing and the Flask routes. They use an in-memory database and Flask's test
client, so they never touch the network. GitHub Actions runs them on every push
(`.github/workflows/tests.yml`).

## Project layout

```
collector.py        fetches the front page and stores a run
db.py               schema, the write path, and the dashboard's queries
app.py              Flask app: the page and the JSON endpoints
templates/          the page (Jinja)
static/             stylesheet and chart code (Chart.js)
test_tracker.py     unit tests
```

## Design notes

- **An API rather than scraping the HTML.** Hacker News publishes an official
  API, so there's no reason to parse markup that could change or to load the
  site itself.
- **Change detection.** Storing a row per story per run is simpler, but most
  rows would repeat the one before. Writing only changes keeps the table small
  and makes "when did this change?" a direct query.
- **SQLite.** One file, no server, and more than enough for one writer and one
  reader. Write-ahead logging is on so the dashboard can read during a write.
- **Thinning long windows.** The 48-hour view samples the runs down to about 120
  points per line so the chart stays responsive.

## Ideas for later

- Track the "new" and "best" lists as well as the front page
- A page for stories that have left the front page
- Alerts when a story climbs unusually fast
- Deploy the dashboard so it's reachable from anywhere
