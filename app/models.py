import hashlib
import secrets
from datetime import datetime
from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum('admin', 'user'), default='user', nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('Employee', backref='user', uselist=False, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def get_id(self):
        return str(self.id)

    def __repr__(self):
        return f'<User {self.username}>'


class Employee(db.Model):
    __tablename__ = 'employees'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    ssn = db.Column(db.String(13))
    service_degree = db.Column(db.Numeric(4, 2), default=1.0, nullable=False)
    initial_flex_balance = db.Column(db.Numeric(8, 4), default=0.0, nullable=False)
    base_year = db.Column(db.Integer, default=2026, nullable=False)

    entries = db.relationship('TimeEntry', backref='employee', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Employee {self.name}>'


class MonthReference(db.Model):
    __tablename__ = 'month_reference'

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    reference_hours = db.Column(db.Numeric(6, 2), nullable=False)

    __table_args__ = (db.UniqueConstraint('year', 'month', name='uq_year_month'),)

    def __repr__(self):
        return f'<MonthReference {self.year}-{self.month}: {self.reference_hours}h>'


class TimeEntry(db.Model):
    __tablename__ = 'time_entries'

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False, index=True)
    entry_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    comment = db.Column(db.String(128))
    adj_from = db.Column(db.Time)
    adj_to = db.Column(db.Time)
    adj_sign = db.Column(db.Enum('+', '-'))
    notes = db.Column(db.String(256))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('employee_id', 'entry_date', name='uq_employee_date'),)

    def __repr__(self):
        return f'<TimeEntry {self.entry_date}>'


class ApiKey(db.Model):
    __tablename__ = 'api_keys'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    key_hash = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    user = db.relationship('User', backref='api_keys')

    @staticmethod
    def generate():
        raw = 'fbk_' + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw.encode()).hexdigest()
        return raw, key_hash

    @staticmethod
    def hash(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    def __repr__(self):
        return f'<ApiKey {self.name}>'
