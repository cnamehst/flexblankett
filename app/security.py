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
# service, not only this one. Staying below it here means the login form
# cannot be used to lock somebody's account: after 4 failures for a user name
# the app stops asking the directory for 10 minutes.
LOGIN_FAILURE_LIMIT = parse('4 per 10 minutes')
# Internet and LAN are counted separately (see _failure_keys), so on top of
# that the two together are capped at 5: never 6 failures for one name.
LOGIN_FAILURE_TOTAL = parse('5 per 10 minutes')


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


def _zone():
    """'wan' for requests that came through a trusted (public) proxy, else 'lan'."""
    return 'wan' if (request.remote_addr or '') in current_app.config['TRUSTED_PROXIES'] else 'lan'


def _failure_keys(username):
    """(limit, key) pairs that a failed login for this name counts against.

    Per side, internet or LAN: somebody on the internet who knows a user name
    can use up the internet side (4) but not the LAN side, so the user still
    gets in from the LAN with the right password. And the two sides together
    (5), so that what reaches the directory stays below its lock threshold
    whatever the mix.
    """
    name = username.strip().lower()[:64]
    return (
        (LOGIN_FAILURE_LIMIT, ('flexblankett', 'login-failure', _zone(), name)),
        (LOGIN_FAILURE_TOTAL, ('flexblankett', 'login-failure', 'total', name)),
    )


def _failures():
    from app import limiter
    return MovingWindowRateLimiter(limiter.storage)


def login_blocked(username):
    """True when this user name has used up its failed logins."""
    try:
        counter = _failures()
        return not all(counter.test(limit, *key) for limit, key in _failure_keys(username))
    except Exception:
        # Counter store unreachable: let the login through, the limit per
        # address still applies.
        log.exception('login failure counter unavailable')
        return False


def record_login_failure(username):
    try:
        counter = _failures()
        for limit, key in _failure_keys(username):
            counter.hit(limit, *key)
    except Exception:
        log.exception('login failure counter unavailable')


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
