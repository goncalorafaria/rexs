"""Persistent Delta services with job-scoped discovery and local container storage."""
import json
import os
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import redis


def configure_podman(scratch):
    os.environ.update(
        XDG_RUNTIME_DIR=str(scratch / 'run'),
        XDG_DATA_HOME=str(scratch / 'data'),
        XDG_CONFIG_HOME=str(scratch / 'config'),
        CONTAINERS_STORAGE_CONF=str(scratch / 'storage.conf'),
        CONTAINERS_CONF=str(scratch / 'containers.conf'),
    )
    (scratch / 'run').mkdir(mode=0o700)
    (scratch / 'bin').mkdir()
    wrapper = scratch / 'bin/podman'
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/podman --storage-opt=vfs.ignore_chown_errors=true "$@"\n')
    wrapper.chmod(0o700)
    os.environ['PATH'] = f'{scratch}/bin:' + os.environ['PATH']
    (scratch / 'storage.conf').write_text(
        f'[storage]\ndriver="vfs"\nrunroot="{scratch}/run/storage"\ngraphroot="{scratch}/data/storage"\n'
        '[storage.options.vfs]\nignore_chown_errors="true"\n'
    )
    (scratch / 'containers.conf').write_text(
        '[containers]\ncgroups="disabled"\nvolumes=["/dev/pts:/dev/pts"]\n'
        '[engine]\ncgroup_manager="cgroupfs"\nevents_logger="file"\n'
        f'events_logfile_path="{scratch}/events.log"\ntmp_dir="{scratch}/libpod"\n'
    )


def wait_endpoint(root, name):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            uri = json.loads((root / f'{name}.0.json').read_text())['uri']
            if name == 'redis':
                client = redis.Redis.from_url(uri, socket_connect_timeout=3, socket_timeout=3)
                try:
                    client.ping()
                finally:
                    client.close()
            else:
                with urllib.request.urlopen(uri + '/health', timeout=3) as response:
                    response.read()
            return uri
        except (OSError, ValueError, KeyError, redis.RedisError):
            time.sleep(2)
    raise TimeoutError(f'{name} not ready after 600 seconds')


def main():
    role = sys.argv[1]
    rank = int(os.environ.get('BEAKER_REPLICA_RANK', '0'))
    job = os.environ['BEAKER_JOB_ID']
    host = os.environ.get('BEAKER_NODE_HOSTNAME', socket.getfqdn())
    root = Path('/deployment/runs') / job / 'endpoints'
    root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f'rexs-{job}-{role}-{rank}-'))
    # The cooperative port supervisor holds a node-local lock for service life.
    if len(sys.argv) < 3:
        # Dependencies must be ready before acquiring the host port lock.
        # Otherwise a waiting worker prevents Redis/gateway from binding.
        if role != 'redis':
            wait_endpoint(root, 'redis')
        if role == 'podman':
            wait_endpoint(root, 'gateway')
        command = ['python', '/deployment/service.py', role, 'serve']
        preferred = 20000 + (int(job) % 1000) * 20 + {'redis': 0, 'gateway': 1, 'mirror': 2, 'podman': 4}[role] + rank
        args = ['python', '-m', 'literegistry.coop.ports', 'run',
                '--assignment', f'PORT={preferred}', '--identity', f'{job}:{role}:{rank}',
                '--host-id', host, '--lock_dir=/tmp/literegistry-port-locks',
                '--command-json', json.dumps(command)]
        os.execvp(args[0], args)
    port = int(os.environ['PORT'])
    registry = None if role == 'redis' else wait_endpoint(root, 'redis')
    common = [f'--registry={registry}', '--host=0.0.0.0', f'--port={port}']
    advertise = [f'--advertise_host={host}', f'--advertise_port={port}']
    if role == 'redis':
        data = root.parent / 'redis-data'
        data.mkdir(exist_ok=True)
        args = ['redis-server', '--bind', '0.0.0.0', '--protected-mode', 'no',
                '--port', str(port), '--dir', str(data), '--appendonly', 'yes', '--save', '']
    elif role == 'gateway':
        args = ['literegistry', 'gateway', *common, '--workers=4', '--timeout=300']
    elif role == 'mirror':
        args = ['python', '-m', 'literegistry.services.docker_mirror_server', *common, *advertise,
                f'--instance_id=mirror-{rank}', '--allow_non_loopback=True',
                f'--distribution_config={scratch}/distribution.yml', f'--storage_root={scratch}/mirror']
    elif role == 'podman':
        gateway = wait_endpoint(root, 'gateway')
        configure_podman(scratch)
        args = ['literegistry', 'podman', *common, *advertise, '--allow_non_loopback=True',
                f'--instance_id=podman-{rank}', f'--registry_mirror={gateway}', '--network=none',
                '--max_sessions=8', '--session_memory=4g', '--session_pids_limit=2048',
                '--session_idle_timeout=600', '--janitor_interval=30']
    else:
        raise ValueError(role)
    uri = f'{"redis" if role == "redis" else "http"}://{host}:{port}'
    record = {'role': role, 'rank': rank, 'job': job, 'uri': uri, 'scratch': str(scratch)}
    target = root / f'{role}.{rank}.json'
    temporary = target.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(target)
    print(json.dumps(record), flush=True)
    os.execvp(args[0], args)


if __name__ == '__main__':
    main()
