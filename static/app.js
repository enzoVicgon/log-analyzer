/* The dashboard has no build step; it talks directly to the API. */

const $ = (id) => document.getElementById(id);

const els = {
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  filename: $("filename"),
  banner: $("banner"),
  results: $("results"),
  themeToggle: $("theme-toggle"),
  themeLabel: document.querySelector("[data-theme-label]"),
};

let lastStats = null;

const charts = {};

/* --------------------------------------------------------------------------
   Theme
   -------------------------------------------------------------------------- */

const THEME_KEY = "log-analyzer-theme";
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function currentTheme() {
  return document.documentElement.dataset.theme || (darkQuery.matches ? "dark" : "light");
}

function syncThemeLabel() {
  els.themeLabel.textContent = currentTheme() === "dark" ? "Light" : "Dark";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  syncThemeLabel();
  if (lastStats) renderCharts(lastStats);
}

const storedTheme = localStorage.getItem(THEME_KEY);
if (storedTheme) document.documentElement.dataset.theme = storedTheme;
syncThemeLabel();

els.themeToggle.addEventListener("click", () => {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
});

darkQuery.addEventListener("change", () => {
  if (localStorage.getItem(THEME_KEY)) return;
  syncThemeLabel();
  if (lastStats) renderCharts(lastStats);
});

function tokens() {
  const style = getComputedStyle(document.documentElement);
  const get = (name) => style.getPropertyValue(name).trim();
  return {
    surface: get("--surface-1"),
    textPrimary: get("--text-primary"),
    textSecondary: get("--text-secondary"),
    muted: get("--text-muted"),
    grid: get("--grid"),
    axis: get("--axis"),
    series1: get("--series-1"),
    good: get("--status-good"),
    warning: get("--status-warning"),
    critical: get("--status-critical"),
  };
}

