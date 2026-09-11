import json
import sys

from rexs.beaker import main
from rexs.cli import Rexs


def test_native_service_create_flags_and_receipt(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("REXS_DRY_RUN", raising=False)
    monkeypatch.setenv("REXS_PROFILE", "delta.yaml")
    monkeypatch.setattr(Rexs, "submit", lambda self, spec, **kw: (
        calls.append((spec, kw)) or {"id": "rex-owned-service", "job_id": "123"}))
    monkeypatch.setattr(sys, "argv", ["beaker", "--format", "json", "experiment",
                                     "create", "-n", "services", "-w", "ai2/test", "spec.json"])
    main()
    assert json.loads(capsys.readouterr().out)["id"] == "rex-owned-service"
    assert calls == [("spec.json", {"profile": "delta.yaml", "name": "services"})]


def test_cleanup_cancels_exact_rexs_experiment_without_profile(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("REXS_PROFILE", raising=False)
    monkeypatch.delenv("REXS_DRY_RUN", raising=False)
    monkeypatch.setattr(Rexs, "cancel", lambda self, identifier: (
        calls.append(identifier) or {"id": identifier, "status": "CANCELLED"}))
    monkeypatch.setattr(sys, "argv", ["beaker", "experiment", "stop", "rex-owned-service"])
    main()
    assert calls == ["rex-owned-service"]
    assert json.loads(capsys.readouterr().out)["status"] == "CANCELLED"


def test_cleanup_dry_run_does_not_cancel(monkeypatch, capsys):
    monkeypatch.setenv("REXS_DRY_RUN", "1")
    monkeypatch.setattr(Rexs, "cancel", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cancelled")))
    monkeypatch.setattr(sys, "argv", ["beaker", "experiment", "stop", "rex-owned-service"])
    main()
    assert json.loads(capsys.readouterr().out)["cancelled"] is False
