from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from rexs.controller import Controller
from rexs.state import default_db_path
from rexs.web import APP_HTML

_DASHBOARD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>REXS experiments</title>
<style>
:root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
body { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; background: #0b1020; color: #d8e1ff; }
h1 { color: #8bd5ff; } button { padding: .5rem .8rem; cursor: pointer; }
table { width: 100%; border-collapse: collapse; background: #121a2f; }
th, td { padding: .65rem; border-bottom: 1px solid #2a3555; text-align: left; }
a { color: #8bd5ff; } .RUNNING { color: #7ee787; } .FAILED, .TIMEOUT { color: #ff7b72; }
.PENDING, .SUBMITTED { color: #d2a8ff; } .COMPLETED { color: #79c0ff; }
small { color: #92a1c6; }
</style>
</head>
<body>
<h1>REXS</h1>
<p>Reproducible Experiments, eXecuted on Slurm</p>
<p><button id="refresh">Refresh Slurm state</button> <small id="stamp"></small></p>
<table>
<thead><tr><th>Status</th><th>Name</th><th>Job</th><th>Created</th><th>Logs</th></tr></thead>
<tbody id="rows"></tbody>
</table>
<script>
async function load() {
  const response = await fetch('/api/experiments');
  const items = await response.json();
  const rows = document.getElementById('rows');
  rows.textContent = '';
   items.forEach(item => {
    const row = document.createElement('tr');
    const values = [
      item.status,
      item.name,
      item.job_id || '-',
      item.created_at,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      if (index === 0) cell.className = value;
      row.appendChild(cell);
    });
    const logs = document.createElement('td');
    const link = document.createElement('a');
    link.href = '/experiment/' + item.id;
    link.textContent = 'details';
    logs.appendChild(link);
    row.appendChild(logs);
    rows.appendChild(row);
  });
  document.getElementById('stamp').textContent = new Date().toLocaleTimeString();
}
document.getElementById('refresh').onclick = async () => {
  await fetch('/api/refresh', {method: 'POST'});
  await load();
};
load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


_DETAIL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>REXS experiment</title>
<style>
:root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
body { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; background: #0b1020; color: #d8e1ff; }
a { color: #8bd5ff; } pre { background: #121a2f; padding: 1rem; overflow: auto; white-space: pre-wrap; }
section { margin: 2rem 0; } button { padding: .4rem .7rem; }
</style>
</head>
<body>
<p><a href="/">← experiments</a></p>
<h1 id="name">Experiment</h1>
<pre id="metadata"></pre>
<section><h2>Experiment specification</h2><pre id="spec"></pre></section>
<section><h2>Tasks and logs</h2><div id="tasks"></div></section>
<section><h2>Events</h2><pre id="events"></pre></section>
<script>
const id = decodeURIComponent(location.pathname.split('/').pop());
async function load() {
  const response = await fetch('/api/experiments/' + encodeURIComponent(id));
  const value = await response.json();
  document.getElementById('name').textContent = value.experiment.name;
  const metadata = {...value.experiment};
  delete metadata.spec_text;
  document.getElementById('metadata').textContent = JSON.stringify(metadata, null, 2);
  document.getElementById('spec').textContent = value.experiment.spec_text || '(snapshot unavailable)';
  document.getElementById('events').textContent = JSON.stringify(value.events, null, 2);
  const tasks = document.getElementById('tasks');
  tasks.textContent = '';
  value.tasks.forEach(task => {
    const heading = document.createElement('h3');
    heading.textContent = task.name + '[' + task.replica_rank + ']';
    const pre = document.createElement('pre');
    pre.textContent = task.content || '(log not created yet)\n' + (task.log_path || '');
    tasks.appendChild(heading);
    tasks.appendChild(pre);
  });
}
load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


class RexsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: Controller, poll_interval: float) -> None:
        super().__init__(address, RexsHandler)
        self.controller = controller
        self.poll_interval = poll_interval
        self.stop_event = threading.Event()


class RexsHandler(BaseHTTPRequestHandler):
    server: RexsServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/" or path.startswith("/experiment/"):
                self._text(APP_HTML, content_type="text/html; charset=utf-8")
                return
            if path == "/api/experiments":
                params = parse_qs(parsed.query)
                statuses = {
                    value.strip().upper()
                    for value in params.get("status", [])
                    for value in value.split(",")
                    if value.strip()
                }
                records = self.server.controller.store.list(limit=500)
                if statuses:
                    records = [item for item in records if item.status in statuses]
                self._json([item.as_dict() for item in records])
                return
            if path.startswith("/api/experiments/"):
                identifier = path.removeprefix("/api/experiments/")
                self._json(self._experiment(identifier, parsed.query))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (KeyError, ValueError) as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/refresh":
                updates = self.server.controller.refresh()
                self._json([asdict(item) for item in updates])
                return
            prefix = "/api/experiments/"
            suffix = "/cancel"
            if path.startswith(prefix) and path.endswith(suffix):
                identifier = path[len(prefix) : -len(suffix)].rstrip("/")
                if not identifier:
                    raise ValueError("experiment identifier is required")
                experiment = self.server.controller.cancel(identifier)
                self._json(experiment.as_dict())
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            self._json({"error": detail}, status=HTTPStatus.CONFLICT)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[rexs-server] {self.address_string()} {fmt % args}\n")

    def _experiment(self, identifier: str, query: str) -> dict[str, Any]:
        params = parse_qs(query)
        line_count = int(params.get("lines", ["200"])[0])
        experiment = self.server.controller.store.get(identifier)
        spec = yaml.safe_load(experiment.spec_text) if experiment.spec_text else {}
        return {
            "experiment": experiment.as_dict(include_spec=True),
            "spec": spec,
            "tasks": self.server.controller.logs(identifier, lines=line_count),
            "events": self.server.controller.store.events(identifier),
        }

    def _json(self, value: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _text(self, value: str, *, content_type: str) -> None:
        payload = value.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: str | Path | None = None,
    poll_interval: float = 10.0,
) -> None:
    controller = Controller(db_path)
    server = RexsServer((host, port), controller, poll_interval)
    poller = threading.Thread(target=_poll, args=(server,), daemon=True, name="rexs-slurm-poller")
    poller.start()
    print(f"REXS server listening on http://{host}:{port} (db: {controller.store.path})")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.stop_event.set()
        server.server_close()
        poller.join(timeout=2)


def start_daemon(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: str | Path | None = None,
    poll_interval: float = 10.0,
) -> dict[str, Any]:
    state_dir = (Path(db_path).expanduser() if db_path else default_db_path()).parent
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file = state_dir / "server.pid"
    log_file = state_dir / "server.log"
    if pid_file.is_file():
        try:
            existing = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(existing, 0)
        except (ValueError, OSError):
            pass
        else:
            raise RuntimeError(f"REXS server already running with pid {existing}")
    command = [
        sys.executable,
        "-m",
        "rexs",
        "server",
        f"--host={host}",
        f"--port={port}",
        f"--poll_interval={poll_interval}",
        "--daemon=False",
    ]
    if db_path:
        command.append(f"--db={Path(db_path).expanduser()}")
    with log_file.open("ab") as output:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return {"pid": process.pid, "url": f"http://{host}:{port}", "log": str(log_file), "pid_file": str(pid_file)}


def stop_daemon(db_path: str | Path | None = None) -> dict[str, Any]:
    state_dir = (Path(db_path).expanduser() if db_path else default_db_path()).parent
    pid_file = state_dir / "server.pid"
    if not pid_file.is_file():
        return {"stopped": False, "reason": "pid file does not exist"}
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    pid_file.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


def _poll(server: RexsServer) -> None:
    while not server.stop_event.wait(server.poll_interval):
        try:
            server.controller.refresh()
        # A transient Slurm/SQLite failure must not kill the long-lived poller.
        except Exception as exc:  # noqa: BLE001
            print(f"REXS poll failed: {exc}", file=sys.stderr)
