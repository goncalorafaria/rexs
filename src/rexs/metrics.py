"""Optional, read-only ingestion of local W&B transaction logs into SQLite.

Framing reference: https://github.com/wandb/wandb/blob/v0.19.11/wandb/sdk/internal/datastore.py
No W&B login, sync, or communication with a training process is required.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import threading
import time
import zlib
from pathlib import Path

from rexs.state import StateStore

HEADER = struct.pack("<4sHB", b":W&B", 0xBEE1, 0)
BLOCK = 32768
GPU_KEY = re.compile(r"^(?:system\.)?gpu\.(\d+)\.(gpu|memoryAllocated|memoryAllocatedBytes)$")


def records(path: Path, offset: int = 7, budget: int = 4 * 1024 * 1024):
    """Yield complete, CRC-checked records; never consume an unfinished tail."""
    with path.open("rb") as stream:
        if stream.read(7) != HEADER:
            raise ValueError("Unsupported W&B file header")
        stream.seek(offset)
        start = offset
        fragments = bytearray()
        assembling = False
        while stream.tell() - offset < budget or fragments:
            remaining = BLOCK - stream.tell() % BLOCK
            if remaining < 7:
                padding = stream.read(remaining)
                if len(padding) != remaining:
                    return
                if any(padding):
                    raise ValueError("Invalid W&B block padding")
            fragment_start = stream.tell()
            header = stream.read(7)
            if len(header) < 7:
                return
            crc, length, kind = struct.unpack("<IHB", header)
            if kind not in (1, 2, 3, 4) or length > BLOCK - fragment_start % BLOCK - 7:
                raise ValueError("Invalid W&B record framing")
            data = stream.read(length)
            if len(data) < length:
                return
            if zlib.crc32(data, zlib.crc32(bytes([kind]))) & 0xFFFFFFFF != crc:
                raise ValueError("W&B record checksum mismatch")
            if kind in (1, 2):
                if assembling:
                    raise ValueError("Interrupted W&B record")
                fragments.extend(data)
                assembling = kind == 2
            elif not assembling:
                raise ValueError("Missing W&B first fragment")
            else:
                fragments.extend(data)
            if len(fragments) > 16 * 1024 * 1024:
                raise ValueError("W&B record exceeds 16 MiB")
            if kind in (1, 4):
                yield start, stream.tell(), bytes(fragments)
                start = stream.tell()
                fragments.clear()
                assembling = False


def discover(root: Path):
    """Bound discovery to this job's outputs; do not follow directory symlinks."""
    for seen, (directory, dirs, files) in enumerate(os.walk(root, followlinks=False), 1):
        relative = Path(directory).relative_to(root)
        dirs[:] = sorted(
            d
            for d in dirs
            if d
            not in {
                "checkpoints",
                ".cache",
                "token_exports",
                "rollouts",
                "tmp",
                "broadcasts",
            }
            and len(relative.parts) < 9
        )
        if seen > 5000:
            return
        for name in sorted(files):
            p = Path(directory) / name
            if name.endswith(".wandb") and not p.is_symlink():
                yield p


