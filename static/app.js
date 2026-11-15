const $ = (id) => document.getElementById(id);

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "—";
  const s = Math.max(0, Number(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m ? `${m}m ${r}s` : `${r}s`;
}

function formatTime(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data?.detail || res.statusText || "Request failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("pill-api").textContent = "API online";
    $("pill-api").className = "pill ok";
    if (h.zoom_configured) {
      $("pill-zoom").textContent = "Zoom configured";
      $("pill-zoom").className = "pill ok";
    } else {
      $("pill-zoom").textContent = "Zoom credentials missing";
      $("pill-zoom").className = "pill bad";
    }
    if (h.default_meeting_id && !$("meeting-id").value) {
      $("meeting-id").value = h.default_meeting_id;
    }
  } catch (err) {
    $("pill-api").textContent = "API offline";
    $("pill-api").className = "pill bad";
    $("pill-zoom").textContent = "Zoom unknown";
    $("pill-zoom").className = "pill bad";
    $("sync-msg").textContent = err.message;
  }
}

function meetingId() {
  return ($("meeting-id").value || "").replace(/\D/g, "");
}

async function loadStats() {
  const mid = meetingId();
  const qs = mid ? `?meeting_id=${encodeURIComponent(mid)}` : "";
  const s = await api(`/api/stats${qs}`);
  $("stat-total").textContent = s.total_logged;
  $("stat-live").textContent = s.in_meeting;
  $("stat-attended").textContent = s.attended;
  $("stat-partial").textContent = s.partial;
  $("stat-noshow").textContent = s.registered_only;
  $("last-sync").textContent = s.last_sync_at
    ? `Last sync: ${formatTime(s.last_sync_at)}`
    : "Last sync: —";
  if (s.meeting_id && !mid) $("meeting-id").value = s.meeting_id;
}

async function loadParticipants() {
  const mid = meetingId();
  const label = $("label-filter").value;
  const q = $("search").value.trim();
  const params = new URLSearchParams();
  if (mid) params.set("meeting_id", mid);
  if (label) params.set("label", label);
  if (q) params.set("q", q);
  const rows = await api(`/api/participants?${params.toString()}`);
  const tbody = $("rows");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No participants yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((r) => `
    <tr>
      <td>${escapeHtml(r.name || "—")}</td>
      <td>${escapeHtml(r.email || "—")}</td>
      <td><span class="badge ${escapeHtml(r.label)}">${escapeHtml(r.label)}</span></td>
      <td>${formatDuration(r.duration_seconds)}</td>
      <td>${escapeHtml(formatTime(r.join_time))}</td>
      <td>${escapeHtml(formatTime(r.leave_time))}</td>
      <td>${escapeHtml(r.source || "—")}</td>
    </tr>
  `).join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function refreshAll() {
  await loadStats();
  await loadParticipants();
}

async function syncNow() {
  const mid = meetingId();
  if (!mid) {
    $("sync-msg").textContent = "Enter a meeting / webinar ID first.";
    return;
  }
  $("btn-sync").disabled = true;
  $("sync-msg").textContent = "Syncing from Zoom…";
  try {
    const result = await api("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ meeting_id: mid, mode: $("mode").value }),
    });
    $("sync-msg").textContent =
      `${result.message}: fetched ${result.fetched}, logged ${result.upserted} ` +
      `(live ${result.in_meeting}, attended ${result.attendees}, partial ${result.partial})`;
    await refreshAll();
  } catch (err) {
    $("sync-msg").textContent = err.message;
  } finally {
    $("btn-sync").disabled = false;
  }
}

async function importRoster(file) {
  const mid = meetingId();
  if (!mid) {
    $("sync-msg").textContent = "Enter a meeting ID before importing registrants.";
    return;
  }
  const body = new FormData();
  body.append("file", file);
  try {
    const result = await api(`/api/registrants/import?meeting_id=${encodeURIComponent(mid)}`, {
      method: "POST",
      body,
    });
    $("sync-msg").textContent = `Imported ${result.imported} registrants. Sync Zoom to classify attendees vs registered-only.`;
    await refreshAll();
  } catch (err) {
    $("sync-msg").textContent = err.message;
  }
}

function exportCsv() {
  const mid = meetingId();
  if (!mid) {
    $("sync-msg").textContent = "Enter a meeting ID to export.";
    return;
  }
  window.location.href = `/api/export.csv?meeting_id=${encodeURIComponent(mid)}`;
}

$("btn-sync").addEventListener("click", syncNow);
$("btn-refresh").addEventListener("click", () => refreshAll().catch((e) => {
  $("sync-msg").textContent = e.message;
}));
$("btn-export").addEventListener("click", exportCsv);
$("label-filter").addEventListener("change", () => loadParticipants());
$("search").addEventListener("input", () => {
  clearTimeout(window.__searchTimer);
  window.__searchTimer = setTimeout(() => loadParticipants(), 250);
});
$("roster-file").addEventListener("change", (ev) => {
  const file = ev.target.files?.[0];
  if (file) importRoster(file);
  ev.target.value = "";
});

loadHealth().then(refreshAll).catch((e) => {
  $("sync-msg").textContent = e.message;
});
