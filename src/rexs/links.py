"""Discover explicit W&B run links without contacting W&B."""
import re
from pathlib import Path

RUN_URL = re.compile(r'https://(?:www\.|app\.)?wandb\.ai/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/runs/[A-Za-z0-9_-]+')


def _texts(spec_text, tasks):
    texts = [spec_text or '']
    for task in tasks:
        texts.append(str(task.get('content') or ''))
        # W&B prints its run URL at startup; retain it after it leaves the log tail.
        path = task.get('log_path')
        if path:
            try:
                with Path(path).open('rb') as stream:
                    texts.append(stream.read(1024 * 1024).decode('utf-8', errors='replace'))
            except OSError:
                pass
    return texts


def wandb_links(spec_text, tasks):
    return sorted({match.group() for text in _texts(spec_text, tasks) for match in RUN_URL.finditer(text)})


def wandb_offline(tasks):
    return any("W&B syncing is set to `offline`" in text for text in _texts('', tasks))
