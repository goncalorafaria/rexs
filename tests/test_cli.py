import json

from rexs.cli import Rexs, main


def test_status_prints_json_instead_of_fire_object_help(monkeypatch, capsys):
    monkeypatch.setattr(Rexs, 'status', lambda self, identifier: {
        'job_id': str(identifier), 'status': 'RUNNING', 'warnings': [],
    })
    main(['status', '12345'])
    result = json.loads(capsys.readouterr().out)
    assert result == {'job_id': '12345', 'status': 'RUNNING', 'warnings': []}


def test_beaker_interceptor_renders_without_submitting(monkeypatch, tmp_path, capsys):
    import sys

    from rexs.beaker import main as intercept

    spec = tmp_path / 'experiment.json'
    spec.write_text(json.dumps({'version': 'v2', 'tasks': [
        {'name': 'sft', 'image': {'beaker': 'runtime'}, 'command': ['true']}
    ]}))
    profile = tmp_path / 'profile.yaml'
    profile.write_text('images:\n  runtime: /images/runtime.sif\n')
    monkeypatch.setenv('REXS_PROFILE', str(profile))
    monkeypatch.setenv('REXS_DRY_RUN', '1')
    monkeypatch.setattr(sys, 'argv', ['beaker', 'experiment', 'create', '--workspace', 'ai2/test', str(spec)])
    intercept()
    result = json.loads(capsys.readouterr().out)
    assert result['submitted'] is False
    assert '/images/runtime.sif' in spec.with_suffix('.sbatch').read_text()
