// Reddit → Lemmy mirror dashboard. No libraries: everything here is plain DOM.
(() => {
  "use strict";

  const REFRESH_MS = 15000;
  // Sent with every control request; the server rejects requests without it
  const HEADERS = { "X-Requested-With": "mirror-dashboard" };

  const $ = (sel, root = document) => root.querySelector(sel);
  const fmt = (n) => (typeof n === "number" ? n.toLocaleString() : n ?? "—");

  // ── Toast ──────────────────────────────────
  let toastTimer;
  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), isError ? 8000 : 4000);
  }

  // ── Confirm dialog ─────────────────────────
  function confirmAction({ title, body, confirmLabel, danger }) {
    const dialog = $("#confirm");
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    const ok = $("#confirm-ok");
    ok.textContent = confirmLabel;
    ok.className = danger ? "btn danger" : "btn primary";
    dialog.showModal();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    });
  }

  async function post(url) {
    const res = await fetch(url, { method: "POST", headers: HEADERS });
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok && !data.error) data.error = data.detail || `The server answered ${res.status}`;
    return data;
  }

  // ── Overview page ──────────────────────────
  function renderStrip(activity) {
    const strip = $("#strip");
    if (!strip || !activity) return;
    const peakPosts = Math.max(0, ...activity.posts);
    const peakComments = Math.max(0, ...activity.comments);
    const maxPosts = Math.max(1, peakPosts);
    const maxComments = Math.max(1, peakComments);
    strip.textContent = "";

    // Hours arrive as ISO times; show them on the viewer's own clock
    const hourLabel = (iso) => {
      const d = new Date(iso);
      return isNaN(d) ? String(iso).slice(11, 13) : String(d.getHours()).padStart(2, "0");
    };

    activity.hours.forEach((iso, i) => {
      const hour = hourLabel(iso);
      const p = activity.posts[i];
      const c = activity.comments[i];
      const isNow = i === activity.hours.length - 1;
      const col = document.createElement("div");
      col.className = "hour" + (i % 3 === 0 ? " labelled" : "") + (isNow ? " now" : "");
      col.tabIndex = 0;
      const label = `${hour}:00${isNow ? " (this hour)" : ""}: ${p} ${p === 1 ? "post" : "posts"}, ${c} ${c === 1 ? "comment" : "comments"}`;
      col.title = label;
      col.setAttribute("aria-label", label);

      const up = document.createElement("div");
      up.className = "bar" + (p ? "" : " zero");
      up.style.height = `${(p / maxPosts) * 100}%`;

      const axis = document.createElement("div");
      axis.className = "axis";
      axis.textContent = isNow ? "now" : hour;

      const down = document.createElement("div");
      down.className = "bar down" + (c ? "" : " zero");
      down.style.height = `${(c / maxComments) * 100}%`;

      col.append(up, axis, down);
      strip.append(col);
    });

    // Each side has its own scale: comments usually outnumber posts many times over
    $("#scale-posts").textContent = `Busiest hour: ${fmt(peakPosts)} ${peakPosts === 1 ? "post" : "posts"}`;
    $("#scale-comments").textContent = `Busiest hour: ${fmt(peakComments)} ${peakComments === 1 ? "comment" : "comments"}`;
  }

  function localTime(iso) {
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function renderOverview(o) {
    const status = $("#status");
    status.dataset.level = o.level;
    setText("headline", o.headline);
    setText("detail", o.detail);
    document.title = `${o.headline} | Mirror dashboard`;

    setText("total-posts", fmt(o.totals.posts));
    setText("total-comments", fmt(o.totals.comments));
    setText("day-posts", fmt(o.last24.posts));
    setText("day-comments", fmt(o.last24.comments));

    const waiting = o.queue.posts + o.queue.comments + o.queue.other;
    setText("queue-total", fmt(waiting));
    const qNotes = [];
    if (o.queue.running) qNotes.push(`${fmt(o.queue.running)} in progress`);
    if (o.queue.failed) qNotes.push(`${fmt(o.queue.failed)} failed`);
    setText("queue-note", qNotes.length ? qNotes.join(", ") : waiting ? `${fmt(o.queue.posts)} posts, ${fmt(o.queue.comments)} comments` : "Nothing waiting");
    $("#field-queue").classList.toggle("alert", o.queue.failed > 0);

    setText("errors-total", fmt(o.errors24));
    setText("errors-note", o.last_error ? `Latest in ${o.last_error.context}, ${o.last_error.ago}` : "None logged");
    $("#field-errors").classList.toggle("alert", o.errors24 > 0);

    setText("running-for", o.running_for || "—");
    setText("workers-up", `${o.workers.running} of ${o.workers.total} workers running`);
    $("#field-running").classList.toggle("alert-bad", o.workers.running < o.workers.total);

    setText("updated", localTime(o.updated));
    renderStrip(o.activity);
  }

  async function refreshOverview() {
    try {
      const res = await fetch("/dashboard/metrics", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      renderOverview(await res.json());
    } catch {
      setText("detail", "Couldn't reach the dashboard server. Retrying shortly.");
    }
  }

  let workersBusy = false;
  async function refreshWorkers() {
    if (workersBusy || $("#confirm")?.open) return;
    try {
      const res = await fetch("/dashboard/health", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      const html = await res.text();
      if (!workersBusy) $("#workers").innerHTML = html;
    } catch {
      $("#workers").innerHTML = '<p class="loading">Couldn\'t load the workers. Retrying shortly.</p>';
    }
  }

  const ACTIONS = {
    restart: { verb: "Restart", ing: "Restarting", body: "It stops and starts again straight away. Any job it is in the middle of is retried." },
    stop: { verb: "Stop", ing: "Stopping", body: "It stays stopped until you start it again.", danger: true },
    start: { verb: "Start", ing: "Starting", body: "It picks up where it left off." },
    build: { verb: "Rebuild", ing: "Rebuilding", body: "Rebuilds it from the project files and starts it. This can take a few minutes." },
    toggle: { body: "The worker is rebuilt with the new setting. This can take a minute." },
  };

  async function handleWorkerAction(button) {
    const { action, name, label } = button.dataset;
    const a = ACTIONS[action];
    let title, confirmLabel, url, busyText;

    if (action === "toggle") {
      const on = button.dataset.enabled === "true";
      title = on ? "Turn off Lemmy → Reddit comments?" : "Turn on Lemmy → Reddit comments?";
      confirmLabel = on ? "Turn off" : "Turn on";
      url = "/dashboard/container/toggle-comment-sync";
      busyText = `Turning ${on ? "off" : "on"}… this can take a minute.`;
    } else {
      title = `${a.verb} ${label}?`;
      confirmLabel = `${a.verb} ${label}`;
      url = `/dashboard/container/${encodeURIComponent(name)}/${action}`;
      busyText = `${a.ing}…${action === "build" ? " this can take a few minutes." : ""}`;
    }

    const yes = await confirmAction({ title, body: a.body, confirmLabel, danger: a.danger });
    if (!yes) return;

    workersBusy = true;
    document.querySelectorAll("#workers .btn").forEach((b) => (b.disabled = true));
    const row = button.closest(".worker");
    const note = document.createElement("p");
    note.className = "busy";
    note.setAttribute("role", "status");
    note.textContent = busyText;
    row.append(note);

    try {
      const data = await post(url);
      if (data.success) toast(data.success);
      else toast(data.details ? `${data.error}: ${String(data.details).slice(0, 200)}` : data.error || "That didn't work.", true);
    } catch (err) {
      toast(`Couldn't reach the dashboard server (${err.message}).`, true);
    } finally {
      workersBusy = false;
      await refreshWorkers();
      refreshOverview();
    }
  }

  function initOverview() {
    const seed = $("#overview-data");
    if (seed) {
      const o = JSON.parse(seed.textContent);
      renderStrip(o.activity);
      setText("updated", localTime(o.updated));
    }
    refreshWorkers();
    setInterval(refreshOverview, REFRESH_MS);
    setInterval(refreshWorkers, REFRESH_MS);
    $("#workers").addEventListener("click", (e) => {
      const button = e.target.closest("button[data-action]");
      if (button) handleWorkerAction(button);
    });
  }

  // ── Logs page ──────────────────────────────
  const MAX_LINES = 3000;
  const LINE = /^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| ?(.*)$/;

  function kindOf(msg) {
    if (/^(❌|🚫)|\bERROR\b|Traceback|Exception/.test(msg)) return "bad";
    if (/^⚠️|\b[Ff]ailed\b|\bretrying\b/.test(msg)) return "warn";
    if (msg.startsWith("✅ Posted")) return "post";
    if (msg.startsWith("💬 Mirrored")) return "comment";
    return "";
  }

  function initLogs() {
    const log = $("#log");
    const search = $("#log-search");
    const jump = $("#jump");
    const live = $("#live");
    let filter = "all";
    let query = "";
    let follow = true;
    let partial = "";
    let first = true;

    const matches = (el) =>
      (filter === "all" ||
        (filter === "problems" && (el.classList.contains("bad") || el.classList.contains("warn"))) ||
        (filter === "activity" && (el.classList.contains("post") || el.classList.contains("comment")))) &&
      (!query || el.dataset.text.includes(query));

    const none = document.createElement("p");
    none.className = "empty";
    none.hidden = true;

    function updateEmpty() {
      const anyShown = log.querySelector(".line:not([hidden])");
      none.hidden = Boolean(anyShown) || !log.querySelector(".line");
      if (!none.hidden) {
        none.textContent =
          query ? `No loaded lines contain “${search.value.trim()}”.`
          : filter === "problems" ? "No warnings or errors in the loaded lines."
          : "No posts or comments mirrored in the loaded lines.";
        log.append(none);
      }
    }

    function applyFilter() {
      log.querySelectorAll(".line").forEach((el) => (el.hidden = !matches(el)));
      updateEmpty();
      if (follow) log.scrollTop = log.scrollHeight;
    }

    function addLines(text) {
      const chunk = partial + text;
      const parts = chunk.split("\n");
      partial = parts.pop();
      if (first) { log.textContent = ""; first = false; }

      const frag = document.createDocumentFragment();
      for (const raw of parts) {
        if (!raw.trim()) continue;
        const el = document.createElement("div");
        const m = raw.match(LINE);
        // textContent only: log lines contain Reddit usernames and post titles
        if (m) {
          const ts = document.createElement("span");
          ts.className = "ts";
          ts.textContent = m[1];
          const msg = document.createElement("span");
          msg.textContent = m[2];
          el.append(ts, msg);
          el.className = `line ${kindOf(m[2])}`;
        } else {
          el.textContent = raw;
          el.className = `line plain ${kindOf(raw)}`;
        }
        el.dataset.text = raw.toLowerCase();
        el.hidden = !matches(el);
        frag.append(el);
      }
      log.append(frag);
      const lines = log.querySelectorAll(".line");
      for (let i = 0; i < lines.length - MAX_LINES; i++) lines[i].remove();
      updateEmpty();
      if (follow) log.scrollTop = log.scrollHeight;
    }

    log.addEventListener("scroll", () => {
      follow = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
      jump.hidden = follow;
    });
    jump.addEventListener("click", () => {
      follow = true;
      jump.hidden = true;
      log.scrollTop = log.scrollHeight;
    });

    let searchTimer;
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { query = search.value.trim().toLowerCase(); applyFilter(); }, 150);
    });

    document.querySelectorAll(".segmented button").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll(".segmented button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        filter = b.dataset.filter;
        applyFilter();
      })
    );

    function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${proto}://${location.host}/dashboard/ws/logs`);
      socket.addEventListener("open", () => { live.className = "live on"; live.textContent = "Live"; });
      socket.addEventListener("message", (e) => addLines(e.data));
      socket.addEventListener("close", () => {
        live.className = "live off";
        live.textContent = "Reconnecting…";
        first = true;
        partial = "";
        setTimeout(connect, 3000);
      });
      socket.addEventListener("error", () => socket.close());
    }
    connect();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.page;
    if (page === "overview") initOverview();
    if (page === "logs") initLogs();
  });

  window.mirrorDashboard = { renderOverview, renderStrip }; // handy for testing
})();
