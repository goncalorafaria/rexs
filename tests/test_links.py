from rexs.links import wandb_links


def test_startup_link_survives_scrolling_and_deduplicates(tmp_path):
    url = 'https://wandb.ai/team/project/runs/abc123'
    log = tmp_path / 'worker.log'
    log.write_text(f'wandb: View run at \x1b[34m{url}\x1b[0m\n' + 'step finished\n' * 500)
    assert wandb_links(url, [{'log_path': str(log), 'content': 'recent lines'}]) == [url]


def test_missing_logs_and_no_run_url_hide_links(tmp_path):
    assert wandb_links('https://wandb.ai/team/project', [{'log_path': str(tmp_path/'missing')}]) == []
    assert wandb_links('https://wandb.ai.evil.test/team/project/runs/abc', []) == []


def test_multiple_runs_from_live_logs():
    urls = ['https://wandb.ai/team/project/runs/a', 'https://wandb.ai/team/project/runs/b']
    assert wandb_links('', [{'content': '\n'.join(urls)}]) == urls


def test_captured_links_survive_log_deletion_and_database_reopen(tmp_path):
    from rexs.state import StateStore

    database = tmp_path / 'state.sqlite3'
    store = StateStore(database)
    experiment = store.create_experiment(
        name='finished-run', spec_path='spec.yaml', spec_sha256='spec',
        spec_text='', script_path='run.sbatch', script_sha256='script',
        run_root=str(tmp_path), warnings=(), tasks=(),
    )
    url = 'https://wandb.ai/team/project/runs/retained'
    log = tmp_path / 'run.log'
    log.write_text(f'wandb: View run at {url}\n')
    tasks = [{'log_path': str(log)}]
    assert store.remember_wandb_links(experiment.id, wandb_links('', tasks)) == [url]
    log.unlink()
    reopened = StateStore(database)
    assert reopened.remember_wandb_links(experiment.id, wandb_links('', tasks)) == [url]
    assert reopened.remember_wandb_links(experiment.id, [url, url]) == [url]
