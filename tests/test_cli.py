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


def test_log_paths_groups_filters_and_marks_missing_files(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from rexs import cli

    existing = tmp_path / 'inference.0.log'
    existing.write_text('log contents must not appear')
    missing = tmp_path / 'inference.1.log'
    records = [
        SimpleNamespace(name='trainer', replica_rank=0, job_id='123', log_path=None),
        SimpleNamespace(name='inference', replica_rank=1, job_id='125', log_path=str(missing)),
        SimpleNamespace(name='inference', replica_rank=0, job_id='124', log_path=str(existing)),
    ]
    # Only metadata access is available: no refresh or log-reading operation.
    monkeypatch.setattr(cli, 'Controller', lambda db: SimpleNamespace(
        store=SimpleNamespace(tasks=lambda identifier: records,
                              get=lambda identifier: SimpleNamespace(backend='slurm'))))
    main(['logs', 'run', '--paths'])
    output = capsys.readouterr().out
    assert output.index('inference:') < output.index('trainer:')
    assert output.index('replica 0 | job 124') < output.index('replica 1 | job 125')
    assert str(existing) in output
    assert 'job 125 [not created yet]' in output
    assert 'log contents' not in output
    main(['logs', 'run', '--paths', '--task=inference', '--replica=0'])
    output = capsys.readouterr().out
    assert str(existing) in output and str(missing) not in output and 'trainer:' not in output
    assert Rexs().log_paths('run', task='absent') == 'No matching task replicas.'
