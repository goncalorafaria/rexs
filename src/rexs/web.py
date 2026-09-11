"""Self-contained Beaker-inspired web interface for the REXS controller."""

APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<link rel="icon" type="image/png" href="/assets/rexs-logo.png">
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
.brand-mark { width: 44px; height: 44px; display: block; object-fit: contain; flex-shrink: 0; }
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
.table-card { overflow-x: auto; }
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
.resource-chips { display:flex; flex-wrap:wrap; gap:3px; min-width:165px; max-width:245px; }
.resource-chip { display:inline-flex; align-items:center; gap:3px; padding:2px 5px; border-radius:4px; font-size:10px; line-height:1.3; font-weight:600; white-space:nowrap; }
.resource-chip svg { width:13px; height:13px; }
.resource-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin:14px 0 10px; }
.resource-tile { display:flex; align-items:center; gap:13px; padding:18px; border:1px solid currentColor; border-radius:10px; }
.resource-tile svg { width:34px; height:34px; flex-shrink:0; }
.resource-number { font-size:25px; font-weight:800; line-height:1.2; }
.resource-caption { font-size:12px; margin-top:5px; }
.resource-gpu { color:#177448; background:#edf9f0; }
.resource-cpu { color:#215daa; background:#eef5ff; }
.resource-memory { color:#945c05; background:#fff7df; }
.resource-node { color:#7743aa; background:#f7f0ff; }
.resource-note { margin-bottom:25px; font-size:12px; }
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
.tab-content[data-content="metrics"] { padding:10px; }
.tab-content[data-content="metrics"] > .section-title { display:none; }
.spark-cluster { margin:0 0 16px; }
.spark-cluster h3 { font-size:12px; margin:0 0 6px; font-weight:650; }
.spark-row { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(0,1fr); gap:0; width:100%; min-width:0; max-width:100%; border:1px solid #e8edf2; border-radius:5px; }
.spark-row .spark-tile + .spark-tile { border-left:1px solid #e8edf2; }
.spark-tile { position:relative; min-width:0; height:48px; padding:2px 0; border:0; border-radius:0; }
.spark-number { position:absolute; top:5px; left:5px; font-size:10px; color:var(--muted); }
.gpu-spark { display:block; width:100%; height:44px; }
.spark-point { opacity:0; } .spark-point:hover,.spark-single { opacity:1; }
.spark-empty { display:block; text-align:center; color:var(--muted); padding-top:12px; }
.spark-warning { border-style:dashed; }
.tabs { flex-wrap:wrap; }
</style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <img class="brand-mark" src="/assets/rexs-logo.png" width="44" height="44" alt="REXS T-rex badge">
      <div class="brand-copy"><div class="brand-name">REXS</div><div class="brand-caption">Experiments on Slurm</div></div>
    </div>
    <nav aria-label="Main navigation">
      <div class="nav-label">Experiments</div>
      <button class="nav-item" data-nav-filter="all"><span class="nav-icon">▦</span><span class="nav-text">All experiments</span><span class="nav-count" id="count-all">0</span></button>
      <button class="nav-item active" data-nav-filter="running"><span class="nav-icon">▶</span><span class="nav-text">Running</span><span class="nav-count" id="count-running">0</span></button>
      <button class="nav-item" data-nav-filter="failed"><span class="nav-icon">!</span><span class="nav-text">Failed and canceled</span><span class="nav-count" id="count-failed">0</span></button>
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
// Use the authenticated session for relative API URLs after a link login.
if (location.username || location.password || new URL(location.href).username) {
  const cleanUrl = new URL(location.href);
  cleanUrl.username = "";
  cleanUrl.password = "";
  history.replaceState({}, "", cleanUrl.href);
}
const app = document.getElementById('app');
const errorBanner = document.getElementById('error-banner');
const terminalStatuses = new Set(['COMPLETED','FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','SUBMISSION_FAILED']);
const failedStatuses = new Set(['FAILED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','SUBMISSION_FAILED']);
const pendingStatuses = new Set(['GENERATED','SUBMITTED','PENDING','SUSPENDED','UNKNOWN']);
let experiments = [];
let activeFilter = 'running';
let activeSearch = '';
let activeTab = 'overview';
let selectedLog = null;
const logScrollPositions = new Map();
let logLineCount = 200;
let refreshTimer = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}
// Render terminal styles only; never interpret log text as HTML or links.
function renderAnsi(value) {
  const palette = ['#111827','#f87171','#86efac','#fde047','#60a5fa','#e879f9','#67e8f9','#d5dbea',
    '#94a3b8','#fca5a5','#bbf7d0','#fef08a','#93c5fd','#f0abfc','#a5f3fc','#ffffff'];
  const indexed = n => {
    if (!Number.isInteger(n) || n < 0 || n > 255) return null;
    if (n < 16) return palette[n];
    if (n >= 232) { const v = 8 + (n-232)*10; return `rgb(${v},${v},${v})`; }
    const v = n-16, levels = [0,95,135,175,215,255];
    return `rgb(${levels[Math.floor(v/36)]},${levels[Math.floor(v/6)%6]},${levels[v%6]})`;
  };
  let state = {}, html = '', position = 0;
  const text = String(value ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const emit = chunk => {
    const safe = escapeHtml(chunk.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, ''));
    if (!safe) return;
    const styles = [];
    if (state.fg) styles.push(`color:${state.fg}`);
    if (state.bg) styles.push(`background-color:${state.bg}`);
    if (state.bold) styles.push('font-weight:700');
    if (state.dim) styles.push('opacity:0.65');
    if (state.italic) styles.push('font-style:italic');
    if (state.underline || state.strike) styles.push(`text-decoration:${[state.underline && 'underline',state.strike && 'line-through'].filter(Boolean).join(' ')}`);
    html += styles.length ? `<span style="${styles.join(';')}">${safe}</span>` : safe;
  };
  // Consume CSI, OSC (including hyperlinks), and other terminal control strings.
  const controls = /(?:\x1b\[|\x9b)([0-?]*)([ -\/]*)([@-~])|(?:\x1b\]|\x9d)[\s\S]*?(?:\x07|\x1b\\|\x9c|$)|\x1b[PX^_][\s\S]*?(?:\x1b\\|$)|\x1b[ -\/]*[@-Z\\-_]|\x1b\[[0-?]*[ -\/]*$/g;
  for (const match of text.matchAll(controls)) {
    emit(text.slice(position, match.index));
    position = match.index + match[0].length;
    if (match[3] !== 'm' || match[2] || !/^[\d;]*$/.test(match[1])) continue;
    const codes = match[1].split(';').map(n => Number(n || 0));
    for (let i = 0; i < codes.length; i++) {
      const code = codes[i];
      if (code === 0) state = {};
      else if (code === 1) state.bold = true;
      else if (code === 2) state.dim = true;
      else if (code === 3) state.italic = true;
      else if (code === 4) state.underline = true;
      else if (code === 9) state.strike = true;
      else if (code === 22) { state.bold = false; state.dim = false; }
      else if (code === 23) state.italic = false;
      else if (code === 24) state.underline = false;
      else if (code === 29) state.strike = false;
      else if (code === 39) delete state.fg;
      else if (code === 49) delete state.bg;
      else if (code >= 30 && code <= 37) state.fg = palette[code-30];
      else if (code >= 90 && code <= 97) state.fg = palette[code-90+8];
      else if (code >= 40 && code <= 47) state.bg = palette[code-40];
      else if (code >= 100 && code <= 107) state.bg = palette[code-100+8];
      else if (code === 38 || code === 48) {
        const target = code === 38 ? 'fg' : 'bg', mode = codes[++i];
        if (mode === 5) { const color = indexed(codes[++i]); if (color) state[target] = color; }
        else if (mode === 2) {
          const rgb = codes.slice(i+1, i+4); i += 3;
          if (rgb.length === 3 && rgb.every(n => Number.isInteger(n) && n >= 0 && n <= 255)) state[target] = `rgb(${rgb.join(',')})`;
        }
      }
    }
  }
  emit(text.slice(position));
  return html;
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
  const requestUrl = new URL(url, location.origin);
  requestUrl.username = "";
  requestUrl.password = "";
  const response = await fetch(requestUrl.href, {...options, credentials: "same-origin", headers: {...options?.headers, "X-REXS-Request": "1"}});
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
  if (filter === 'running') return item.status === 'RUNNING' || pendingStatuses.has(item.status);
  if (filter === 'failed') return failedStatuses.has(item.status) || item.status === 'CANCELLED';
  return true;
}
function updateCounts() {
  const counts = {
    all: experiments.length,
    running: experiments.filter(item => filterMatches(item, 'running')).length,
    failed: experiments.filter(item => filterMatches(item, 'failed')).length,
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
function formatStartEstimate(item) {
  const stamp = Date.parse(item.estimated_start_at || '');
  if (!Number.isFinite(stamp)) return 'Estimate unavailable';
  if (stamp < Date.now()) return 'Awaiting new estimate';
  const minutes = Math.ceil((stamp-Date.now())/60000);
  const wait = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes/60)} h ${minutes%60} min`;
  return `~${new Date(stamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',timeZoneName:'short'})} (in ${wait})`;
}
function formatRuntime(item) {
  if (['GENERATED','SUBMITTED','PENDING','SUBMISSION_FAILED'].includes(item.status)) return 'Not started';
  const seconds = item.runtime_seconds;
  if (!Number.isFinite(seconds)) return '—';
  if (seconds === 0) return '0 min';
  if (seconds < 60) return '<1 min';
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor(seconds % 3600 / 60)} min`;
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
    ['running','Running'], ['failed','Failed and canceled'], ['all','All'],
  ].map(([key,label]) => `<button class="filter-chip ${activeFilter === key ? 'active' : ''}" data-filter="${key}">${label}</button>`).join('');
  const body = rows.length ? rows.map(item => `
    <tr>
      <td>${statusMarkup(item.status)}</td>
      <td><button class="name-link" data-open="${escapeHtml(item.id)}">${escapeHtml(item.name)}</button><div class="mono muted">${escapeHtml(shortId(item.id))}</div></td>
      <td class="mono">${escapeHtml(item.job_id || 'Not submitted')}</td>
      <td class="mono" title="Total task replicas">${escapeHtml(item.replica_count ?? '—')}</td>
      <td>${renderResources(item.resources, true)}</td>
      <td class="mono" title="Elapsed execution time reported by Slurm; excludes queue wait">${escapeHtml(formatRuntime(item))}${['SUBMITTED','PENDING'].includes(item.status) ? `<div class="muted" style="font:11px/1.5 sans-serif" title="Slurm estimated start; may change">Start ${escapeHtml(formatStartEstimate(item))}</div>` : ''}</td>
      <td><button class="button" data-open="${escapeHtml(item.id)}">View</button></td>
    </tr>`).join('') : `<tr><td colspan="7"><div class="empty"><div class="empty-icon">⌕</div><strong>No experiments found</strong><div>Try another status or search term.</div></div></td></tr>`;
  app.innerHTML = `
    <div class="page-head"><div><h1>Experiments</h1><p class="page-subtitle">History of Beaker experiment configurations executed through Slurm.</p></div></div>
    <div class="filter-card"><div class="status-filters" aria-label="Experiment status">${chips}</div><input class="search" id="search" type="search" placeholder="Search name, ID, Slurm job, or status" value="${escapeHtml(activeSearch)}" aria-label="Search experiments"></div>
    <div class="table-card"><div class="table-summary"><strong>${rows.length}</strong>&nbsp;of&nbsp;<strong>${experiments.length}</strong>&nbsp;experiments</div>
      <table aria-label="Experiment history"><thead><tr><th>Status</th><th>Name</th><th>Slurm job</th><th>Replicas</th><th>Resources requested</th><th>Runtime</th><th></th></tr></thead><tbody>${body}</tbody></table>
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
function metadataCards(item, taskCount, wandbLinks = [], wandbOffline = false) {
  const values = [
    ['REXS ID', item.id], ['Slurm job', item.job_id || 'Not submitted'], ['W&B', wandbOffline ? 'Offline · no cloud link' : 'No run link captured'], ['Task replicas', taskCount], ['SUBMITTED','PENDING'].includes(item.status) ? ['Estimated start · may change', formatStartEstimate(item)] : ['Runtime', formatRuntime(item)],
  ];
  return values.map(([label,value]) => `<div class="meta-card"><div class="meta-label">${escapeHtml(label)}</div><div class="meta-value ${label.includes('ID') || label.includes('job') ? 'mono' : ''}" title="${escapeHtml(value)}">${label === 'W&B' && wandbLinks.length ? wandbLinks.map((url, index) => `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open run${wandbLinks.length > 1 ? ` ${index + 1}` : ''} ↗</a>`).join(' · ') : escapeHtml(value)}</div></div>`).join('');
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
  }).join('')}</div><div class="log-view"><div class="log-toolbar"><span class="mono">${escapeHtml(current.log_path || `${current.name}.${current.replica_rank}.log`)}</span><select id="log-lines" aria-label="Recent log lines"><option value="200">200 lines</option><option value="500">500 lines</option><option value="1000">1,000 lines</option><option value="5000">5,000 lines</option></select></div><pre class="log-output">${renderAnsi(current.content || (current.exists ? '(empty log)' : 'Log file has not been created yet.'))}</pre></div></div>`;
}
async function loadDetail(identifier, {quiet=false} = {}) {
  if (!quiet) app.innerHTML = '<div class="loading"><span><i class="spinner"></i>Loading experiment…</span></div>';
  setError('');
  try {
    const detail = await request(`/api/experiments/${encodeURIComponent(identifier)}?lines=${logLineCount}`);
    renderDetail(detail);
  } catch (error) { setError(error.message); app.innerHTML = '<div class="empty">Experiment could not be loaded.</div>'; }
}
function gpuSparkline(points, color, maximum, extent, unit) {
  points = (points || []).filter(p => p.length === 2 && p.every(Number.isFinite));
  if (!points.length) return '<span class="spark-empty" title="No samples">—</span>';
  const x = t => 2+(t-extent[0])/(extent[1]-extent[0] || 1)*112;
  const y = v => 40-Math.max(0,Math.min(maximum,v))/maximum*36;
  const path = points.map((p,i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join(' ');
  const area = `${path} L${x(points[points.length-1][0])},40 L${x(points[0][0])},40 Z`;
  return `<svg class="gpu-spark" viewBox="0 0 116 44" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(unit)} over time"><path d="${area}" fill="${color}" opacity=".18"/><path d="${path}" fill="none" stroke="${color}" stroke-width="1.5"/>${points.map(p => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="3" fill="${color}" class="spark-point${points.length === 1 ? ' spark-single' : ''}"><title>${escapeHtml(new Date(p[0]*1000).toLocaleString())} · ${p[1].toFixed(1)} ${escapeHtml(unit)}</title></circle>`).join('')}</svg>`;
}

function renderGpuMetrics(metrics, status) {
  if (!metrics?.gpus.length) return `<div class="empty">${escapeHtml(metrics?.error || 'No GPU samples yet.')}</div>`;
  const notes = [!metrics.enabled && 'Capture disabled', metrics.error].filter(Boolean);
  const times = metrics.gpus.flatMap(g => Object.values(g.series || {}).flat().map(p => p[0])).filter(Number.isFinite);
  const extent = times.length ? times.reduce((e,t) => [Math.min(e[0],t),Math.max(e[1],t)], [Infinity,-Infinity]) : [0,0];
  const memoryInPercent = metrics.gpus.every(g => (g.series?.memoryAllocated || []).length > 0);
  const getPoints = (gpu, kind) => kind === 'utilization' ? gpu.series?.utilization : memoryInPercent ? gpu.series?.memoryAllocated : (gpu.series?.memoryAllocatedBytes || []).map(p => [p[0],p[1]/1073741824]);
  const memoryMax = memoryInPercent ? 100 : metrics.gpus.flatMap(g => getPoints(g,'memory')).reduce((m,p) => Math.max(m,p[1]),1)*1.05;
  return `${notes.length ? `<p class="muted">${escapeHtml(notes.join(' · '))}</p>` : ''}` + [['utilization','Utilization','#16804a'],['memory','Memory','#d97706']].map(([kind,label,color]) => `<section class="spark-cluster"><h3 style="color:${color}">${label}</h3><div class="spark-row" aria-label="${label} across all GPUs">${metrics.sources.map(source => {
    const gpus = metrics.gpus.filter(g => g.source_id === source.id);
    if (!gpus.length) return '';
    return gpus.map(gpu => {
      const stamp = Math.max(...Object.values(gpu.timestamps));
      const stale = !terminalStatuses.has(status) && Date.now()/1000-stamp > 120;
      const detail = `${source.label}\nGPU ${gpu.gpu} · ${label}\n${stale ? 'Stale sample · ' : ''}${new Date(stamp*1000).toLocaleString()}${source.error ? '\n'+source.error : ''}`;
      return `<div class="spark-tile${stale || source.error ? ' spark-warning' : ''}" title="${escapeHtml(detail)}"><span class="spark-number">${escapeHtml(gpu.gpu)}</span>${gpuSparkline(getPoints(gpu,kind),color,kind === 'utilization' ? 100 : memoryMax,extent,kind === 'utilization' || memoryInPercent ? '%' : 'GiB')}</div>`;
    }).join('');
  }).join('')}</div></section>`).join('');
}

function renderResources(resources, compact = false) {
  if (compact && !resources?.available) return `<span class="muted" title="${escapeHtml(resources?.reason || 'Resource request unavailable')}">—</span>`;
  if (!resources?.available) return `<h2 class="section-title">Resources requested</h2><p class="muted">${escapeHtml(resources?.reason || 'Resource request unavailable.')}</p>`;
  const icons = {
    gpu: '<rect x="3" y="6" width="24" height="18" rx="2"/><circle cx="13" cy="15" r="5"/><path d="M22 10v10M7 24v3m5-3v3m5-3v3M27 10h3v10h-3"/>',
    cpu: '<rect x="7" y="7" width="18" height="18" rx="3"/><rect x="12" y="12" width="8" height="8"/><path d="M11 3v4m5-4v4m5-4v4M11 25v4m5-4v4m5-4v4M3 11h4m-4 5h4m-4 5h4m18-10h4m-4 5h4m-4 5h4"/>',
    memory: '<rect x="2" y="8" width="28" height="15" rx="2"/><path d="M6 12h4v6H6zm8 0h4v6h-4zm8 0h4v6h-4zM6 23v4m5-4v4m5-4v4m5-4v4m5-4v4"/>',
    node: '<rect x="5" y="3" width="22" height="11" rx="2"/><rect x="5" y="18" width="22" height="11" rx="2"/><path d="M10 8h1m4 0h7M10 23h1m4 0h7"/>'
  };
  const number = n => n == null ? '—' : n.toLocaleString(undefined, {maximumFractionDigits:1});
  const cards = [
    ['gpu', `${number(resources.gpus)} ${resources.gpu_type || 'GPUs'}`, 'GPUs across the experiment'],
    ['cpu', `${number(resources.cpus)} CPUs`, 'Total CPUs requested'],
    ['memory', resources.memory_all ? 'All memory' : `${number(resources.memory_gib)} GiB`, 'Total host memory'],
    ['node', `${number(resources.nodes)} ${resources.nodes === 1 ? 'node' : 'nodes'}`, 'Compute nodes']
  ];
  if (compact) return `<div class="resource-chips" aria-label="Total requested resources">${cards.map(([kind,value,label]) => `<span class="resource-chip resource-${kind}" title="${escapeHtml(label + (kind === 'gpu' && resources.gpu_type_from_partition ? ' · model inferred from partition' : ''))}"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true">${icons[kind]}</svg>${escapeHtml(value)}</span>`).join('')}</div>`;
  return `<h2 class="section-title">Resources requested</h2><div class="resource-grid">${cards.map(([kind,value,label]) => `<div class="resource-tile resource-${kind}"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true">${icons[kind]}</svg><div><div class="resource-number">${escapeHtml(value)}</div><div class="resource-caption">${label}</div></div></div>`).join('')}</div><p class="muted resource-note">Total submitted allocation${resources.partition ? ` · ${escapeHtml(resources.partition)}` : ''}${resources.gpu_type_from_partition ? ' · GPU model inferred from partition' : ''}. These are requested resources, not live utilization.</p>`;
}

function renderDetail(detail) {
  // Capture the old element before replacing the detail page, including tab switches.
  const previousLog = app.querySelector('.log-output');
  if (previousLog?.clientHeight && previousLog.dataset.scrollKey) {
    logScrollPositions.set(previousLog.dataset.scrollKey, {
      top: previousLog.scrollTop,
      left: previousLog.scrollLeft,
      follow: previousLog.scrollHeight - previousLog.clientHeight - previousLog.scrollTop <= 24,
    });
  }
  const item = detail.experiment;
  document.title = `REXS · ${item.name}`;
  document.getElementById('breadcrumb').innerHTML = '<button class="name-link" id="crumb-home">Experiments</button> / <strong>' + escapeHtml(item.name) + '</strong>';
  const canCancel = Boolean(item.job_id) && !terminalStatuses.has(item.status);
  const tabs = [['overview','Overview'],['logs','Logs'],['configuration','Configuration'],['history','History'],['metrics','GPU metrics']];
  const overview = `${renderResources(detail.resources)}<h2 class="section-title">Task replicas</h2><div class="task-list">${detail.tasks.length ? detail.tasks.map(task => `<div class="task-row"><div><div class="task-name">${escapeHtml(task.name)}</div><div class="muted">Replica ${task.replica_rank}</div></div><div>${task.exists ? '<span style="color:var(--success)">● Log ready</span>' : '<span class="muted">○ Waiting</span>'}</div><div class="mono muted path">${escapeHtml(task.result_path || 'Result path pending')}</div><button class="button" data-task-log="${escapeHtml(`${task.name}:${task.replica_rank}`)}">Logs</button></div>`).join('') : '<div class="empty">No task replicas recorded.</div>'}</div>`;
  const configuration = `<div class="config-layout"><div><h2 class="section-title">Experiment fields</h2><div class="field-card">${renderFields(detail.spec)}</div></div><div><h2 class="section-title">Executed YAML</h2><div class="code-wrap"><div class="code-actions"><button class="code-button" id="copy-spec">Copy</button><button class="code-button" id="download-spec">Download</button></div><pre class="spec-code" id="spec-code">${escapeHtml(item.spec_text || '(snapshot unavailable)')}</pre></div></div></div>`;
  app.innerHTML = `
    <div class="page-head"><div><button class="name-link" id="back-button">← Experiments</button><h1 style="margin-top:13px">${escapeHtml(item.name)}</h1><div class="detail-status">${statusMarkup(item.status)}</div></div><div class="page-actions">${(detail.wandb_links || []).map((url, index, links) => `<a class="button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">W&amp;B${links.length > 1 ? ` ${index + 1}` : ''} ↗</a>`).join('')}<button class="button" id="detail-refresh">↻ Refresh</button><button class="button danger" id="cancel-button" ${canCancel ? '' : 'disabled'}>Cancel experiment</button></div></div>
    <div class="meta-grid">${metadataCards(item, detail.tasks.length, detail.wandb_links || [], detail.wandb_offline)}</div>
    <div class="panel"><div class="tabs">${tabs.map(([key,label]) => `<button class="tab ${activeTab === key ? 'active' : ''}" data-tab="${key}">${label}${key === 'logs' ? ` (${detail.tasks.length})` : ''}</button>`).join('')}</div>
      <div class="tab-content ${activeTab === 'overview' ? 'active' : ''}" data-content="overview">${overview}</div>
      <div class="tab-content ${activeTab === 'logs' ? 'active' : ''}" data-content="logs"><h2 class="section-title">Replica logs</h2>${logPanel(detail.tasks)}</div>
      <div class="tab-content ${activeTab === 'configuration' ? 'active' : ''}" data-content="configuration">${configuration}</div>
      <div class="tab-content ${activeTab === 'history' ? 'active' : ''}" data-content="history"><h2 class="section-title">Status history</h2>${renderEvents(detail.events)}</div>
      <div class="tab-content ${activeTab === 'metrics' ? 'active' : ''}" data-content="metrics"><h2 class="section-title">GPU metrics</h2>${renderGpuMetrics(detail.metrics, item.status)}</div>
    </div>`;
  const logOutput = app.querySelector('.log-output');
  if (logOutput) {
    const key = JSON.stringify([item.id, selectedLog]);
    logOutput.dataset.scrollKey = key;
    if (activeTab === 'logs') {
      const position = logScrollPositions.get(key);
      logOutput.scrollTop = !position || position.follow ? logOutput.scrollHeight : position.top;
      logOutput.scrollLeft = position?.left || 0;
    }
  }
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
