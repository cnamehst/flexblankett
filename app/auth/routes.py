from urllib.parse import urlparse
from flask import render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.auth import bp
from app.models import User
from app import db, limiter
from app.security import audit, login_blocked, record_login_failure


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

        # Checked before the directory is asked, see app.security.
        if username and login_blocked(username):
            audit('login_throttled', username=username)
            flash('För många misslyckade försök. Vänta 10 minuter och försök igen.', 'danger')
            return render_template('auth/login.html')

        # Try LDAP first — auto-provisions user row on first login
        attrs = _try_ldap(username, password)
        if attrs is not None:
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

        # Fallback: local password hash (admin / service accounts)
        user = User.query.filter_by(username=username).first()
        if user and user.active and user.check_password(password):
            login_user(user, remember=remember)
            audit('login_ok', username=username, method='local')
            return redirect(_safe_next())

        if username:
            record_login_failure(username)
        audit('login_failed', username=username)
        flash('Felaktigt användarnamn eller lösenord.', 'danger')

    return render_template('auth/login.html')


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    audit('logout')
    logout_user()
    return redirect(url_for('auth.login'))
