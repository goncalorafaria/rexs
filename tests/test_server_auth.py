import base64
import json
import stat
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from rexs.auth import dashboard_password
from rexs.controller import Controller
from rexs.server import RexsServer
from rexs.state import StateStore


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.delenv('REXS_SERVER_PASSWORD', raising=False)
    store = StateStore(tmp_path / 'state.sqlite3')
    controller = Controller(store.path)
    calls = []
    monkeypatch.setattr(controller, 'refresh', lambda: calls.append('refresh') or [])
    monkeypatch.setattr(controller, 'cancel', lambda identifier: calls.append(identifier))
    server = RexsServer(('127.0.0.1', 0), controller, poll_interval=60)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = base64.b64encode(server.expected_credentials).decode()
    yield f'http://127.0.0.1:{server.server_port}', {'Authorization': 'Basic ' + token}, calls
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize('path,method', [('/', 'GET'), ('/experiment/private', 'GET'),
    ('/api/experiments', 'GET'), ('/api/experiments/private', 'GET'),
    ('/assets/rexs-logo.png', 'GET'), ('/api/refresh', 'POST'),
    ('/api/experiments/private/cancel', 'POST')])
def test_anonymous_requests_are_challenged_before_data_or_actions(dashboard, path, method):
    base, _, calls = dashboard
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + path, method=method), timeout=5)
    assert error.value.code == 401
    assert error.value.headers['WWW-Authenticate'].startswith('Basic realm="REXS"')
    assert error.value.headers['Cache-Control'] == 'no-store'
    assert calls == []


@pytest.mark.parametrize('authorization', ['Basic not-base64!', 'Bearer anything',
    'Basic ' + base64.b64encode(b'rexs:wrong').decode(),
    'Basic ' + base64.b64encode(b'wrong:user').decode()])
def test_invalid_credentials_do_not_authorize(dashboard, authorization):
    base, _, _ = dashboard
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/', headers={'Authorization': authorization}), timeout=5)
    assert error.value.code == 401


def test_correct_login_can_read_and_refresh_but_requires_post_header(dashboard):
    base, headers, calls = dashboard
    with urlopen(Request(base + '/', headers=headers), timeout=5) as response:
        assert b'Experiment history' in response.read()
    with urlopen(Request(base + '/api/experiments', headers=headers), timeout=5) as response:
        assert json.load(response) == []
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/api/refresh', method='POST', headers=headers), timeout=5)
    assert error.value.code == 403
    assert calls == []
    with urlopen(Request(base + '/api/refresh', method='POST',
                        headers={**headers, 'X-REXS-Request': '1'}), timeout=5) as response:
        assert json.load(response) == []
    assert calls == ['refresh']


def test_password_is_private_persistent_and_unique(tmp_path, monkeypatch):
    monkeypatch.delenv('REXS_SERVER_PASSWORD', raising=False)
    first = dashboard_password(tmp_path / 'one')
    assert len(first) >= 20
    assert dashboard_password(tmp_path / 'one') == first
    assert dashboard_password(tmp_path / 'two') != first
    assert stat.S_IMODE((tmp_path/'one/server.password').stat().st_mode) == 0o600


def test_password_override_and_empty_password_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('REXS_SERVER_PASSWORD', 'custom-password')
    assert dashboard_password(tmp_path) == 'custom-password'
    assert not (tmp_path/'server.password').exists()
    monkeypatch.setenv('REXS_SERVER_PASSWORD', '')
    with pytest.raises(ValueError, match='empty'):
        dashboard_password(tmp_path)
    monkeypatch.delenv('REXS_SERVER_PASSWORD')
    (tmp_path/'server.password').write_text('\n')
    with pytest.raises(ValueError, match='empty'):
        dashboard_password(tmp_path)


def test_password_file_symlink_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv('REXS_SERVER_PASSWORD', raising=False)
    target = tmp_path/'other'
    target.write_text('password')
    (tmp_path/'server.password').symlink_to(target)
    with pytest.raises(OSError):
        dashboard_password(tmp_path)


def test_password_login_establishes_session_for_browser_requests(dashboard):
    base, headers, calls = dashboard
    with urlopen(Request(base + '/', headers=headers), timeout=5) as response:
        cookie = response.headers['Set-Cookie']
        assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
        assert b'cleanUrl.username = ""' in response.read()
    session = {'Cookie': cookie.split(';')[0]}
    with urlopen(Request(base + '/api/experiments', headers=session), timeout=5) as response:
        assert json.load(response) == []
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/api/refresh', method='POST', headers=session), timeout=5)
    assert error.value.code == 403
    with urlopen(Request(base + '/api/refresh', method='POST',
                        headers={**session, 'X-REXS-Request': '1'}), timeout=5) as response:
        assert response.status == 200
    assert calls == ['refresh']
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/api/experiments', headers={'Cookie': session['Cookie'] + 'wrong'}), timeout=5)
    assert error.value.code == 401
