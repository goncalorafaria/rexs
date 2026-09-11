"""Discover task container images from the JSONL sources in a Prime-RL config."""
import json
from pathlib import Path
import tomllib

IMAGE_KEYS = ('original_image', 'image', 'container_image', 'published_container_image', 'resolved_container_image')


def row_image(row):
    # Match PrimeBeaker's task_image lookup order for a dataset row.
    candidates = [row]
    for key in ('info', 'source', 'misc', 'raw'):
        nested = row.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
            for child in ('misc', 'raw'):
                if isinstance(nested.get(child), dict):
                    candidates.append(nested[child])
    for candidate in candidates:
        for key in IMAGE_KEYS:
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                image = value.strip()
                if '/' not in image:
                    image = 'docker.io/library/' + image
                elif image.split('/')[0] != 'localhost' and not any(c in image.split('/')[0] for c in '.:'):
                    image = 'docker.io/' + image
                if not image.startswith(('docker.io/', 'index.docker.io/', 'registry-1.docker.io/')):
                    raise ValueError(f'Docker Hub mirror cannot warm {image}')
                image = 'docker.io/' + image.split('/', 1)[1]
                repo = image.rsplit('/', 1)[-1]
                return image if ':' in repo or '@' in image else image + ':latest'
    raise ValueError('Missing task container image')


def discover(config_path, working_dir):
    config = tomllib.loads(Path(config_path).read_text())
    images = set()
    sources = []
    for phase in ('train', 'eval'):
        for source in config['orchestrator'].get(phase, {}).get('source', []):
            raw = source.get('legacy', {}).get('args', {}).get('dataset')
            if not raw:
                raise ValueError(f'{phase} source has no JSONL dataset path')
            path = Path(raw)
            if not path.is_absolute():
                path = Path(working_dir) / path
            count = 0
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    images.add(row_image(row))
                except (ValueError, AttributeError) as exc:
                    raise ValueError(f'{path}:{number}: {exc}') from exc
                count += 1
            sources.append({'phase': phase, 'path': str(path), 'rows': count})
    if not images:
        raise ValueError('No task images found in configured JSONL datasets')
    return sorted(images), sources
