/* Dashboard behaviour: the rank chart, the selected-story panel, and keeping
   the chart, the panel and the table pointing at the same story. */
(() => {
  const app = document.getElementById("app");
  if (!app) return; // nothing collected yet, the page shows setup instructions instead

  const rankCanvas = document.getElementById("rank-chart");
  const storyCanvas = document.getElementById("story-chart");
  const detail = document.getElementById("detail");

  if (typeof Chart === "undefined") {
    for (const canvas of [rankCanvas, storyCanvas]) {
      const note = document.createElement("p");
      note.className = "chart-note";
      note.textContent = "The charts could not load. Chart.js comes from a CDN, so check your internet connection and reload.";
      canvas.replaceWith(note);
    }
    return;
  }

  let rankChart = null;
  let storyChart = null;
  let selectedId = null;
  let hours = Number(app.querySelector(".range button[aria-pressed='true']").dataset.hours);

  // ---------------------------------------------------------------- helpers --

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function timeLabel(ms, withDay) {
    const options = withDay
      ? { weekday: "short", hour: "numeric", minute: "2-digit" }
      : { hour: "numeric", minute: "2-digit" };
    return new Date(ms).toLocaleString([], options);
  }

  const shorten = (text, max = 64) => (text.length > max ? text.slice(0, max - 1) + "\u2026" : text);

  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return response.json();
  }

  function baseOptions(withDay) {
    Chart.defaults.font.family = token("--font");
    Chart.defaults.color = token("--muted");
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: {
          type: "linear",
          grid: { display: false },
          border: { color: token("--rule") },
          ticks: { maxTicksLimit: 7, maxRotation: 0, callback: (value) => timeLabel(value, withDay) },
        },
      },
    };
  }

  // ------------------------------------------------------------- rank chart --

  // One story is highlighted; every other line stays quiet behind it.
  function lineStyle(storyId, pointCount) {
    const selected = storyId === selectedId;
    const color = selected ? token("--hl") : token("--line");
    return {
      borderColor: color,
      backgroundColor: color,
      borderWidth: selected ? 3.5 : 1.25,
      pointRadius: pointCount === 1 ? (selected ? 4 : 2) : 0,
      pointHoverRadius: 4,
      pointHitRadius: 8,
      order: selected ? 0 : 1, // lower order is drawn on top
    };
  }

  async function loadRanks() {
    const data = await getJSON(`/api/ranks?hours=${hours}`);
    const withDay = data.until - data.since > 24 * 3600 * 1000;
    const deepest = Math.max(10, ...data.stories.flatMap((s) => s.points.map((p) => p.rank)));
    const rankTicks = [1];
    for (let value = 5; value <= deepest; value += 5) rankTicks.push(value);

    const options = baseOptions(withDay);
    // With a single run there is no span yet, so give the lone points some room.
    const pad = data.until === data.since ? 5 * 60 * 1000 : 0;
    options.scales.x.min = data.since - pad;
    options.scales.x.max = data.until + pad;
    options.scales.y = {
      reverse: true, // rank 1 belongs at the top
      min: 0.5,
      max: deepest + 0.5,
      grid: { color: token("--rule") },
      border: { display: false },
      afterBuildTicks: (axis) => { axis.ticks = rankTicks.map((value) => ({ value })); },
      title: { display: true, text: "Rank" },
    };
    options.interaction = { mode: "nearest", intersect: false };
    options.plugins = {
      legend: { display: false },
      tooltip: {
        displayColors: false,
        callbacks: {
          title: (items) => timeLabel(items[0].parsed.x, withDay),
          label: (item) => `#${item.parsed.y}  ${shorten(item.dataset.label)}`,
        },
      },
    };
    options.onClick = (_event, elements, chart) => {
      // `elements` holds the line nearest the click (see options.interaction above)
      if (elements.length) select(chart.data.datasets[elements[0].datasetIndex].storyId);
    };
    options.onHover = (event, elements) => {
      event.native.target.style.cursor = elements.length ? "pointer" : "default";
    };

    const datasets = data.stories.map((story) => ({
      label: story.title,
      storyId: story.id,
      data: story.points.map((p) => ({ x: p.t, y: p.rank })),
      cubicInterpolationMode: "monotone",
      ...lineStyle(story.id, story.points.length),
    }));

    if (rankChart) rankChart.destroy();
    rankChart = new Chart(rankCanvas, { type: "line", data: { datasets }, options });
  }

  function restyleRanks() {
    if (!rankChart) return;
    for (const dataset of rankChart.data.datasets) {
      Object.assign(dataset, lineStyle(dataset.storyId, dataset.data.length));
    }
    rankChart.update("none");
  }

  // ----------------------------------------------------------- story detail --

  async function loadStory(id) {
    let story;
    try {
      story = await getJSON(`/api/story/${id}`);
    } catch {
      return; // an id from an old link that the tracker has never seen
    }
    if (id !== selectedId) return; // the selection moved on while this was loading

    document.getElementById("detail-title").textContent = story.title;
    const posted = story.posted_at ? `, posted ${timeLabel(story.posted_at * 1000, true)}` : "";
    document.getElementById("detail-meta").textContent =
      `${story.domain}${story.author ? ", by " + story.author : ""}${posted}`;
    document.getElementById("detail-link").href = story.link;
    document.getElementById("detail-discussion").href = story.discussion;
    document.getElementById("fact-rank").textContent = story.rank;
    document.getElementById("fact-best").textContent = story.best_rank;
    document.getElementById("fact-gained").textContent = (story.gained > 0 ? "+" : "") + story.gained;
    document.getElementById("fact-tracked").textContent = story.tracked_for;
    detail.hidden = false;

    const first = story.points[0].t;
    const last = story.points[story.points.length - 1].t;
    const options = baseOptions(last - first > 24 * 3600 * 1000);
    const pad = first === last ? 5 * 60 * 1000 : 0;
    options.scales.x.min = first - pad;
    options.scales.x.max = last + pad;
    options.scales.y = {
      position: "left",
      beginAtZero: false,
      grid: { color: token("--rule") },
      border: { display: false },
      ticks: { precision: 0 },
      title: { display: true, text: "Points" },
    };
    options.scales.y1 = {
      position: "right",
      beginAtZero: false,
      grid: { display: false },
      border: { display: false },
      ticks: { precision: 0 },
      title: { display: true, text: "Comments" },
    };
    options.interaction = { mode: "index", intersect: false };
    options.plugins = {
      legend: { align: "start", labels: { usePointStyle: true, pointStyle: "line", pointStyleWidth: 28 } },
      tooltip: { callbacks: { title: (items) => timeLabel(items[0].parsed.x, true) } },
    };

    const single = story.points.length === 1;
    const datasets = [
      {
        label: "Points",
        data: story.points.map((p) => ({ x: p.t, y: p.score })),
        borderColor: token("--hl"),
        backgroundColor: token("--hl"),
        borderWidth: 3,
        pointRadius: single ? 4 : 0,
        pointHitRadius: 8,
      },
      {
        label: "Comments",
        data: story.points.map((p) => ({ x: p.t, y: p.comments })),
        yAxisID: "y1",
        borderColor: token("--ink"),
        backgroundColor: token("--ink"),
        borderWidth: 1.5,
        borderDash: [5, 4], // dashed as well as coloured, so the two lines differ without colour
        pointRadius: single ? 3 : 0,
        pointHitRadius: 8,
      },
    ];

    if (storyChart) storyChart.destroy();
    storyChart = new Chart(storyCanvas, { type: "line", data: { datasets }, options });
  }

  // -------------------------------------------------------------- selection --

  function select(id, { scroll = false } = {}) {
    selectedId = id;
    history.replaceState(null, "", `#story-${id}`);

    for (const row of app.querySelectorAll("tbody tr")) {
      const on = Number(row.dataset.story) === id;
      row.classList.toggle("is-selected", on);
      row.querySelector(".pick").setAttribute("aria-pressed", String(on));
    }
    restyleRanks();
    loadStory(id).then(() => {
      if (!scroll) return;
      const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
      detail.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
    });
  }

  app.querySelector("tbody").addEventListener("click", (event) => {
    const button = event.target.closest(".pick");
    // The panel sits above the table, so bring it into view after picking from the table.
    if (button) select(Number(button.dataset.story), { scroll: true });
  });

  for (const button of app.querySelectorAll(".range button")) {
    button.addEventListener("click", () => {
      hours = Number(button.dataset.hours);
      for (const other of app.querySelectorAll(".range button")) {
        other.setAttribute("aria-pressed", String(other === button));
      }
      loadRanks();
    });
  }

  // Charts read their colours from CSS, so redraw when the colour scheme flips.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    loadRanks();
    if (selectedId) loadStory(selectedId);
  });

  // ------------------------------------------------------------------ start --

  const fromHash = Number((location.hash.match(/^#story-(\d+)$/) || [])[1]);
  const firstRow = app.querySelector("tbody tr");
  selectedId = fromHash || (firstRow ? Number(firstRow.dataset.story) : null);

  loadRanks().then(() => {
    if (selectedId) select(selectedId);
  });
})();
