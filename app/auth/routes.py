from urllib.parse import urlparse
from flask import render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.auth import bp
from app.models import User
from app import db, limiter
from app.security import (
    audit, directory_login_succeeded, login_attempts_left, reserve_local_password_check,
    reserve_login_attempt,
)


def _try_ldap(username, password):
    cfg = current_app.config
    if not cfg.get('LDAP_ENABLED'):
        return None
    from app.auth.ldap import ldap_authenticate
    return ldap_authenticate(
        host=cfg['LDAP_HOST'],
        base_dn=cfg['LDAP_BASE_DN'],
        ca_cert=cfg['LDAP_CA_CERT'],
        username=username,
        password=password,
        required_group=cfg.get('LDAP_REQUIRED_GROUP'),
    )


def _name_for_log(username):
    """The typed user name if it is an account here, else a placeholder.

    People sometimes type their password in the user name field; text that
    is not a known account must not end up in the log.
    """
    if username and User.query.filter_by(username=username).first():
        return username
    return '<not an account here>'


def _throttled(username):
    audit('login_throttled', username=_name_for_log(username))
    flash('För många misslyckade försök. Vänta 10 minuter och försök igen.', 'danger')
    return render_template('auth/login.html')


def _safe_next():
    next_url = request.args.get('next', '')
    # Only same-site absolute paths; reject //host and backslash tricks
    # (/\evil.com is normalised to //evil.com by browsers).
    if (not next_url.startswith('/') or next_url.startswith('//')
            or '\\' in next_url or urlparse(next_url).netloc):
        return url_for('main.index')
    return next_url


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('20 per minute; 5 per 10 seconds', methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = 'remember' in request.form

        if username and not login_attempts_left(username):
            return _throttled(username)

        # Local password first (admin / service accounts). A match never
        # touches the directory, and does not give attempts back: earlier
        # failures for this name may have reached the directory, and only a
        # successful bind there resets its count.
        user = User.query.filter_by(username=username).first()
        if user and user.active and user.has_local_password:
            # Counted in one step before the comparison, so that requests at
            # the same moment cannot get more guesses than the limit.
            if not reserve_local_password_check(username):
                return _throttled(username)
            if user.check_password(password):
                login_user(user, remember=remember)
                audit('login_ok', username=username, method='local')
                return redirect(_safe_next())

        # Everything below may send a bind to the directory, so the attempt
        # is taken first, in one step (see app.security).
        if username and not reserve_login_attempt(username):
            return _throttled(username)

        # LDAP — auto-provisions user row on first login
        attrs = _try_ldap(username, password)
        if attrs is not None:
            directory_login_succeeded(username)
            user = User.query.filter_by(username=username).first()
            if user is None:
                user = User(username=username, email=attrs['email'], password_hash='!ldap')
                db.session.add(user)
                db.session.commit()
            if user.active:
                login_user(user, remember=remember)
                audit('login_ok', username=username, method='ldap')
                return redirect(_safe_next())
            audit('login_denied', username=username, reason='inactive')
            flash('Kontot är inaktiverat.', 'danger')
            return render_template('auth/login.html')

        audit('login_failed', username=_name_for_log(username))
        flash('Felaktigt användarnamn eller lösenord.', 'danger')

    return render_template('auth/login.html')


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    audit('logout')
    logout_user()
    return redirect(url_for('auth.login'))
