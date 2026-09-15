import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from rexs.alerts import validate_alerts
from rexs.errors import ConfigurationError
from rexs.state import StateStore


def setup_store(tmp_path, alerts=None):
    store = StateStore(tmp_path / "state.sqlite3")
    spec = {"tasks": [{"name": "evaluation", "replicas": 2}], "rexs": {}}
    if alerts is not None:
        spec["rexs"]["alerts"] = alerts
    record = store.create_experiment(
        name="example",
        spec_path="spec.yaml",
        spec_text=yaml.safe_dump(spec),
        spec_sha256="",
        script_path="",
        script_sha256="",
        run_root="/tmp/runs",
        warnings=[],
        tasks=spec["tasks"],
    )
    store.add_allocation(
        record.id,
        name="eval",
        ordinal=0,
        spec_text="",
        script_path="",
        script_sha256="",
        run_root="/tmp/runs",
        tasks=[{"name": "evaluation", "rank": i, "local_rank": i} for i in range(2)],
    )
    store.update_allocation(record.id, "eval", "RUNNING", job_id="101")
    return store, record.id


@pytest.mark.parametrize("settings", [None, {}, {"tasks": {"evaluation": {"completed": False, "failed": False}}}])
def test_alerts_disabled_by_default(tmp_path, settings):
    store, eid = setup_store(tmp_path, settings)
    store.update_allocation(eid, "eval", "FAILED")
    store.update_status(eid, "FAILED")
    assert store.alerts() == []


def test_task_completion_is_durable_passive_and_deduplicated(tmp_path):
    store, eid = setup_store(tmp_path, {"tasks": {"evaluation": {"completed": True}}})
    for _ in range(3):
        store.update_allocation(eid, "eval", "COMPLETED", exit_code="0:0")
    store.update_status(eid, "COMPLETED")
    rows = StateStore(store.path).alerts()
    assert len(rows) == 1
    alert = rows[0]
    assert (alert["kind"], alert["reason"], alert["task_name"], alert["job_id"]) == (
        "completed",
        "COMPLETED",
        "evaluation",
        "101",
    )
    assert alert["context"]["replica_ranks"] == [0, 1]  # One packed allocation alert, not one per replica.
    assert store.alerts() == rows  # Reading never consumes the alert.
    assert store.alerts(after=alert["id"]) == []
    assert store.get(eid).status == "COMPLETED"


def test_failure_filter_and_new_job_attempt(tmp_path):
    store, eid = setup_store(
        tmp_path, {"tasks": {"evaluation": {"failed": True, "failure_reasons": ["OUT_OF_MEMORY", "PREEMPTED"]}}}
    )
    for status in ["PENDING", "RUNNING", "UNKNOWN", "COMPLETED", "CANCELLED", "FAILED"]:
        store.update_allocation(eid, "eval", status)
    assert not store.alerts()
    store.update_allocation(eid, "eval", "OUT_OF_MEMORY", exit_code="1:0")
    store.update_allocation(eid, "eval", "OUT_OF_MEMORY", exit_code="1:0")
    store.update_allocation(eid, "eval", "SUBMITTED", job_id="102")
    store.update_allocation(eid, "eval", "PREEMPTED")
    rows = store.alerts()
    assert [r["reason"] for r in rows] == ["OUT_OF_MEMORY", "PREEMPTED"]
    assert store.alerts(after=rows[0]["id"]) == [rows[1]]
    assert store.alerts(limit=1) == rows[:1]


def test_experiment_alert_only_when_selected(tmp_path):
    store, eid = setup_store(tmp_path, {"experiment": {"failed": True}})
    store.update_allocation(eid, "eval", "FAILED")
    assert not store.alerts()
    store.update_status(eid, "FAILED", exit_code="1:0")
    store.update_status(eid, "FAILED", exit_code="1:0")
    (alert,) = store.alerts(experiment_id=eid)
    assert alert["task_name"] == "" and alert["allocation_name"] == ""
    assert alert["context"]["exit_code"] == "1:0"
    with store.connect() as db:
        assert json.loads(db.execute("SELECT context_json FROM alerts").fetchone()[0])["schema_version"] == 1


def test_concurrent_pollers_insert_only_one_alert(tmp_path):
    store, eid = setup_store(tmp_path, {"tasks": {"evaluation": {"failed": True}}})

    def update(_):
        store.update_allocation(eid, "eval", "PREEMPTED")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update, range(8)))
    assert len(store.alerts()) == 1


@pytest.mark.parametrize(
    "settings",
    [
        {"unknown": True},
        {"tasks": {"typo": {"completed": True}}},
        {"experiment": {"completed": "yes"}},
        {"experiment": {"failure_reasons": ["FAILED"]}},
        {"tasks": {"evaluation": {"failed": True, "failure_reasons": ["RUNNING"]}}},
    ],
)
def test_invalid_flags(settings):
    with pytest.raises(ConfigurationError):
        validate_alerts(settings, ["evaluation"])
