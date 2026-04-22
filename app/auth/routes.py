from flask import render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.auth import bp
from app.models import User
from app import db


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
    )


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = 'remember' in request.form

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
                return redirect(request.args.get('next') or url_for('main.index'))
            flash('Kontot är inaktiverat.', 'danger')
            return render_template('auth/login.html')

        # Fallback: local password hash (admin / service accounts)
        user = User.query.filter_by(username=username).first()
        if user and user.active and user.check_password(password):
            login_user(user, remember=remember)
            return redirect(request.args.get('next') or url_for('main.index'))

        flash('Felaktigt användarnamn eller lösenord.', 'danger')

    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
