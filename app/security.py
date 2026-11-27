"""Client address behind the proxies, login throttling per user name, audit log."""
import logging
import logging.handlers
import socket
from datetime import datetime, timezone

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


MAX_SYSLOG_BYTES = 8000


class _Rfc5424(logging.Formatter):
    """Syslog message with its own time (UTC, with offset), host and program
    name, so the log server does not have to guess any of them. The message
    id is AUDIT for the audit log and the logger name for everything else."""

    def format(self, record):
        when = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds')
        text = record.getMessage()
        if record.exc_info:
            text += '\n' + self.formatException(record.exc_info)
        msgid = 'AUDIT' if record.name == audit_log.name else record.name[:32]
        line = f'1 {when} flexblankett flexblankett {record.process} {msgid} - {text}'
        return line.encode()[:MAX_SYSLOG_BYTES].decode(errors='ignore')


def _syslog_handler(target):
    """UDP syslog to host:port. UDP on purpose: nothing to connect, so a log
    server that is down or slow can never hold up or break a request."""
    host, _, port = target.rpartition(':')
    handler = logging.handlers.SysLogHandler(
        address=(host, int(port)),
        facility=logging.handlers.SysLogHandler.LOG_AUTH,
        socktype=socket.SOCK_DGRAM,
    )
    handler.append_nul = False
    handler.setFormatter(_Rfc5424())
    return handler


def init_logging(syslog_target=''):
    """Where the app's log lines go.

    Always to the container log (lost when the pod is replaced). With
    SYSLOG_TARGET set, also to that syslog server, which keeps them: the
    audit log and everything the app itself logs (warnings and errors from
    libraries included). gunicorn's own logs are sent there by gunicorn, see
    GUNICORN_CMD_ARGS in k8s/ldap-config.yaml.
    """
    if audit_log.handlers:
        return
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    audit_console = logging.StreamHandler()
    audit_console.setFormatter(logging.Formatter('%(asctime)s AUDIT %(message)s'))

    root = logging.getLogger()
    root.addHandler(console)
    root.setLevel(logging.WARNING)          # libraries: warnings and errors
    logging.getLogger('app').setLevel(logging.INFO)

    audit_log.addHandler(audit_console)
    audit_log.setLevel(logging.INFO)
    audit_log.propagate = False

    if syslog_target:
        try:
            syslog = _syslog_handler(syslog_target)
        except (OSError, ValueError):
            # Bad setting or the name does not resolve: say so and go on with
            # the container log only, rather than not starting.
            log.exception('SYSLOG_TARGET=%r not usable, logs go to the container log only',
                          syslog_target)
            return
        root.addHandler(syslog)
        audit_log.addHandler(syslog)
