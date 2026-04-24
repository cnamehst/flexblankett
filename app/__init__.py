from datetime import date
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

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