class MetricsCollector:
    def __init__(self, store: StateStore):
        self.store = store
        self.lock = threading.Lock()
        self.last_scan = {}
        self.error = None

    def collect(self, identifier: str):
        if os.environ.get("REXS_CAPTURE_WANDB", "1").lower() in ("0", "false", "no"):
            return
        if not self.lock.acquire(blocking=False):
            return
        try:
            experiment = self.store.get(identifier)
            if not experiment.job_id or time.monotonic() - self.last_scan.get(experiment.id, -60) < 10:
                return
            self.last_scan[experiment.id] = time.monotonic()
            try:
                from wandb.proto.wandb_internal_pb2 import Record
            except ImportError:
                self.error = "Optional W&B reader unavailable. Install rexs[wandb] to capture local metrics."
                return
            self.error = None
            root = Path(experiment.run_root) / experiment.job_id
            for index, path in enumerate(discover(root)):
                if index >= 64:
                    break
                try:
                    self._file(experiment.id, root, path, Record)
                except Exception as exc:  # noqa: BLE001 - isolate optional source failures
                    self._source_error(experiment.id, path, str(exc))
        finally:
            self.lock.release()

    def _source_error(self, experiment_id, path, error):
        with self.store.connect() as db:
            db.execute(
                "UPDATE wandb_sources SET error=? WHERE experiment_id=? AND path=?",
                (error[:300], experiment_id, str(path)),
            )

    def _file(self, experiment_id, root, path, record_class):
        stat = path.stat()
        try:
            first = next(records(path, budget=1), None)
        except ValueError:
            first = (0, 0, b"invalid-header")
        if first is None:
            return
        fingerprint = hashlib.sha256(first[2]).hexdigest()
        source_id = hashlib.sha256(f"{experiment_id}:{path}:{stat.st_ino}:{fingerprint}".encode()).hexdigest()
        with self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO wandb_sources(id,experiment_id,path,label) VALUES(?,?,?,?)",
                (source_id, experiment_id, str(path), str(path.relative_to(root))),
            )
            source = db.execute("SELECT * FROM wandb_sources WHERE id=?", (source_id,)).fetchone()
        offset = source["offset"]
        if stat.st_size < offset:
            raise ValueError("W&B source was truncated; retained previously captured metrics")
        captured = []
        end = offset
        for begin, end, raw in records(path, offset):
            record = record_class()
            record.ParseFromString(raw)
            kind = record.WhichOneof("record_type")
            if kind not in ("stats", "history", "summary", "output"):
                continue
            part = getattr(record, kind)
            stamp = None
            if kind == "stats":
                stamp = part.timestamp.seconds + part.timestamp.nanos / 1e9
            values = {}
            items = part.update if kind == "summary" else getattr(part, "item", [])
            for item in items:
                key = ".".join(item.nested_key) if getattr(item, "nested_key", None) else item.key
                try:
                    value = json.loads(item.value_json)
                except (ValueError, TypeError):
                    continue
                # Capture scalar log metrics, not arbitrary configs or model payloads.
                if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                    values[key] = value
            if kind == "output":
                values = {"line": part.line}
            if stamp is None:
                stamp = values.get("_timestamp")
            captured.append((begin, kind, stamp, values))
        with self.store.connect() as db:
            for begin, kind, stamp, values in captured:
                db.execute(
                    "INSERT OR IGNORE INTO wandb_records VALUES(?,?,?,?,?)",
                    (source_id, begin, kind, stamp, json.dumps(values)),
                )
                if kind == "stats" and stamp is not None:
                    for key, value in values.items():
                        match = GPU_KEY.fullmatch(key)
                        if match:
                            gpu, metric = match.groups()
                            db.execute(
                                """INSERT INTO wandb_gpu_latest VALUES(?,?,?,?,?)
                                ON CONFLICT(source_id,gpu,metric) DO UPDATE SET
                                value=excluded.value,timestamp=excluded.timestamp
                                WHERE excluded.timestamp >= wandb_gpu_latest.timestamp""",
                                (source_id, gpu, metric, value, stamp),
                            )
            db.execute("UPDATE wandb_sources SET offset=?,error=NULL WHERE id=?", (end, source_id))

    def snapshot(self, identifier):
        experiment = self.store.get(identifier)
        with self.store.connect() as db:
            sources = [
                dict(r)
                for r in db.execute(
                    """SELECT s.id,s.label,s.error,
                (SELECT COUNT(*) FROM wandb_records r WHERE r.source_id=s.id) AS records
                FROM wandb_sources s WHERE experiment_id=? ORDER BY label""",
                    (experiment.id,),
                )
            ]
            rows = db.execute(
                """SELECT g.* FROM wandb_gpu_latest g JOIN wandb_sources s
                ON s.id=g.source_id WHERE s.experiment_id=? ORDER BY g.source_id,CAST(g.gpu AS INTEGER)""",
                (experiment.id,),
            ).fetchall()
        grouped = {}
        for row in rows:
            card = grouped.setdefault(
                (row["source_id"], row["gpu"]), {"source_id": row["source_id"], "gpu": row["gpu"]}
            )
            card["utilization" if row["metric"] == "gpu" else row["metric"]] = row["value"]
            card.setdefault("timestamps", {})[row["metric"]] = row["timestamp"]
        # Reconstruct independent series: absent metrics are gaps, not zeroes.
        with self.store.connect() as db:
            history = db.execute(
                """SELECT r.source_id,r.timestamp,r.payload FROM wandb_records r
                JOIN wandb_sources s ON s.id=r.source_id
                WHERE s.experiment_id=? AND r.kind='stats' AND r.timestamp IS NOT NULL
                ORDER BY r.timestamp,r.offset""",
                (experiment.id,),
            )
            for record in history:
                for key, value in json.loads(record["payload"]).items():
                    match = GPU_KEY.fullmatch(key)
                    if not match:
                        continue
                    gpu, metric = match.groups()
                    card = grouped.get((record["source_id"], gpu))
                    if card is None:
                        continue
                    metric = "utilization" if metric == "gpu" else metric
                    card.setdefault("series", {}).setdefault(metric, []).append([record["timestamp"], value])
        for card in grouped.values():
            for metric, points in card.get("series", {}).items():
                card["series"][metric] = compact_series(points)
        enabled = os.environ.get("REXS_CAPTURE_WANDB", "1").lower() not in ("0", "false", "no")
        return {"enabled": enabled, "error": self.error, "sources": sources, "gpus": list(grouped.values())}


def compact_series(points, limit=600):
    """Bound chart payloads while preserving endpoints and bucket extrema."""
    if len(points) <= limit:
        return points
    width = math.ceil((len(points) - 2) / ((limit - 2) // 2))
    selected = [points[0]]
    for start in range(1, len(points) - 1, width):
        bucket = points[start : min(start + width, len(points) - 1)]
        indices = {
            min(range(len(bucket)), key=lambda i: bucket[i][1]),
            max(range(len(bucket)), key=lambda i: bucket[i][1]),
        }
        selected.extend(bucket[i] for i in sorted(indices))
    return selected + [points[-1]]
