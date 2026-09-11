from __future__ import annotations

import json
import struct
import zlib

import pytest

from rexs.metrics import BLOCK, HEADER, MetricsCollector, records
from rexs.state import StateStore


def chunk(payload, kind=1):
    return (
        struct.pack("<IHB", zlib.crc32(payload, zlib.crc32(bytes([kind]))) & 0xFFFFFFFF, len(payload), kind) + payload
    )


def fixture(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    run = store.create_experiment(
        name="metrics",
        spec_path="spec",
        spec_text="tasks: []",
        spec_sha256="a",
        script_path="script",
        script_sha256="b",
        run_root=str(tmp_path / "runs"),
        warnings=[],
        tasks=[],
    )
    store.record_submission(run.id, "123", "submitted")
    source = tmp_path / "runs/123/results/train/0/wandb/offline-run/run-a.wandb"
    source.parent.mkdir(parents=True)
    return store, run, source


def stats(timestamp, **values):
    pb = pytest.importorskip("wandb.proto.wandb_internal_pb2")
    record = pb.Record()
    record.stats.timestamp.seconds = timestamp
    for key, value in values.items():
        record.stats.item.add(key=key, value_json=json.dumps(value))
    return record.SerializeToString()


def test_framing_handles_fragmentation_and_incomplete_tail(tmp_path):
    path = tmp_path / "run.wandb"
    payload = b"x" * (BLOCK + 11)
    first = payload[: BLOCK - 14]
    complete = HEADER + chunk(first, 2) + chunk(payload[len(first) :], 4)
    path.write_bytes(complete[:-3])
    assert list(records(path)) == []
    path.write_bytes(complete)
    assert list(records(path)) == [(7, len(complete), payload)]
    path.write_bytes(complete + chunk(b"next")[:9])
    assert len(list(records(path))) == 1


def test_zero_length_first_fragment_and_padding(tmp_path):
    path = tmp_path / "run.wandb"
    first = chunk(b"a" * (BLOCK - 21))
    content = HEADER + first + chunk(b"", 2) + chunk(b"end", 4)
    path.write_bytes(content)
    assert [r[2] for r in records(path)] == [b"a" * (BLOCK - 21), b"end"]
    path.write_bytes(HEADER + chunk(b"a" * (BLOCK - 17)) + b"\0" * 3 + chunk(b"last"))
    assert [r[2] for r in records(path)][-1] == b"last"


def test_bad_checksum_is_rejected(tmp_path):
    path = tmp_path / "run.wandb"
    path.write_bytes(HEADER + chunk(b"good")[:-1] + b"x")
    with pytest.raises(ValueError, match="checksum"):
        list(records(path))


def test_ingest_resume_idempotency_and_persistence(tmp_path):
    store, run, path = fixture(tmp_path)
    first = stats(
        100, **{"gpu.0.gpu": 75, "gpu.0.memoryAllocated": 25, "gpu.0.memoryAllocatedBytes": 1024**3, "gpu.0.memory": 99}
    )
    second = stats(110, **{"gpu.0.gpu": 50})
    path.write_bytes(HEADER + chunk(first) + chunk(second)[:-4])
    collector = MetricsCollector(store)
    collector.collect(run.id)
    card = collector.snapshot(run.id)["gpus"][0]
    assert card["gpu"] == "0"
    assert card["utilization"] == 75
    assert card["memoryAllocated"] == 25  # memory bandwidth is not allocated memory
    assert card["memoryAllocatedBytes"] == 1024**3
    path.write_bytes(HEADER + chunk(first) + chunk(second))
    collector = MetricsCollector(store)
    collector.collect(run.id)
    collector = MetricsCollector(store)
    collector.collect(run.id)
    result = collector.snapshot(run.id)
    assert result["sources"][0]["records"] == 2
    assert result["gpus"][0]["utilization"] == 50
    assert result["gpus"][0]["memoryAllocated"] == 25
    assert result["gpus"][0]["series"]["utilization"] == [[100, 75], [110, 50]]
    assert result["gpus"][0]["series"]["memoryAllocated"] == [[100, 25]]
    path.unlink()
    assert MetricsCollector(StateStore(store.path)).snapshot(run.id)["gpus"] == result["gpus"]


def test_missing_disabled_and_bad_source_are_nonfatal(tmp_path, monkeypatch):
    store, run, path = fixture(tmp_path)
    collector = MetricsCollector(store)
    collector.collect(run.id)
    assert collector.snapshot(run.id)["gpus"] == []
    path.write_bytes(b"not a wandb file" * 10)
    monkeypatch.setenv("REXS_CAPTURE_WANDB", "0")
    collector = MetricsCollector(store)
    collector.collect(run.id)
    assert collector.snapshot(run.id)["enabled"] is False
    monkeypatch.delenv("REXS_CAPTURE_WANDB")
    collector.collect(run.id)
    result = collector.snapshot(run.id)
    assert result["gpus"] == []
    assert "header" in result["sources"][0]["error"]


def test_replaced_file_and_separate_loggers_do_not_merge_devices(tmp_path):
    store, run, path = fixture(tmp_path)
    path.write_bytes(HEADER + chunk(stats(100, **{"gpu.0.gpu": 1})))
    second = path.with_name("run-b.wandb")
    second.write_bytes(HEADER + chunk(stats(100, **{"gpu.0.gpu": 90})))
    collector = MetricsCollector(store)
    collector.collect(run.id)
    assert sorted(g["utilization"] for g in collector.snapshot(run.id)["gpus"]) == [1, 90]
    path.write_bytes(HEADER + chunk(stats(200, **{"gpu.0.gpu": 50})))
    collector = MetricsCollector(store)
    collector.collect(run.id)
    assert sorted(g["utilization"] for g in collector.snapshot(run.id)["gpus"]) == [1, 50, 90]


def test_history_and_output_are_saved(tmp_path):
    pb = pytest.importorskip("wandb.proto.wandb_internal_pb2")
    store, run, path = fixture(tmp_path)
    history = pb.Record()
    history.history.item.add(key="loss", value_json="0.5")
    history.history.item.add(key="_timestamp", value_json="100")
    output = pb.Record()
    output.output.line = "finished step one"
    path.write_bytes(HEADER + chunk(history.SerializeToString()) + chunk(output.SerializeToString()))
    MetricsCollector(store).collect(run.id)
    with store.connect() as db:
        saved = db.execute("SELECT kind,payload FROM wandb_records ORDER BY offset").fetchall()
    assert [(r["kind"], json.loads(r["payload"])) for r in saved] == [
        ("history", {"loss": 0.5, "_timestamp": 100}),
        ("output", {"line": "finished step one"}),
    ]


def test_chart_compaction_preserves_spikes_and_time_order():
    from rexs.metrics import compact_series

    points = [[i, 0] for i in range(10000)]
    points[321][1] = 100
    points[654][1] = -1
    compact = compact_series(points)
    assert len(compact) <= 600
    assert compact[0] == points[0] and compact[-1] == points[-1]
    assert points[321] in compact and points[654] in compact
    assert compact == sorted(compact)


def test_duplicate_output_copy_shows_once_but_replicas_stay_distinct(tmp_path):
    store, run, source = fixture(tmp_path)
    source.write_bytes(HEADER + chunk(stats(10, **{'gpu.0.gpu': 20})))
    copy = source.parent.parent.parent / 'run_default/wandb/offline-run/run-a.wandb'
    copy.parent.mkdir(parents=True)
    copy.write_bytes(source.read_bytes() + chunk(stats(20, **{'gpu.0.gpu': 40})))
    replica = source.parents[3] / '1/wandb/offline-run/run-a.wandb'
    replica.parent.mkdir(parents=True)
    replica.write_bytes(source.read_bytes())
    collector = MetricsCollector(store)
    collector.collect(run.id)
    result = collector.snapshot(run.id)
    assert len(result['sources']) == 2
    assert sorted(g['utilization'] for g in result['gpus']) == [20, 40]
