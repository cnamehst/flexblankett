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
# 3 attempts from the internet and 2 from the LAN per 10 minutes.

def wan(i):
    return f'203.0.113.{i}, {PROXY}'


def lan(i):
    return f'192.168.1.{i}'


def test_lan_third_attempt_is_refused_even_with_the_right_password(app, audit_lines):
    client = app.test_client()
    # Different addresses, so the limit per address is not what stops it.
    for i in range(2):
        assert login(client, 'anna', 'wrong', addr=lan(i)).status_code == 200
    response = login(client, 'anna', 'right', addr=lan(99))
    assert response.status_code == 200          # login page again, no redirect
    assert 'För många misslyckade försök' in response.get_data(as_text=True)
    assert sum(line.startswith('login_failed') for line in audit_lines) == 2
    assert sum(line.startswith('login_throttled') for line in audit_lines) == 1


def test_internet_fourth_attempt_is_refused(app):
    client = app.test_client()
    for i in range(3):
        login(client, 'anna', 'wrong', addr=wan(i))
    assert 'För många' in login(client, 'anna', 'right', addr=wan(99)).get_data(as_text=True)


def test_throttle_is_case_insensitive(app):
    client = app.test_client()
    for i, name in enumerate(('anna', 'ANNA')):
        login(client, name, 'wrong', addr=lan(i))
    assert 'För många' in login(client, 'aNNa', 'right', addr=lan(9)).get_data(as_text=True)


def test_other_user_is_not_affected(app):
    client = app.test_client()
    for i in range(2):
        login(client, 'anna', 'wrong', addr=lan(i))
    assert login(client, 'bo', 'right', addr=lan(9)).status_code == 302


def test_failures_from_the_internet_do_not_block_the_lan(app):
    client = app.test_client()
    for i in range(6):
        login(client, 'anna', 'wrong', addr=wan(i))
    # The LAN side still has both its attempts: one typo, then in.
    assert login(client, 'anna', 'wrong', addr=lan(1)).status_code == 200
    assert login(client, 'anna', 'right', addr=lan(2)).status_code == 302


def test_never_six_password_checks_for_one_name(app, audit_lines):
    client = app.test_client()
    for i in range(10):
        login(client, 'anna', 'wrong', addr=wan(i))
        login(client, 'anna', 'wrong', addr=lan(i))
    # 3 from the internet + 2 from the LAN got as far as a password check.
    assert sum(line.startswith('login_failed') for line in audit_lines) == 5
    assert sum(line.startswith('login_throttled') for line in audit_lines) == 15


def test_directory_success_gives_the_attempts_back(app, monkeypatch):
    from app.auth import routes
    monkeypatch.setattr(routes, '_try_ldap',
                        lambda u, p: {'email': 'cia@example.com'} if p == 'ldap-right' else None)
    client = app.test_client()
    for round_ in range(3):
        assert login(client, 'cia', 'wrong', addr=lan(round_)).status_code == 200
        assert login(client, 'cia', 'ldap-right', addr=lan(50 + round_)).status_code == 302
        client.post('/logout')


def test_local_login_sends_nothing_to_the_directory(app, monkeypatch):
    from app.auth import routes
    binds = []
    monkeypatch.setattr(routes, '_try_ldap', lambda u, p: binds.append(u))
    assert login(app.test_client(), 'anna', 'right', addr=lan(1)).status_code == 302
    assert binds == []


def test_local_success_does_not_give_attempts_back(app, monkeypatch):
    """Failed binds for the name stay counted when a local password matches,
    so local logins cannot be used to send failure after failure to the
    directory."""
    from app.auth import routes
    binds = []
    monkeypatch.setattr(routes, '_try_ldap', lambda u, p: binds.append(u))
    client = app.test_client()
    for round_ in range(5):
        login(client, 'anna', 'wrong', addr=lan(round_))
        if login(client, 'anna', 'right', addr=lan(50 + round_)).status_code == 302:
            client.post('/logout')
    assert len(binds) == 2      # the LAN budget, no more


def test_local_password_checks_are_limited_per_name(app, monkeypatch):
    """Even if the directory budget were never used up (requests at the same
    moment), a local password is compared at most 10 times in 10 minutes."""
    from app.auth import routes
    from app.models import User as U
    monkeypatch.setattr(routes, 'login_attempts_left', lambda u: True)
    monkeypatch.setattr(routes, 'reserve_login_attempt', lambda u: False)
    checks = []
    real = U.check_password
    monkeypatch.setattr(U, 'check_password',
                        lambda self, pw: checks.append(pw) or real(self, pw))
    client = app.test_client()
    for i in range(15):
        login(client, 'anna', 'wrong', addr=lan(i))
    assert len(checks) == 10
    assert login(client, 'anna', 'right', addr=lan(99)).status_code == 200   # refused
    # The internet side has its own checks and is not affected.
    assert login(client, 'anna', 'right', addr=wan(1)).status_code == 302


def test_local_checks_are_counted_on_the_account_not_the_spelling(app, monkeypatch):
    """MariaDB finds the same account for 'anna', 'ANNA', 'ánna', ... Stand in
    for that here (sqlite compares exactly) and check that all spellings
    share one counter."""
    from app.auth import routes
    from app.models import User as U
    monkeypatch.setattr(routes, 'login_attempts_left', lambda u: True)
    monkeypatch.setattr(routes, 'reserve_login_attempt', lambda u: False)
    anna = U.query.filter_by(username='anna').first()

    class LooseQuery:
        def filter_by(self, **kw):
            return self

        def first(self):
            return anna

    monkeypatch.setattr(routes.User, 'query', LooseQuery())
    checks = []
    real = U.check_password
    monkeypatch.setattr(U, 'check_password',
                        lambda self, pw: checks.append(pw) or real(self, pw))
    client = app.test_client()
    for i in range(15):
        login(client, f'spelling-{i}', 'wrong', addr=lan(i))
    assert len(checks) == 10


def test_attempt_is_taken_before_the_password_check(app, monkeypatch):
    """Requests at the same moment must not all slip past the limit: the
    attempt is counted when the request starts, not after the check."""
    from app.auth import routes
    from app.security import reserve_login_attempt

    seen = []

    def check_then_record(username, password):
        # While "the directory is being asked", is the attempt already counted?
        with app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.168.1.77'}):
            seen.append(reserve_login_attempt(username))
        return None

    monkeypatch.setattr(routes, '_try_ldap', check_then_record)
    login(app.test_client(), 'anna', 'wrong', addr=lan(1))
    # LAN budget is 2: the request took one, the probe inside took the second,
    # so a third is refused.
    with app.test_request_context('/login', environ_base={'REMOTE_ADDR': '192.168.1.78'}):
        assert seen == [True] and reserve_login_attempt('anna') is False


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
    for i in range(4):
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
