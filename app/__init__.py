from datetime import date
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

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
