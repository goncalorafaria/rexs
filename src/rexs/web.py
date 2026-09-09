"""Self-contained Beaker-inspired web interface for the REXS controller."""

APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>REXS · Experiments</title>
<style>
:root {
  color-scheme: light;
  --nav: #171a2b;
  --nav-muted: #9da5bd;
  --ink: #20243a;
  --muted: #667085;
  --line: #e4e7ec;
  --surface: #ffffff;
  --canvas: #f7f8fa;
  --primary: #1769e0;
  --primary-soft: #e9f2ff;
  --success: #18864b;
  --success-soft: #e8f7ef;
  --warning: #b35c00;
  --warning-soft: #fff3df;
  --danger: #c42c31;
  --danger-soft: #fdeced;
  --neutral-soft: #eef0f4;
  font-family: Manrope, Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--canvas); color: var(--ink); min-width: 320px; }
button, input, select { font: inherit; }
button { cursor: pointer; }
.shell { min-height: 100vh; display: grid; grid-template-columns: 248px minmax(0, 1fr); }
.sidebar { position: sticky; top: 0; height: 100vh; padding: 24px 16px; background: var(--nav); color: white; }
.brand { display: flex; align-items: center; gap: 11px; padding: 0 10px 28px; }
.brand-mark { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 9px; background: #4a8df0; font-weight: 800; font-size: 18px; }
.brand-name { font-size: 19px; font-weight: 800; letter-spacing: .02em; }
.brand-caption { color: var(--nav-muted); font-size: 11px; margin-top: 2px; }
.nav-label { padding: 16px 12px 7px; color: #727c99; font-size: 11px; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
.nav-item { width: 100%; display: flex; align-items: center; gap: 10px; border: 0; border-radius: 7px; padding: 10px 12px; background: transparent; color: #cbd1e1; text-align: left; }
.nav-item:hover { background: #242940; color: white; }
.nav-item.active { background: #303650; color: white; font-weight: 700; }
.nav-icon { width: 20px; text-align: center; font-size: 16px; }
.nav-count { margin-left: auto; min-width: 24px; padding: 2px 7px; border-radius: 10px; background: #3b425f; color: #dfe5f4; font-size: 11px; text-align: center; }
.side-note { position: absolute; bottom: 22px; left: 26px; right: 24px; color: var(--nav-muted); font-size: 12px; line-height: 1.5; }
.side-note strong { display: block; color: #dce1ef; }
.main { min-width: 0; }
.topbar { height: 72px; display: flex; align-items: center; gap: 12px; padding: 0 34px; border-bottom: 1px solid var(--line); background: var(--surface); }
.breadcrumb { color: var(--muted); font-size: 14px; }
.breadcrumb strong { color: var(--ink); }
.top-actions { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.sync-state { color: var(--muted); font-size: 12px; }
.content { max-width: 1500px; margin: 0 auto; padding: 34px; }
.page-head { display: flex; align-items: flex-start; gap: 20px; margin-bottom: 26px; }
.page-head h1 { margin: 0; font-size: 30px; line-height: 1.2; letter-spacing: -.02em; }
.page-subtitle { margin: 7px 0 0; color: var(--muted); font-size: 14px; }
.page-actions { margin-left: auto; display: flex; gap: 9px; }
.button { min-height: 38px; padding: 8px 14px; border: 1px solid #cfd4dc; border-radius: 6px; background: white; color: var(--ink); font-weight: 700; font-size: 13px; }
.button:hover { border-color: #9fa8b7; background: #fafbfc; }
.button.primary { border-color: var(--primary); background: var(--primary); color: white; }
.button.danger { border-color: #e0a5a8; color: var(--danger); }
.button.danger:hover { border-color: var(--danger); background: var(--danger-soft); }
.button:disabled { opacity: .48; cursor: not-allowed; }
.filter-card, .table-card, .panel { border: 1px solid var(--line); border-radius: 9px; background: var(--surface); box-shadow: 0 1px 2px rgba(16,24,40,.03); }
.filter-card { display: grid; grid-template-columns: minmax(360px, 1.6fr) minmax(220px, .8fr); gap: 14px; padding: 15px; margin-bottom: 18px; }
.status-filters { display: flex; flex-wrap: wrap; gap: 7px; }
.filter-chip { border: 1px solid #d5d9e1; border-radius: 18px; padding: 7px 12px; background: white; color: #4a5265; font-size: 12px; font-weight: 700; }
.filter-chip:hover { border-color: #aab2c0; }
.filter-chip.active { border-color: #8bb8f4; background: var(--primary-soft); color: #1256b5; }
.search { width: 100%; height: 38px; border: 1px solid #cfd4dc; border-radius: 6px; padding: 0 12px; outline: none; }
.search:focus { border-color: #6da1e9; box-shadow: 0 0 0 3px #e8f1fd; }
.table-card { overflow: hidden; }
.table-summary { display: flex; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 13px; }
.table-summary strong { color: var(--ink); }
table { width: 100%; border-collapse: collapse; }
th { padding: 12px 16px; background: #fafbfc; border-bottom: 1px solid var(--line); color: #5c6578; text-align: left; font-size: 11px; letter-spacing: .05em; text-transform: uppercase; }
td { padding: 15px 16px; border-bottom: 1px solid #edf0f3; vertical-align: middle; font-size: 13px; }
tbody tr { transition: background .12s; }
tbody tr:hover { background: #f8fbff; }
tbody tr:last-child td { border-bottom: 0; }
.name-link { border: 0; padding: 0; background: none; color: #155ebd; font-weight: 750; text-align: left; }
.name-link:hover { color: #0d4791; text-decoration: underline; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.muted { color: var(--muted); }
.status { display: inline-flex; align-items: center; gap: 7px; min-width: 102px; font-size: 12px; font-weight: 750; }
.status-dot { width: 9px; height: 9px; border-radius: 50%; background: #8991a1; box-shadow: 0 0 0 3px var(--neutral-soft); }
.status.running .status-dot { background: var(--warning); box-shadow: 0 0 0 3px var(--warning-soft); }
.status.pending .status-dot { background: #6b63ce; box-shadow: 0 0 0 3px #efedff; }
.status.completed .status-dot { background: var(--success); box-shadow: 0 0 0 3px var(--success-soft); }
.status.failed .status-dot { background: var(--danger); box-shadow: 0 0 0 3px var(--danger-soft); }
.status.cancelled .status-dot { background: #6e7583; box-shadow: 0 0 0 3px var(--neutral-soft); }
.empty { padding: 70px 20px; text-align: center; color: var(--muted); }
.empty-icon { width: 44px; height: 44px; display: grid; place-items: center; margin: 0 auto 12px; border-radius: 50%; background: var(--neutral-soft); font-size: 20px; }
.error-banner { display: none; padding: 12px 15px; margin-bottom: 18px; border: 1px solid #efb5b8; border-radius: 7px; background: var(--danger-soft); color: #8d2226; font-size: 13px; }
.error-banner.visible { display: block; }
.detail-status { margin-top: 12px; }
.meta-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0 0 22px; }
.meta-card { padding: 15px 17px; border: 1px solid var(--line); border-radius: 8px; background: white; min-width: 0; }
.meta-label { color: var(--muted); font-size: 11px; font-weight: 750; text-transform: uppercase; letter-spacing: .05em; }
.meta-value { margin-top: 7px; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tabs { display: flex; gap: 22px; padding: 0 20px; border-bottom: 1px solid var(--line); background: white; }
.tab { border: 0; border-bottom: 3px solid transparent; padding: 15px 2px 12px; background: none; color: var(--muted); font-weight: 700; font-size: 13px; }
.tab:hover { color: var(--ink); }
.tab.active { border-bottom-color: var(--primary); color: var(--ink); }
.tab-content { display: none; padding: 22px; }
.tab-content.active { display: block; }
.section-title { margin: 0 0 14px; font-size: 17px; }
.task-list { display: grid; gap: 10px; }
.task-row { display: grid; grid-template-columns: minmax(180px, 1fr) 100px minmax(260px, 1.4fr) auto; align-items: center; gap: 12px; padding: 13px 15px; border: 1px solid var(--line); border-radius: 7px; }
.task-name { font-weight: 750; }
.log-layout { display: grid; grid-template-columns: 240px minmax(0, 1fr); min-height: 480px; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.log-nav { padding: 10px; border-right: 1px solid var(--line); background: #fafbfc; }
.log-option { width: 100%; border: 0; border-radius: 5px; padding: 10px; background: transparent; text-align: left; color: #4d5668; }
.log-option:hover { background: #eef3fa; }
.log-option.active { background: var(--primary-soft); color: #1256b5; font-weight: 750; }
.log-view { min-width: 0; background: #111827; color: #d5dbea; }
.log-toolbar { height: 48px; display: flex; align-items: center; gap: 10px; padding: 0 13px; border-bottom: 1px solid #30384b; background: #182033; }
.log-toolbar select { margin-left: auto; border: 1px solid #4c5870; border-radius: 4px; padding: 4px 7px; background: #111827; color: white; }
.log-output { min-height: 430px; max-height: 68vh; margin: 0; padding: 16px; overflow: auto; white-space: pre-wrap; word-break: break-word; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.config-layout { display: grid; grid-template-columns: minmax(260px, .75fr) minmax(420px, 1.4fr); gap: 18px; }
.field-card { border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.field-row { display: grid; grid-template-columns: minmax(110px, .6fr) minmax(0, 1.4fr); border-bottom: 1px solid var(--line); }
.field-row:last-child { border-bottom: 0; }
.field-key, .field-value { padding: 10px 12px; font-size: 12px; }
.field-key { background: #fafbfc; color: var(--muted); font-weight: 700; }
.field-value { word-break: break-word; }
.code-wrap { position: relative; min-width: 0; }
.code-actions { position: absolute; top: 10px; right: 10px; display: flex; gap: 6px; }
.code-button { border: 1px solid #59647a; border-radius: 4px; padding: 5px 9px; background: #1c2538; color: #e1e6f0; font-size: 11px; }
.spec-code { min-height: 420px; max-height: 68vh; margin: 0; padding: 48px 18px 18px; overflow: auto; border-radius: 7px; background: #111827; color: #d5dbea; white-space: pre; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.timeline { position: relative; margin-left: 7px; padding-left: 25px; }
.timeline:before { content: ""; position: absolute; top: 7px; bottom: 10px; left: 5px; width: 2px; background: var(--line); }
.event { position: relative; margin: 0 0 23px; }
.event:before { content: ""; position: absolute; left: -25px; top: 4px; width: 10px; height: 10px; border: 2px solid white; border-radius: 50%; background: #7a8497; box-shadow: 0 0 0 1px #aab1bd; }
.event-title { font-size: 13px; font-weight: 750; }
.event-meta, .event-detail { margin-top: 4px; color: var(--muted); font-size: 12px; }
.loading { display: grid; place-items: center; min-height: 320px; color: var(--muted); }
.spinner { width: 20px; height: 20px; margin-right: 9px; display: inline-block; vertical-align: middle; border: 2px solid #cbd5e1; border-top-color: var(--primary); border-radius: 50%; animation: spin .75s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 980px) {
  .shell { grid-template-columns: 68px minmax(0, 1fr); }
  .sidebar { padding: 20px 10px; }
  .brand { padding: 0 7px 24px; }
  .brand-copy, .nav-label, .nav-text, .nav-count, .side-note { display: none; }
  .nav-item { justify-content: center; padding: 11px; }
  .meta-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .config-layout { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .shell { display: block; }
  .sidebar { position: static; width: 100%; height: auto; display: flex; align-items: center; padding: 10px 14px; }
  .brand { padding: 0; margin-right: auto; }
  .brand-copy { display: block; }
  .sidebar nav { display: flex; }
  .nav-label, .nav-count, .nav-text { display: none; }
  .topbar { padding: 0 18px; }
  .content { padding: 24px 16px; }
  .filter-card { grid-template-columns: 1fr; }
  .table-card { overflow-x: auto; }
  table { min-width: 760px; }
  .page-head { flex-wrap: wrap; }
  .page-actions { margin-left: 0; width: 100%; }
  .meta-grid { grid-template-columns: 1fr; }
  .log-layout { grid-template-columns: 1fr; }
  .log-nav { border-right: 0; border-bottom: 1px solid var(--line); display: flex; overflow-x: auto; }
  .log-option { min-width: 160px; }
  .task-row { grid-template-columns: 1fr auto; }
  .task-row .path { display: none; }
}
</style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">R</div>
      <div class="brand-copy"><div class="brand-name">REXS</div><div class="brand-caption">Experiments on Slurm</div></div>
    </div>
    <nav aria-label="Main navigation">
      <div class="nav-label">Experiments</div>
      <button class="nav-item active" data-nav-filter="all"><span class="nav-icon">▦</span><span class="nav-text">All experiments</span><span class="nav-count" id="count-all">0</span></button>
      <button class="nav-item" data-nav-filter="running"><span class="nav-icon">▶</span><span class="nav-text">Running</span><span class="nav-count" id="count-running">0</span></button>
      <button class="nav-item" data-nav-filter="failed"><span class="nav-icon">!</span><span class="nav-text">Failed</span><span class="nav-count" id="count-failed">0</span></button>
      <button class="nav-item" data-nav-filter="cancelled"><span class="nav-icon">⊘</span><span class="nav-text">Canceled</span><span class="nav-count" id="count-cancelled">0</span></button>
    </nav>
    <div class="side-note"><strong>Slurm is authoritative</strong>REXS records observed state in SQLite.</div>
  </aside>
  <main class="main">
    <header class="topbar">
      <div class="breadcrumb" id="breadcrumb">REXS / <strong>Experiments</strong></div>
      <div class="top-actions"><span class="sync-state" id="sync-state">Not synced</span><button class="button" id="refresh-button">↻ Refresh</button></div>
    </header>
    <div class="content">
      <div class="error-banner" id="error-banner" role="alert"></div>
      <div id="app"><div class="loading"><span><i class="spinner"></i>Loading experiments…</span></div></div>
    </div>
  </main>
</div>
<script>
const app = document.getElementById('app');
const errorBanner = document.getElementById('error-banner');
const terminalStatuses = new Set(['COMPLETED','FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','SUBMISSION_FAILED']);
const failedStatuses = new Set(['FAILED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','SUBMISSION_FAILED']);
const pendingStatuses = new Set(['GENERATED','SUBMITTED','PENDING','SUSPENDED','UNKNOWN']);
let experiments = [];
let activeFilter = 'all';
let activeSearch = '';
let activeTab = 'overview';
let selectedLog = null;
let logLineCount = 200;
let refreshTimer = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}
function statusClass(status) {
  if (status === 'RUNNING') return 'running';
  if (status === 'COMPLETED') return 'completed';
  if (status === 'CANCELLED') return 'cancelled';
  if (failedStatuses.has(status)) return 'failed';
  if (pendingStatuses.has(status)) return 'pending';
  return '';
}
function statusLabel(status) {
  const labels = {COMPLETED:'Succeeded', CANCELLED:'Canceled', OUT_OF_MEMORY:'Out of memory', NODE_FAIL:'Node failed', SUBMISSION_FAILED:'Submission failed'};
  return labels[status] || status.toLowerCase().replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
}
function statusMarkup(status) {
  return `<span class="status ${statusClass(status)}"><i class="status-dot"></i>${escapeHtml(statusLabel(status))}</span>`;
}
function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
function shortId(value) { return value ? value.slice(0, 10) : '—'; }
function setError(message) {
  errorBanner.textContent = message || '';
  errorBanner.classList.toggle('visible', Boolean(message));
}
async function request(url, options) {
  const response = await fetch(url, options);
  const value = await response.json().catch(() => ({error: response.statusText}));
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}
function navigate(path) {
  history.pushState({}, '', path);
  renderRoute();
}
function filterMatches(item, filter) {
  if (filter === 'all') return true;
  if (filter === 'running') return item.status === 'RUNNING';
  if (filter === 'pending') return pendingStatuses.has(item.status);
  if (filter === 'completed') return item.status === 'COMPLETED';
  if (filter === 'failed') return failedStatuses.has(item.status);
  if (filter === 'cancelled') return item.status === 'CANCELLED';
  return true;
}
function updateCounts() {
  const counts = {
    all: experiments.length,
    running: experiments.filter(item => item.status === 'RUNNING').length,
    failed: experiments.filter(item => failedStatuses.has(item.status)).length,
    cancelled: experiments.filter(item => item.status === 'CANCELLED').length,
  };
  Object.entries(counts).forEach(([key, value]) => {
    const target = document.getElementById(`count-${key}`);
    if (target) target.textContent = value;
  });
}
async function loadExperiments({refresh=false} = {}) {
  setError('');
  try {
    if (refresh) await request('/api/refresh', {method: 'POST'});
    experiments = await request('/api/experiments');
    updateCounts();
    document.getElementById('sync-state').textContent = `Updated ${new Date().toLocaleTimeString()}`;
    if (location.pathname === '/') renderList();
  } catch (error) { setError(error.message); }
}
function renderList() {
  document.title = 'REXS · Experiments';
  document.getElementById('breadcrumb').innerHTML = 'REXS / <strong>Experiments</strong>';
  const query = activeSearch.trim().toLowerCase();
  const rows = experiments.filter(item => filterMatches(item, activeFilter)).filter(item => {
    if (!query) return true;
    return [item.name, item.id, item.job_id, item.status].some(value => String(value || '').toLowerCase().includes(query));
  });
  const chips = [
    ['all','All'], ['running','Running'], ['pending','Queued'], ['completed','Succeeded'], ['failed','Failed'], ['cancelled','Canceled'],
  ].map(([key,label]) => `<button class="filter-chip ${activeFilter === key ? 'active' : ''}" data-filter="${key}">${label}</button>`).join('');
  const body = rows.length ? rows.map(item => `
    <tr>
      <td>${statusMarkup(item.status)}</td>
      <td><button class="name-link" data-open="${escapeHtml(item.id)}">${escapeHtml(item.name)}</button><div class="mono muted">${escapeHtml(shortId(item.id))}</div></td>
      <td class="mono">${escapeHtml(item.job_id || 'Not submitted')}</td>
      <td>${escapeHtml(formatTime(item.created_at))}</td>
      <td>${escapeHtml(formatTime(item.updated_at))}</td>
      <td><button class="button" data-open="${escapeHtml(item.id)}">View</button></td>
    </tr>`).join('') : `<tr><td colspan="6"><div class="empty"><div class="empty-icon">⌕</div><strong>No experiments found</strong><div>Try another status or search term.</div></div></td></tr>`;
  app.innerHTML = `
    <div class="page-head"><div><h1>Experiments</h1><p class="page-subtitle">History of Beaker experiment configurations executed through Slurm.</p></div></div>
    <div class="filter-card"><div class="status-filters" aria-label="Experiment status">${chips}</div><input class="search" id="search" type="search" placeholder="Search name, ID, Slurm job, or status" value="${escapeHtml(activeSearch)}" aria-label="Search experiments"></div>
    <div class="table-card"><div class="table-summary"><strong>${rows.length}</strong>&nbsp;of&nbsp;<strong>${experiments.length}</strong>&nbsp;experiments</div>
      <table aria-label="Experiment history"><thead><tr><th>Status</th><th>Name</th><th>Slurm job</th><th>Created</th><th>Updated</th><th></th></tr></thead><tbody>${body}</tbody></table>
    </div>`;
  document.querySelectorAll('[data-filter]').forEach(button => button.onclick = () => {
    activeFilter = button.dataset.filter;
    syncNavigationFilter();
    renderList();
  });
  document.querySelectorAll('[data-open]').forEach(button => button.onclick = () => navigate(`/experiment/${encodeURIComponent(button.dataset.open)}`));
  document.getElementById('search').oninput = event => { activeSearch = event.target.value; renderList(); const input = document.getElementById('search'); input.focus(); input.setSelectionRange(activeSearch.length, activeSearch.length); };
}
function syncNavigationFilter() {
  document.querySelectorAll('[data-nav-filter]').forEach(item => item.classList.toggle('active', item.dataset.navFilter === activeFilter));
}
function metadataCards(item, taskCount) {
  const values = [
    ['REXS ID', item.id], ['Slurm job', item.job_id || 'Not submitted'], ['Task replicas', taskCount], ['Created', formatTime(item.created_at)],
  ];
  return values.map(([label,value]) => `<div class="meta-card"><div class="meta-label">${escapeHtml(label)}</div><div class="meta-value ${label.includes('ID') || label.includes('job') ? 'mono' : ''}" title="${escapeHtml(value)}">${escapeHtml(value)}</div></div>`).join('');
}
function renderFields(value) {
  if (!value || typeof value !== 'object') return `<div class="field-row"><div class="field-key">Value</div><div class="field-value mono">${escapeHtml(JSON.stringify(value))}</div></div>`;
  return Object.entries(value).map(([key,item]) => {
    const display = item && typeof item === 'object' ? `<pre class="mono" style="margin:0;white-space:pre-wrap">${escapeHtml(JSON.stringify(item, null, 2))}</pre>` : escapeHtml(item);
    return `<div class="field-row"><div class="field-key">${escapeHtml(key)}</div><div class="field-value">${display}</div></div>`;
  }).join('');
}
function renderEvents(events) {
  if (!events.length) return '<div class="empty">No state transitions recorded.</div>';
  return `<div class="timeline">${events.slice().reverse().map(event => `<div class="event"><div class="event-title">${escapeHtml(statusLabel(event.status))}</div><div class="event-meta">${escapeHtml(formatTime(event.occurred_at))}${event.previous_status ? ` · from ${escapeHtml(statusLabel(event.previous_status))}` : ''}</div>${event.detail ? `<div class="event-detail">${escapeHtml(event.detail)}</div>` : ''}</div>`).join('')}</div>`;
}
function logPanel(tasks) {
  if (!tasks.length) return '<div class="empty">No task replicas were recorded for this experiment.</div>';
  if (!selectedLog || !tasks.some(task => `${task.name}:${task.replica_rank}` === selectedLog)) selectedLog = `${tasks[0].name}:${tasks[0].replica_rank}`;
  const current = tasks.find(task => `${task.name}:${task.replica_rank}` === selectedLog) || tasks[0];
  return `<div class="log-layout"><div class="log-nav">${tasks.map(task => {
    const key = `${task.name}:${task.replica_rank}`;
    return `<button class="log-option ${key === selectedLog ? 'active' : ''}" data-log="${escapeHtml(key)}"><strong>${escapeHtml(task.name)}</strong><br><span class="muted">Replica ${task.replica_rank}${task.exists ? '' : ' · waiting'}</span></button>`;
  }).join('')}</div><div class="log-view"><div class="log-toolbar"><span class="mono">${escapeHtml(current.log_path || `${current.name}.${current.replica_rank}.log`)}</span><select id="log-lines" aria-label="Recent log lines"><option value="200">200 lines</option><option value="500">500 lines</option><option value="1000">1,000 lines</option><option value="5000">5,000 lines</option></select></div><pre class="log-output">${escapeHtml(current.content || (current.exists ? '(empty log)' : 'Log file has not been created yet.'))}</pre></div></div>`;
}
async function loadDetail(identifier, {quiet=false} = {}) {
  if (!quiet) app.innerHTML = '<div class="loading"><span><i class="spinner"></i>Loading experiment…</span></div>';
  setError('');
  try {
    const detail = await request(`/api/experiments/${encodeURIComponent(identifier)}?lines=${logLineCount}`);
    renderDetail(detail);
  } catch (error) { setError(error.message); app.innerHTML = '<div class="empty">Experiment could not be loaded.</div>'; }
}
function renderDetail(detail) {
  const item = detail.experiment;
  document.title = `REXS · ${item.name}`;
  document.getElementById('breadcrumb').innerHTML = '<button class="name-link" id="crumb-home">Experiments</button> / <strong>' + escapeHtml(item.name) + '</strong>';
  const canCancel = Boolean(item.job_id) && !terminalStatuses.has(item.status);
  const tabs = [['overview','Overview'],['logs','Logs'],['configuration','Configuration'],['history','History']];
  const overview = `<h2 class="section-title">Task replicas</h2><div class="task-list">${detail.tasks.length ? detail.tasks.map(task => `<div class="task-row"><div><div class="task-name">${escapeHtml(task.name)}</div><div class="muted">Replica ${task.replica_rank}</div></div><div>${task.exists ? '<span style="color:var(--success)">● Log ready</span>' : '<span class="muted">○ Waiting</span>'}</div><div class="mono muted path">${escapeHtml(task.result_path || 'Result path pending')}</div><button class="button" data-task-log="${escapeHtml(`${task.name}:${task.replica_rank}`)}">Logs</button></div>`).join('') : '<div class="empty">No task replicas recorded.</div>'}</div>`;
  const configuration = `<div class="config-layout"><div><h2 class="section-title">Experiment fields</h2><div class="field-card">${renderFields(detail.spec)}</div></div><div><h2 class="section-title">Executed YAML</h2><div class="code-wrap"><div class="code-actions"><button class="code-button" id="copy-spec">Copy</button><button class="code-button" id="download-spec">Download</button></div><pre class="spec-code" id="spec-code">${escapeHtml(item.spec_text || '(snapshot unavailable)')}</pre></div></div></div>`;
  app.innerHTML = `
    <div class="page-head"><div><button class="name-link" id="back-button">← Experiments</button><h1 style="margin-top:13px">${escapeHtml(item.name)}</h1><div class="detail-status">${statusMarkup(item.status)}</div></div><div class="page-actions"><button class="button" id="detail-refresh">↻ Refresh</button><button class="button danger" id="cancel-button" ${canCancel ? '' : 'disabled'}>Cancel experiment</button></div></div>
    <div class="meta-grid">${metadataCards(item, detail.tasks.length)}</div>
    <div class="panel"><div class="tabs">${tabs.map(([key,label]) => `<button class="tab ${activeTab === key ? 'active' : ''}" data-tab="${key}">${label}${key === 'logs' ? ` (${detail.tasks.length})` : ''}</button>`).join('')}</div>
      <div class="tab-content ${activeTab === 'overview' ? 'active' : ''}" data-content="overview">${overview}</div>
      <div class="tab-content ${activeTab === 'logs' ? 'active' : ''}" data-content="logs"><h2 class="section-title">Replica logs</h2>${logPanel(detail.tasks)}</div>
      <div class="tab-content ${activeTab === 'configuration' ? 'active' : ''}" data-content="configuration">${configuration}</div>
      <div class="tab-content ${activeTab === 'history' ? 'active' : ''}" data-content="history"><h2 class="section-title">Status history</h2>${renderEvents(detail.events)}</div>
    </div>`;
  document.getElementById('crumb-home').onclick = () => navigate('/');
  document.getElementById('back-button').onclick = () => navigate('/');
  document.getElementById('detail-refresh').onclick = async () => {
    try { await request('/api/refresh', {method:'POST'}); await loadDetail(item.id, {quiet:true}); } catch (error) { setError(error.message); }
  };
  document.getElementById('cancel-button').onclick = async () => {
    if (!canCancel || !confirm(`Cancel ${item.name} (Slurm job ${item.job_id})?`)) return;
    const button = document.getElementById('cancel-button'); button.disabled = true; button.textContent = 'Canceling…';
    try { await request(`/api/experiments/${encodeURIComponent(item.id)}/cancel`, {method:'POST'}); await loadDetail(item.id, {quiet:true}); await loadExperiments(); }
    catch (error) { setError(error.message); button.disabled = false; button.textContent = 'Cancel experiment'; }
  };
  document.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => { activeTab = button.dataset.tab; renderDetail(detail); });
  document.querySelectorAll('[data-task-log]').forEach(button => button.onclick = () => { selectedLog = button.dataset.taskLog; activeTab = 'logs'; renderDetail(detail); });
  document.querySelectorAll('[data-log]').forEach(button => button.onclick = () => { selectedLog = button.dataset.log; renderDetail(detail); });
  const logLines = document.getElementById('log-lines');
  if (logLines) {
    logLines.value = String(logLineCount);
    logLines.onchange = async () => { logLineCount = Number(logLines.value); await loadDetail(item.id, {quiet:true}); };
  }
  const copy = document.getElementById('copy-spec');
  if (copy) copy.onclick = async () => { await navigator.clipboard.writeText(item.spec_text || ''); copy.textContent = 'Copied'; setTimeout(() => copy.textContent = 'Copy', 1200); };
  const download = document.getElementById('download-spec');
  if (download) download.onclick = () => {
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([item.spec_text || ''], {type:'text/yaml'})); link.download = `${item.name || 'experiment'}.yaml`; link.click(); URL.revokeObjectURL(link.href);
  };
}
async function renderRoute() {
  clearInterval(refreshTimer);
  setError('');
  if (location.pathname.startsWith('/experiment/')) {
    const identifier = decodeURIComponent(location.pathname.slice('/experiment/'.length));
    if (!experiments.length) await loadExperiments();
    await loadDetail(identifier);
    refreshTimer = setInterval(() => loadDetail(identifier, {quiet:true}), 5000);
  } else {
    if (location.pathname !== '/') history.replaceState({}, '', '/');
    if (!experiments.length) await loadExperiments(); else renderList();
    refreshTimer = setInterval(() => loadExperiments(), 5000);
  }
}
document.getElementById('refresh-button').onclick = async () => {
  const button = document.getElementById('refresh-button'); button.disabled = true;
  try {
    if (location.pathname.startsWith('/experiment/')) {
      await request('/api/refresh', {method:'POST'});
      await loadDetail(decodeURIComponent(location.pathname.slice('/experiment/'.length)), {quiet:true});
    } else await loadExperiments({refresh:true});
  } finally { button.disabled = false; }
};
document.querySelectorAll('[data-nav-filter]').forEach(button => button.onclick = () => { activeFilter = button.dataset.navFilter; syncNavigationFilter(); navigate('/'); });
window.onpopstate = renderRoute;
renderRoute();
</script>
</body>
</html>
"""
