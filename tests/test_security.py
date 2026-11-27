"""Client address behind the proxies, login throttling per user name, audit log."""
import logging

import pytest

from app import create_app, db, limiter
from app.models import User
from app.security import audit_log, client_ip

PROXY = '192.168.203.9'  # TRUSTED_PROXIES in conftest.py


@pytest.fixture
def app():
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, LDAP_ENABLED=False)
    with app.app_context():
        db.create_all()
        for name in ('anna', 'bo'):
            user = User(username=name, email=f'{name}@example.com')
            user.set_password('right')
            db.session.add(user)
        db.session.commit()
        limiter.reset()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def audit_lines(app):
    class Collect(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    handler = Collect()
    audit_log.addHandler(handler)
    yield handler.lines
    audit_log.removeHandler(handler)


def login(client, username, password, addr='10.0.0.1'):
    return client.post('/login', data={'username': username, 'password': password},
                       headers={'X-Forwarded-For': addr})


# --- client address ----------------------------------------------------------

def ip_for(app, remote_addr, forwarded):
    with app.test_request_context('/', headers={'X-Forwarded-For': forwarded},
                                  environ_base={'REMOTE_ADDR': remote_addr}):
        return client_ip()


def test_lan_client_is_the_last_hop(app):
    assert ip_for(app, '192.168.1.50', '192.168.1.50') == '192.168.1.50'


def test_internet_client_is_the_hop_before_the_trusted_proxy(app):
    assert ip_for(app, PROXY, f'203.0.113.7, {PROXY}') == '203.0.113.7'


def test_address_sent_by_the_client_is_ignored(app):
    # The browser sent "1.2.3.4"; the public proxy appended the real address.
    assert ip_for(app, PROXY, f'1.2.3.4, 203.0.113.7, {PROXY}') == '203.0.113.7'


def test_falls_back_to_the_proxy_when_nothing_else_is_known(app):
    assert ip_for(app, PROXY, PROXY) == PROXY


def test_lan_client_cannot_pose_as_another_address(app):
    # Not from a trusted proxy: the header is not looked at.
    assert ip_for(app, '192.168.1.50', '203.0.113.7, 192.168.1.50') == '192.168.1.50'


def test_rate_limit_is_per_internet_client(app):
    client = app.test_client()
    # 5 per 10 seconds per address; six from one address, then another address.
    codes = [login(client, f'nobody{i}', 'x', addr=f'203.0.113.7, {PROXY}').status_code
             for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    assert login(client, 'nobody9', 'x', addr=f'203.0.113.8, {PROXY}').status_code == 200


# --- login throttling per user name -------------------------------------------

def test_fifth_attempt_is_refused_even_with_the_right_password(app, audit_lines):
    client = app.test_client()
    # Different addresses, so the limit per address is not what stops it.
    for i in range(4):
        assert login(client, 'anna', 'wrong', addr=f'10.0.0.{i}').status_code == 200
    response = login(client, 'anna', 'right', addr='10.0.0.99')
    assert response.status_code == 200          # login page again, no redirect
    assert 'För många misslyckade försök' in response.get_data(as_text=True)
    assert sum('login_failed' in line for line in audit_lines) == 4
    assert any('login_throttled' in line for line in audit_lines)


def test_throttle_is_case_insensitive(app):
    client = app.test_client()
    for i, name in enumerate(('anna', 'Anna', 'ANNA', 'aNNa')):
        login(client, name, 'wrong', addr=f'10.0.1.{i}')
    assert 'För många' in login(client, 'anna', 'right', addr='10.0.1.9').get_data(as_text=True)


def test_other_user_is_not_affected(app):
    client = app.test_client()
    for i in range(4):
        login(client, 'anna', 'wrong', addr=f'10.0.2.{i}')
    assert login(client, 'bo', 'right', addr='10.0.2.9').status_code == 302


def test_failures_from_the_internet_do_not_block_the_lan(app):
    client = app.test_client()
    for i in range(4):
        login(client, 'anna', 'wrong', addr=f'203.0.113.{i}, {PROXY}')
    blocked = login(client, 'anna', 'right', addr=f'203.0.113.99, {PROXY}')
    assert 'För många' in blocked.get_data(as_text=True)
    assert login(client, 'anna', 'right', addr='192.168.1.50').status_code == 302


def test_both_sides_together_stay_below_the_directory_lock(app, audit_lines):
    client = app.test_client()
    for i in range(4):                                   # internet side used up
        login(client, 'anna', 'wrong', addr=f'203.0.113.{i}, {PROXY}')
    login(client, 'anna', 'wrong', addr='192.168.1.50')  # one more from the LAN: 5
    for i in range(3):                                   # refused on both sides now
        login(client, 'anna', 'wrong', addr=f'192.168.1.{60 + i}')
        login(client, 'anna', 'wrong', addr=f'203.0.113.{60 + i}, {PROXY}')
    # Only five attempts got as far as a password check.
    assert sum(line.startswith('login_failed') for line in audit_lines) == 5
    assert sum(line.startswith('login_throttled') for line in audit_lines) == 6


def test_three_failures_then_success(app):
    client = app.test_client()
    for i in range(3):
        login(client, 'anna', 'wrong', addr=f'10.0.3.{i}')
    assert login(client, 'anna', 'right', addr='10.0.3.9').status_code == 302


# --- audit log ----------------------------------------------------------------

def test_login_and_logout_are_logged_with_the_client_address(app, audit_lines):
    client = app.test_client()
    login(client, 'bo', 'right', addr=f'203.0.113.7, {PROXY}')
    client.post('/logout', headers={'X-Forwarded-For': f'203.0.113.7, {PROXY}'})
    assert any(line.startswith('login_ok') and "username='bo'" in line
               and 'ip=203.0.113.7' in line for line in audit_lines)
    assert any(line.startswith("logout actor='bo'") for line in audit_lines)


def test_typed_text_cannot_add_log_lines(app, audit_lines):
    login(app.test_client(), 'x\nlogin_ok actor=admin', 'wrong')
    assert all('\n' not in line for line in audit_lines)


def test_text_that_is_not_an_account_is_not_logged(app, audit_lines):
    # A password typed in the user name field.
    client = app.test_client()
    for i in range(5):
        login(client, 'MyPassw0rd', 'x', addr=f'10.0.4.{i}')
    text = '\n'.join(audit_lines)
    assert 'MyPassw0rd' not in text
    assert 'login_failed' in text and 'login_throttled' in text


def test_changed_email_is_logged_without_the_address(app, audit_lines):
    with app.app_context():
        admin = User(username='root', email='root@example.com', role='admin')
        admin.set_password('right')
        db.session.add(admin)
        db.session.commit()
    client = app.test_client()
    login(client, 'root', 'right')
    client.post('/admin/users/1/edit', data={
        'username': 'anna', 'email': 'new@example.org', 'role': 'user',
        'active': '1', 'name': 'Anna'})
    text = '\n'.join(audit_lines)
    assert "'email': 'changed'" in text and 'new@example.org' not in text


def test_password_is_never_logged(app, audit_lines):
    client = app.test_client()
    login(client, 'anna', 'hunter2-secret')
    login(client, 'anna', 'right')
    assert not any('hunter2-secret' in line or "'right'" in line for line in audit_lines)


def test_api_key_and_admin_events(app, audit_lines):
    with app.app_context():
        admin = User(username='root', email='root@example.com', role='admin')
        admin.set_password('right')
        db.session.add(admin)
        db.session.commit()
    client = app.test_client()
    login(client, 'bo', 'right')
    client.post('/keys/create', data={'name': 'phone'})
    assert client.get('/admin/users').status_code == 302
    client.post('/logout')
    login(client, 'root', 'right')
    client.post('/admin/users/1/edit', data={
        'username': 'anna', 'email': 'anna@example.com', 'role': 'admin',
        'name': 'Anna', 'password': 'new-password'})
    text = '\n'.join(audit_lines)
    assert "api_key_created actor='bo'" in text and "name='phone'" in text
    assert "admin_denied actor='bo'" in text
    assert "user_edited actor='root'" in text and "'role': ['user', 'admin']" in text
    assert 'password_set=True' in text and 'new-password' not in text
