"""Persistent, per-account credentials for the local dashboard."""
from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path

USERNAME = 'rexs'


def dashboard_password(state_dir: Path) -> str:
    """Use an environment override or atomically create a private default password."""
    override = os.environ.get('REXS_SERVER_PASSWORD')
    if override is not None:
        if not override.strip():
            raise ValueError('REXS_SERVER_PASSWORD must not be empty')
        return override
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / 'server.password'
    # Publish only a fully written file, including when two servers start together.
    if not path.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=state_dir, delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write('rexs-' + secrets.token_urlsafe(18) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('dashboard password must be a regular file owned by this user')
        os.fchmod(stream.fileno(), 0o600)
        password = stream.read().rstrip('\r\n')
    if not password.strip():
        raise ValueError(f'dashboard password file is empty: {path}')
    return password