function withAlpha(hex, alpha) {
  const raw = hex.replace("#", "");
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  const n = parseInt(full, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/* --------------------------------------------------------------------------
   Formatting
   -------------------------------------------------------------------------- */

const numberFormat = new Intl.NumberFormat("en-US");
const compactFormat = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const formatCount = (n) => numberFormat.format(n);
const formatTileValue = (n) => (n < 10000 ? numberFormat.format(n) : compactFormat.format(n));

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(2)} s`;
}

function utcFormatter(options) {
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" });
}

const HOUR_MINUTE = utcFormatter({ hour: "2-digit", minute: "2-digit", hour12: false });
const DAY_MONTH = utcFormatter({ day: "2-digit", month: "short" });
const DAY_HOUR = utcFormatter({
  day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});
const FULL_STAMP = utcFormatter({
  day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
});

function bucketLabel(iso, granularity) {
  const date = new Date(iso);
  if (granularity === "day") return DAY_MONTH.format(date);
  if (granularity === "hour") return DAY_HOUR.format(date);
  return HOUR_MINUTE.format(date);
}

function formatRange(firstIso, lastIso) {
  if (!firstIso || !lastIso) return "";
  return `${DAY_HOUR.format(new Date(firstIso))} – ${DAY_HOUR.format(new Date(lastIso))} UTC`;
}

/* --------------------------------------------------------------------------
   Upload
   -------------------------------------------------------------------------- */

function showBanner(kind, message) {
  els.banner.className = `banner banner-${kind}`;
  els.banner.replaceChildren();
  if (kind === "loading") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    els.banner.append(spinner);
  }
  const text = document.createElement("span");
  text.textContent = message;
  els.banner.append(text);
  els.banner.hidden = false;
}

async function handleFile(file) {
  els.filename.textContent = `${file.name} · ${formatBytes(file.size)}`;
  els.filename.hidden = false;
  showBanner("loading", `Analyzing ${file.name}…`);
  if (lastStats) els.results.classList.add("is-stale");

  const body = new FormData();
  body.append("file", file);

  try {
    const response = await fetch("/api/analyze", { method: "POST", body });

    if (!response.ok) {
      let detail = `Server responded ${response.status}`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === "string") detail = payload.detail;
      } catch {
        /* response had no JSON body; keep the status-code message */
      }
      throw new Error(detail);
    }

    render(await response.json());
  } catch (error) {
    els.results.classList.remove("is-stale");
    showBanner("error", error.message || "Could not reach the server.");
  }
}

els.dropzone.addEventListener("click", () => els.fileInput.click());
els.dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    els.fileInput.click();
  }
});

els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  if (file) handleFile(file);
  els.fileInput.value = "";
});

["dragenter", "dragover"].forEach((name) => {
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropzone.classList.add("is-dragover");
  });
});

els.dropzone.addEventListener("dragleave", (event) => {
  if (!els.dropzone.contains(event.relatedTarget)) {
    els.dropzone.classList.remove("is-dragover");
  }
});

els.dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  els.dropzone.classList.remove("is-dragover");
  const file = event.dataTransfer.files[0];
  if (file) handleFile(file);
});

/* --------------------------------------------------------------------------
   Rendering
   -------------------------------------------------------------------------- */

function render(stats) {
  lastStats = stats;
  els.results.classList.remove("is-stale");

  if (stats.total_requests === 0) {
    els.results.hidden = true;
    showBanner(
      "error",
      stats.malformed_lines > 0
        ? `No lines matched the log format (${formatCount(stats.malformed_lines)} unparsed). ` +
          "Combined or Common Log Format is expected."
        : "That file is empty."
    );
    return;
  }

  els.banner.hidden = true;
  els.results.hidden = false;

  renderKpis(stats);
  renderCharts(stats);
  renderTables(stats);
}

function renderKpis(stats) {
  $("kpi-total").textContent = formatTileValue(stats.total_requests);
  $("kpi-span").textContent = formatRange(stats.first_timestamp, stats.last_timestamp);

  const errorCount = Math.round((stats.error_rate / 100) * stats.total_requests);
  $("kpi-error-rate").textContent = `${stats.error_rate.toFixed(1)}%`;
  $("kpi-error-count").textContent = `${formatCount(errorCount)} responses were 4xx or 5xx`;

  $("kpi-visitors").textContent = formatTileValue(stats.unique_visitors);
  $("kpi-bytes").textContent = formatBytes(stats.total_bytes);
  $("kpi-malformed").textContent = formatTileValue(stats.malformed_lines);

  if (stats.timing_available) {
    $("kpi-avg-time").textContent = formatDuration(stats.avg_response_time);
    const slowest = stats.slowest_requests[0];
    $("kpi-slowest-note").textContent = slowest
      ? `slowest was ${formatDuration(slowest.request_time)}`
      : " ";
  } else {
    $("kpi-avg-time").textContent = "—";
    $("kpi-slowest-note").textContent = "not recorded in this log";
  }
}

function renderCharts(stats) {
  const t = tokens();
  renderTimelineChart(stats, t);
  renderStatusChart(stats, t);
  renderEndpointsChart(stats, t);
}

/** Shared tooltip chrome, so all three charts read as one system. */
function tooltipStyle(t, callbacks = {}) {
  return {
    backgroundColor: t.surface,
    titleColor: t.textPrimary,
    bodyColor: t.textSecondary,
    borderColor: t.axis,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 8,
    boxWidth: 8,
    boxHeight: 8,
    boxPadding: 4,
    usePointStyle: true,
    callbacks,
  };
}

function replaceChart(key, canvasId, config) {
  charts[key]?.destroy();
  charts[key] = new Chart($(canvasId), config);
}

/* -- Traffic over time ----------------------------------------------------- */

function renderTimelineChart(stats, t) {
  const labels = stats.timeline.map((b) => bucketLabel(b.timestamp, stats.timeline_granularity));

  $("timeline-sub").textContent =
    `Per ${stats.timeline_granularity} · ${stats.timeline.length} intervals · UTC`;

  replaceChart("timeline", "chart-timeline", {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Requests",
          data: stats.timeline.map((b) => b.requests),
          borderColor: t.series1,
          backgroundColor: withAlpha(t.series1, 0.1), // a wash, not a solid block
          fill: true,
          borderWidth: 2,
          // 'monotone' keeps the curve from overshooting below zero between
          // points, which plain cubic smoothing does on spiky traffic data.
          cubicInterpolationMode: "monotone",
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHitRadius: 14, // generous hit target; the mark itself is invisible
          pointBackgroundColor: t.series1,
          pointBorderColor: t.surface,
          pointBorderWidth: 2, // 2px surface ring
        },
        {
          label: "Errors",
          data: stats.timeline.map((b) => b.errors),
          borderColor: t.critical,
          fill: false,
          borderWidth: 2,
          cubicInterpolationMode: "monotone",
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHitRadius: 14,
          pointBackgroundColor: t.critical,
          pointBorderColor: t.surface,
          pointBorderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      // Both series share ONE y-axis. Errors are a small fraction of requests
      // and the chart should say so; a second y-axis would rescale them to look
      // comparable and invent a relationship that is not in the data.
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid: { display: false },
          border: { color: t.axis },
          ticks: {
            color: t.muted, font: { size: 11 },
            maxRotation: 0, autoSkip: true, maxTicksLimit: 10,
          },
        },
        y: {
          beginAtZero: true,
          grid: { color: t.grid, drawTicks: false }, // solid hairline, never dashed
          border: { display: false },
          ticks: {
            color: t.muted, font: { size: 11 },
            maxTicksLimit: 5, padding: 8,
            callback: (value) => compactFormat.format(value),
          },
        },
      },
      plugins: {
        legend: {
          position: "top",
          align: "end",
          labels: {
            usePointStyle: true, pointStyle: "circle",
            boxWidth: 8, boxHeight: 8, padding: 14,
            color: t.textSecondary, // text token, never the series colour
            font: { size: 12 },
          },
        },
        tooltip: tooltipStyle(t, {
          label: (item) => ` ${item.dataset.label}: ${formatCount(item.parsed.y)}`,
        }),
      },
    },
  });

  buildTable(
    $("table-timeline"),
    [{ label: "Interval (UTC)" }, { label: "Requests", numeric: true }, { label: "Errors", numeric: true }],
    stats.timeline.map((b, i) => [labels[i], formatCount(b.requests), formatCount(b.errors)])
  );
}

/* -- Status codes ---------------------------------------------------------- */

// 2xx/4xx/5xx genuinely mean good / warning / bad, so they use the reserved
// status palette rather than arbitrary categorical hues. 3xx is neither good
// nor bad -- it is informational -- so it takes the neutral categorical blue.
// Every slice is also labelled in the legend below, so colour is never the
// only channel carrying the meaning.
const STATUS_CLASSES = {
  "1xx": { name: "Informational", token: "muted" },
  "2xx": { name: "Success", token: "good" },
  "3xx": { name: "Redirect", token: "series1" },
  "4xx": { name: "Client error", token: "warning" },
  "5xx": { name: "Server error", token: "critical" },
};

function renderStatusChart(stats, t) {
  const classes = stats.status_classes;
  const total = classes.reduce((sum, c) => sum + c.count, 0) || 1;
  const meta = classes.map((c) => STATUS_CLASSES[c.label] ?? { name: c.label, token: "muted" });
  const colors = meta.map((m) => t[m.token]);

  replaceChart("status", "chart-status", {
    type: "doughnut",
    data: {
      labels: classes.map((c, i) => `${c.label} ${meta[i].name}`),
      datasets: [{
        data: classes.map((c) => c.count),
        backgroundColor: colors,
        // A ring in the surface colour IS the 2px gap between segments -- it is
        // separation, not a border drawn around the mark.
        borderColor: t.surface,
        borderWidth: 2,
        hoverOffset: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "64%",
      plugins: {
        legend: { display: false }, // replaced by the labelled HTML legend below
        tooltip: tooltipStyle(t, {
          label: (item) =>
            ` ${formatCount(item.parsed)} (${((item.parsed / total) * 100).toFixed(1)}%)`,
        }),
      },
    },
  });

  const legend = $("legend-status");
  legend.replaceChildren();
  classes.forEach((entry, i) => {
    const item = document.createElement("li");

    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = colors[i];

    const label = document.createElement("span");
    label.className = "legend-label";
    label.textContent = `${entry.label} ${meta[i].name}`;

    const count = document.createElement("span");
    count.className = "legend-count";
    count.textContent = formatCount(entry.count);

    const share = document.createElement("span");
    share.className = "legend-share";
    share.textContent = `${((entry.count / total) * 100).toFixed(1)}%`;

    item.append(swatch, label, count, share);
    legend.append(item);
  });

  buildTable(
    $("table-status"),
    [{ label: "Code" }, { label: "Count", numeric: true }, { label: "Share", numeric: true }],
    stats.status_counts.map((s) => [
      String(s.status),
      formatCount(s.count),
      `${((s.count / stats.total_requests) * 100).toFixed(1)}%`,
    ])
  );
}

/* -- Top endpoints --------------------------------------------------------- */

/** Draws each bar's value just past its tip, outside the bar so it can't clip. */
const barValueLabels = {
  id: "barValueLabels",
  afterDatasetsDraw(chart, _args, options) {
    const { ctx } = chart;
    ctx.save();
    ctx.font = '500 11px system-ui, -apple-system, "Segoe UI", sans-serif';
    ctx.fillStyle = options.color;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    chart.getDatasetMeta(0).data.forEach((bar, i) => {
      ctx.fillText(formatCount(chart.data.datasets[0].data[i]), bar.x + 8, bar.y);
    });
    ctx.restore();
  },
};

function renderEndpointsChart(stats, t) {
  replaceChart("endpoints", "chart-endpoints", {
    type: "bar",
    data: {
      labels: stats.top_endpoints.map((e) => e.path),
      datasets: [{
        data: stats.top_endpoints.map((e) => e.count),
        // Endpoints are nominal, with no natural order, so all bars share one
        // colour. Shading them by size would double-encode the bar length.
        backgroundColor: t.series1,
        maxBarThickness: 18,
        // Rounded at the data end, square at the baseline.
        borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 4, bottomRight: 4 },
        borderSkipped: false,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { right: 56 } }, // room for the value labels
      scales: {
        // The x-axis is redundant once every bar is directly labelled.
        x: { display: false, beginAtZero: true },
        y: {
          grid: { display: false },
          border: { color: t.axis },
          ticks: {
            color: t.muted,
            font: { size: 11, family: 'ui-monospace, SFMono-Regular, Menlo, monospace' },
            autoSkip: false,
            crossAlign: "far",
            callback(value) {
              const label = this.getLabelForValue(value);
              return label.length > 28 ? `${label.slice(0, 27)}…` : label;
            },
          },
        },
      },
      plugins: {
        legend: { display: false }, // a single series needs no legend box
        tooltip: tooltipStyle(t, {
          label: (item) => ` ${formatCount(item.parsed.x)} requests`,
        }),
        barValueLabels: { color: t.textSecondary },
      },
    },
    plugins: [barValueLabels],
  });
}

/* --------------------------------------------------------------------------
   Tables
   -------------------------------------------------------------------------- */

/**
 * Build a table from plain data.
 * Cells are strings (inserted with textContent) or DOM nodes -- never HTML.
 */
function buildTable(table, columns, rows) {
  table.replaceChildren();

  const headRow = document.createElement("tr");
  columns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column.label;
    if (column.numeric) th.classList.add("col-num");
    headRow.append(th);
  });
  const thead = document.createElement("thead");
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  if (rows.length === 0) {
    const cell = document.createElement("td");
    cell.colSpan = columns.length;
    cell.textContent = "Nothing to show.";
    cell.style.color = "var(--text-muted)";
    const row = document.createElement("tr");
    row.append(cell);
    tbody.append(row);
  }
  rows.forEach((cells) => {
    const row = document.createElement("tr");
    cells.forEach((value, i) => {
      const td = document.createElement("td");
      if (value instanceof Node) td.append(value);
      else td.textContent = value; // textContent, never innerHTML -- see file header
      if (columns[i].numeric) td.classList.add("col-num");
      if (columns[i].cls) td.classList.add(columns[i].cls);
      row.append(td);
    });
    tbody.append(row);
  });

  table.append(thead, tbody);
}

function statusPill(status) {
  const pill = document.createElement("span");
  pill.className = "pill";
  if (status >= 500) pill.classList.add("pill-5xx");
  else if (status >= 400) pill.classList.add("pill-4xx");
  pill.textContent = String(status); // the number, so colour is never alone
  return pill;
}

function renderTables(stats) {
  buildTable(
    $("table-errors"),
    [{ label: "Status" }, { label: "Endpoint", cls: "cell-path" }, { label: "Count", numeric: true }],
    stats.top_errors.map((e) => [statusPill(e.status), e.path, formatCount(e.count)])
  );

  const slowestCard = $("card-slowest");
  const unavailable = $("slowest-unavailable");
  const slowestTable = $("table-slowest");

  if (!stats.timing_available) {
    slowestTable.replaceChildren();
    slowestTable.hidden = true;
    unavailable.hidden = false;
    $("slowest-sub").textContent = "Unavailable for this log";
    return;
  }

  slowestTable.hidden = false;
  unavailable.hidden = true;
  $("slowest-sub").textContent = "Longest individual response times";
  slowestCard.hidden = false;

  buildTable(
    slowestTable,
    [
      { label: "Time", numeric: false },
      { label: "Method", cls: "cell-method" },
      { label: "Endpoint", cls: "cell-path" },
      { label: "Status" },
      { label: "Duration", numeric: true },
    ],
    stats.slowest_requests.map((r) => [
      FULL_STAMP.format(new Date(r.timestamp)),
      r.method,
      r.path,
      statusPill(r.status),
      formatDuration(r.request_time),
    ])
  );
}
