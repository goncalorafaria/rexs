"""Add independently scheduled replicas to an existing unified experiment."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import yaml

from rexs.allocations import plan, resolve_jobs
from rexs.compiler import compile_experiment
from rexs.controller import Controller, parse_job_id
from rexs.errors import ConfigurationError, RexsError
from rexs.state import TERMINAL_STATES


def extend(identifier, task, replicas=1, *, db=None, strict=False, profile=None, overrides=None):
    """Add replicas (not a target count), keeping the original experiment ID."""
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
        raise ConfigurationError('replicas must be a positive integer')
    controller = Controller(db)
    store = controller.store
    parent = store.get(identifier)
    if parent.backend != "slurm":
        raise ConfigurationError("Dynamic replica extension is currently supported only on Slurm")
    with controller._experiment_lock(parent.id):
        parent = store.get(parent.id)
        if not parent.allocations or parent.status in TERMINAL_STATES | {'GENERATED'}:
            raise ConfigurationError('Only active unified experiments can be extended')
        completion = next((a for a in parent.allocations if a['completion']), None)
        if completion and completion['status'] in TERMINAL_STATES:
            raise ConfigurationError('Completion task has already exited')
        spec = yaml.safe_load(parent.spec_text)
        group = next((g for g in spec['rexs']['allocations'] if g['tasks'] == [task]), None)
        if not group or not group.get('independent_replicas') or task == spec['rexs'].get('completion_task'):
            raise ConfigurationError('Extension requires a non-completion task with independent_replicas: true')
        existing = [t for a in parent.allocations for t in a['tasks'] if t['name'] == task]
        first = max(t['rank'] for t in existing) + 1
        count = first + replicas
        template = copy.deepcopy(next(t for t in spec['tasks'] if t['name'] == task))
        group = copy.deepcopy(group)
        if profile:
            group['profile'] = str(Path(profile).resolve())
        if overrides:
            override = yaml.safe_load(Path(overrides).read_text())
            if not isinstance(override, dict) or set(override) - {'resources', 'envVars'}:
                raise ConfigurationError('Extension overrides support only resources and envVars')
            if 'resources' in override:
                template.setdefault('resources', {}).update(override['resources'])
            if 'envVars' in override:
                values = {e['name']: e for e in template.get('envVars', [])}
                values.update({e['name']: e for e in override['envVars']})
                template['envVars'] = list(values.values())
        template['replicas'] = 1
        small = {'version': spec['version'], 'tasks': [template], 'rexs': {'allocations': [group]}}
        policies = spec['rexs'].get('failure_policies', {})
        if task in policies:small['rexs']['failure_policies'] = {task: policies[task]}
        allocation = plan(small, parent.spec_path)[0]
        jobs = {a['name']: a['job_id'] for a in parent.allocations if a['job_id']}
        directory = Path((completion or parent.allocations[0])['script_path']).parent
        owned = directory / 'owned-jobs.txt'
        compiled = []
        names = {a['name'] for a in parent.allocations}
        for rank in range(first, count):
            name = f"{group['name']}-{rank}"
            if name in names:raise ConfigurationError(f'Allocation already exists: {name}')
            resolved = resolve_jobs(copy.deepcopy(allocation.spec), jobs)
            env = resolved['tasks'][0].setdefault('envVars', [])
            keys = {'BEAKER_REPLICA_RANK': str(rank), 'BEAKER_REPLICA_COUNT': str(count),
                    'REXS_EXPERIMENT_ID': parent.id, 'REXS_ALLOCATION_NAME': name}
            env[:] = [e for e in env if e['name'] not in keys]
            env.extend({'name': k, 'value': v} for k, v in keys.items())
            result = compile_experiment(resolved, profile=allocation.profile, job_name=f'{parent.name}-{name}')
            if strict and result.warnings:raise ConfigurationError('\n'.join(result.warnings))
            subprocess.run(['bash', '-n'], input=result.script, text=True, capture_output=True, check=True)
            compiled.append((name, rank, resolved, result))
        # Persist intent before submitting; a failed extension never cancels the original jobs.
        ordinal = max(a['ordinal'] for a in parent.allocations) + 1
        for i, (name, rank, resolved, result) in enumerate(compiled):
            target = directory / f'{name}.sbatch'
            target.write_text(result.script);target.chmod(0o700)
            store.add_allocation(parent.id, name=name, ordinal=ordinal+i,
                spec_text=yaml.safe_dump(resolved), script_path=str(target.resolve()),
                script_sha256=hashlib.sha256(result.script.encode()).hexdigest(),
                run_root=allocation.profile.run_root, tasks=[{'name':task,'rank':rank,'local_rank':0}])
        with store.connect() as connection:
            connection.executemany('INSERT INTO task_replicas VALUES (?,?,?)', [(parent.id,task,rank) for _,rank,_,_ in compiled])
        submitted = {}
        try:
            for name, _, _, _ in compiled:
                result = subprocess.run(['sbatch', str(directory / f'{name}.sbatch')], check=True, text=True, capture_output=True)
                submitted[name] = parse_job_id(result.stdout)
                store.update_allocation(parent.id, name, 'SUBMITTED', job_id=submitted[name], detail='Replica extension')
                with owned.open('a') as f:f.write(submitted[name]+'\n')
            # Catch an evaluator exiting while sbatch was adding jobs, after its trap read the ownership file.
            if completion:
                status, _, _ = controller._slurm_status(completion['job_id'])
                if status in TERMINAL_STATES:raise RexsError('Completion task exited during extension')
        except BaseException as error:
            cleanup_error = None
            try:
                if submitted:subprocess.run(['scancel', *submitted.values()], check=True, text=True, capture_output=True)
            except (OSError, subprocess.SubprocessError) as exc:cleanup_error = str(exc)
            for name, _, _, _ in compiled:
                store.update_allocation(parent.id, name, 'UNKNOWN' if name in submitted and cleanup_error else 'CANCELLED', detail=f'Extension rollback: {error}; cleanup: {cleanup_error}')
            if cleanup_error is None:
                with store.connect() as connection:
                    connection.executemany('DELETE FROM allocations WHERE experiment_id=? AND name=?', [(parent.id,name) for name,_,_,_ in compiled])
                    connection.executemany('DELETE FROM task_replicas WHERE experiment_id=? AND name=? AND replica_rank=?', [(parent.id,task,rank) for _,rank,_,_ in compiled])
            raise RexsError(f'Extension failed; original jobs preserved: {error}; cleanup: {cleanup_error}') from error
        next(t for t in spec['tasks'] if t['name']==task)['replicas'] = count
        text = yaml.safe_dump(spec, sort_keys=False)
        with store.connect() as connection:
            connection.execute('UPDATE experiments SET spec_text=?,spec_sha256=? WHERE id=?', (text,hashlib.sha256(text.encode()).hexdigest(),parent.id))
            store._event(connection,parent.id,parent.status,parent.status,f'Extended {task} by {replicas} replicas: {json.dumps(submitted)}')
        return store.get(parent.id).as_dict()
