from datetime import date
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def _rate_limit_key():
    from app.security import client_ip
    return client_ip()


limiter = Limiter(key_func=_rate_limit_key, default_limits=[])


APP_VERSION = "1.0.3"


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')
    # One proxy hop is trusted for X-Forwarded-*: Traefik. See app.security.client_ip
    # for how the public reverse proxy in front of Traefik is handled.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.jinja_env.globals['APP_VERSION'] = APP_VERSION

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from app.security import init_audit_log
    init_audit_log(app.config['AUDIT_SYSLOG'])

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Logga in för att fortsätta.'
    login_manager.login_message_category = 'warning'

    with app.app_context():
        from app.auth import bp as auth_bp
        app.register_blueprint(auth_bp)

        from app.main import bp as main_bp
        app.register_blueprint(main_bp)

        from app.admin import bp as admin_bp
        app.register_blueprint(admin_bp, url_prefix='/admin')

        from app.api import bp as api_bp
        csrf.exempt(api_bp)
        app.register_blueprint(api_bp)

    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "frame-ancestors 'none';"
        )
        return response

    @app.template_filter('timeformat')
    def timeformat(t):
        return t.strftime('%H:%M') if t else ''

    @app.template_filter('signed')
    def signed_filter(v):
        return f'{float(v):+.2f}'

    @app.context_processor
    def inject_globals():
        return {'now_year': date.today().year}

    return app
