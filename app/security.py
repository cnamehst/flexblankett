"""Client address behind the proxies, login throttling per user name, audit log."""
import logging

from flask import current_app, has_request_context, request
from flask_login import current_user
from limits import parse
from limits.strategies import MovingWindowRateLimiter

log = logging.getLogger(__name__)
audit_log = logging.getLogger('flexblankett.audit')

# The directory (IPA) locks an account for 10 minutes after 6 failed logins in
# a row with less than 60 s between them, and that lock applies to every
# service, not only this one. The login form must not be usable to lock
# somebody's account, so the number of attempts that reach the directory for
# one user name is kept below 6: 3 from the internet plus 2 from the LAN per
# 10 minutes. The two sides have separate budgets, so somebody on the internet
# who knows a user name can use up the internet side only.
LOGIN_ATTEMPTS = {
    'wan': parse('3 per 10 minutes'),
    'lan': parse('2 per 10 minutes'),
}


def client_ip():
    """Address of the browser, for rate limits and the audit log.

    request.remote_addr is the last hop in X-Forwarded-For (ProxyFix, set by
    Traefik). From the LAN that is the client. From the internet it is the
    public reverse proxy, the same for everybody; when that proxy is listed in
    TRUSTED_PROXIES the client is the right-most address in the header that is
    not a trusted proxy. Addresses further left are whatever the client sent
    and are never used.
    """
    trusted = current_app.config['TRUSTED_PROXIES']
    addr = request.remote_addr or ''
    if addr not in trusted:
        return addr
    hops = [h.strip() for h in request.headers.get('X-Forwarded-For', '').split(',')]
    for hop in reversed(hops):
        if hop and hop not in trusted:
            return hop
    return addr


# Checks of a local password (admin / service accounts) never reach the
# directory, so they have their own counter: enough for normal use, far too
# few to guess a password. Counted for successes too, nothing is given back.
# Per side, like the directory attempts, so the internet cannot use up the
# LAN's checks.
LOCAL_PASSWORD_CHECKS = parse('10 per 10 minutes')


def reserve_local_password_check(user_id):
    """Take one local password check for this account; False when none is
    left. Taken in one step before the password is compared.

    Counted on the account that was found, not on the text that was typed:
    the database matches names loosely (case, accents, trailing spaces), so
    many spellings find the same account and must share one counter.
    """
    key = ('flexblankett', 'local-password-check', _zone(), str(int(user_id)))
    try:
        return _attempts().hit(LOCAL_PASSWORD_CHECKS, *key)
    except Exception:
        log.exception('login attempt counter unavailable')
        return True


def _zone():
    """'wan' for requests that came through a trusted (public) proxy, else 'lan'."""
    return 'wan' if (request.remote_addr or '') in current_app.config['TRUSTED_PROXIES'] else 'lan'


def _attempt_key(zone, username):
    return ('flexblankett', 'login-attempt', zone, username.strip().lower()[:64])


def _attempts():
    from app import limiter
    return MovingWindowRateLimiter(limiter.storage)


def login_attempts_left(username):
    """False when this user name has no attempt left on this side. Does not
    take one."""
    zone = _zone()
    try:
        return _attempts().test(LOGIN_ATTEMPTS[zone], *_attempt_key(zone, username))
    except Exception:
        log.exception('login attempt counter unavailable')
        return True


def reserve_login_attempt(username):
    """Take one attempt for this user name; False when none is left.

    Taken before the password is checked, in one step in the counter store.
    Checking first and counting afterwards would let several requests at the
    same moment all pass the check.
    """
    zone = _zone()
    try:
        return _attempts().hit(LOGIN_ATTEMPTS[zone], *_attempt_key(zone, username))
    except Exception:
        # Counter store unreachable: let the login through, the limit per
        # address still applies.
        log.exception('login attempt counter unavailable')
        return True


def directory_login_succeeded(username):
    """A successful bind gives the attempts back, on both sides: the directory
    resets its own failure count at that moment too. Only for that case; a
    login that did not bind successfully must not reset anything."""
    try:
        for zone, limit in LOGIN_ATTEMPTS.items():
            _attempts().clear(limit, *_attempt_key(zone, username))
    except Exception:
        log.exception('login attempt counter unavailable')


def audit(event, **fields):
    """One line per security-relevant event, to the container log.

    Values are written with repr() so that text typed by a user cannot add
    lines or fields of its own. Never pass passwords or API keys.
    """
    actor = ip = '-'
    if has_request_context():
        ip = client_ip()
        if current_user and current_user.is_authenticated:
            actor = current_user.username
    details = ' '.join(f'{k}={v!r}' for k, v in fields.items())
    audit_log.info('%s actor=%r ip=%s %s', event, actor, ip, details)


def init_audit_log():
    if audit_log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s AUDIT %(message)s'))
    audit_log.addHandler(handler)
    audit_log.setLevel(logging.INFO)
    audit_log.propagate = False
