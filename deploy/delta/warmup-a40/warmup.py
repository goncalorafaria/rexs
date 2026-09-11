"""Warm the actual mirror, verify a Podman session, keep the service allocation alive."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.request
import subprocess
import sys
from dataset_images import discover

root = Path('/deployment/runs') / os.environ['BEAKER_JOB_ID'] / 'endpoints'
def endpoint(name, path, rank=0):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            uri = json.loads((root / f'{name}.{rank}.json').read_text())['uri']
            with urllib.request.urlopen(uri + path, timeout=3):
                return uri
        except (OSError, ValueError):
            time.sleep(2)
    raise TimeoutError(f'{name} did not become ready')

def post(uri, path, payload):
    request = urllib.request.Request(uri + path, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)

mirrors = [endpoint('mirror', '/v2/', rank) for rank in range(int(os.environ['REXS_MIRROR_REPLICAS']))]
images, sources = discover(os.environ.get('REXS_WARMUP_CONFIG', '/training/deploy/podman-colocated/rl.toml'), '/training')
Path('/results/images.txt').write_text('\n'.join(images) + '\n')
Path('/results/datasets.json').write_text(json.dumps(sources, indent=2) + '\n')
print(json.dumps({'datasets': sources, 'unique_images': images}), flush=True)
# Separate processes prevent LiteRegistry's global blob-dedup cache from
# skipping layers on the second and subsequent mirror servers.
def warm_mirror(item):
    rank, mirror = item
    log_path = Path(f'/results/mirror-{rank}-warmup.log')
    print(f'Warming mirror {rank}: {mirror}; log={log_path}', flush=True)
    with log_path.open('w') as log:
        subprocess.run([sys.executable, '-u', '-m', 'literegistry.services.docker_mirror_warmup',
                        f'--mirror={mirror}', '--images_file=/results/images.txt',
                        '--workers=8', '--platform=linux/amd64', '--verbose=True'],
                       check=True, timeout=3000, stdout=log, stderr=subprocess.STDOUT)
    print(f'Mirror {rank} warmup passed: {len(images)} images', flush=True)

with ThreadPoolExecutor(max_workers=len(mirrors)) as pool:
    list(pool.map(warm_mirror, enumerate(mirrors)))
podman = endpoint('podman', '/health')
session = post(podman, '/handshake', {'image':images[0], 'client_id':'mirror-warmup-check'})
cid = session['container_id']
try:
    result = post(podman, '/podman', {'container_id':cid, 'command':'printf mirror-ready', 'timeout':10})
    assert result['success'] and result['stdout'] == 'mirror-ready', result
finally:
    closed = post(podman, '/close', {'container_id':cid})
    assert closed.get('removed'), closed
report = {'ready':True, 'mirrors':mirrors, 'podman':podman, 'gateway':endpoint('gateway','/health'), 'redis':json.loads((root/'redis.0.json').read_text())['uri'], 'caches':[json.loads((root/f'mirror.{rank}.json').read_text())['scratch'] + '/mirror' for rank in range(len(mirrors))], 'images':images, 'datasets':sources}
Path('/results/ready.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2),flush=True)
while True:
    time.sleep(30)
