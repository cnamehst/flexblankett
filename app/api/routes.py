import calendar
from datetime import date, datetime, time
from functools import wraps

from flask import request, jsonify, g
from app import db
from app.api import bp
from app.models import ApiKey, TimeEntry, MonthReference
from app.calculations import calc_entry, calc_month_summary, MONTH_NAMES_SV


def _api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
        key_hash = ApiKey.hash(auth[7:])
        api_key = ApiKey.query.filter_by(key_hash=key_hash, active=True).first()
        if not api_key:
            return jsonify({'error': 'Invalid or revoked API key'}), 401
        api_key.last_used_at = datetime.utcnow()
        db.session.commit()
        g.api_user = api_key.user
        return f(*args, **kwargs)
    return decorated


def _get_employee():
    emp = g.api_user.employee
    if not emp:
        return None, jsonify({'error': 'No employee profile linked to this user'}), 404
    return emp, None, None


def _get_incoming_flex(employee, year, month):
    balance = float(employee.initial_flex_balance)
    for m in range(1, month):
        ref = MonthReference.query.filter_by(year=year, month=m).first()
        if not ref:
            continue
        _, last = calendar.monthrange(year, m)
        entries = TimeEntry.query.filter(
            TimeEntry.employee_id == employee.id,
            TimeEntry.entry_date >= date(year, m, 1),
            TimeEntry.entry_date <= date(year, m, last),
        ).all()
        summary = calc_month_summary(entries, float(employee.service_degree),
                                     float(ref.reference_hours), balance)
        balance = summary['outgoing_flex']
    return balance


def _entry_to_dict(entry):
    return {
        'date': entry.entry_date.isoformat(),
        'start_time': entry.start_time.strftime('%H:%M') if entry.start_time else None,
        'end_time': entry.end_time.strftime('%H:%M') if entry.end_time else None,
        'comment': entry.comment,
        'adj_from': entry.adj_from.strftime('%H:%M') if entry.adj_from else None,
        'adj_to': entry.adj_to.strftime('%H:%M') if entry.adj_to else None,
        'adj_sign': entry.adj_sign,
        'notes': entry.notes,
        'day_norm_hours': float(entry.day_norm_hours) if entry.day_norm_hours else None,
    }


def _parse_time(val):
    if not val:
        return None
    try:
        parts = str(val).split(':')
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


@bp.route('/api/v1/balance')
@_api_key_required
def balance():
    """Current flex balance up to and including the current month."""
    employee, err, code = _get_employee()
    if err:
        return err, code

    today = date.today()
    incoming = _get_incoming_flex(employee, today.year, today.month)
    ref = MonthReference.query.filter_by(year=today.year, month=today.month).first()

    _, last = calendar.monthrange(today.year, today.month)
    entries = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.entry_date >= date(today.year, today.month, 1),
        TimeEntry.entry_date <= date(today.year, today.month, last),
    ).all()

    current_flex = incoming
    if ref:
        summary = calc_month_summary(entries, float(employee.service_degree),
                                     float(ref.reference_hours), incoming)
        current_flex = summary['outgoing_flex']

    return jsonify({
        'employee': employee.name,
        'flex_balance': current_flex,
        'as_of': {'year': today.year, 'month': today.month},
        'today': today.isoformat(),
    })


@bp.route('/api/v1/month/<int:year>/<int:month>')
@_api_key_required
def month(year, month):
    if not 1 <= month <= 12:
        return jsonify({'error': 'Invalid month'}), 400

    employee, err, code = _get_employee()
    if err:
        return err, code

    _, last = calendar.monthrange(year, month)
    entries_raw = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.entry_date >= date(year, month, 1),
        TimeEntry.entry_date <= date(year, month, last),
    ).all()

    ref = MonthReference.query.filter_by(year=year, month=month).first()
    incoming = _get_incoming_flex(employee, year, month)

    summary = None
    if ref:
        summary = calc_month_summary(entries_raw, float(employee.service_degree),
                                     float(ref.reference_hours), incoming)

    return jsonify({
        'year': year,
        'month': month,
        'month_name': MONTH_NAMES_SV.get(month),
        'summary': summary,
        'entries': [_entry_to_dict(e) for e in sorted(entries_raw, key=lambda e: e.entry_date)],
    })


@bp.route('/api/v1/overview/<int:year>')
@_api_key_required
def overview(year):
    employee, err, code = _get_employee()
    if err:
        return err, code

    months = []
    balance = float(employee.initial_flex_balance)

    for m in range(1, 13):
        ref = MonthReference.query.filter_by(year=year, month=m).first()
        _, last = calendar.monthrange(year, m)
        entries = TimeEntry.query.filter(
            TimeEntry.employee_id == employee.id,
            TimeEntry.entry_date >= date(year, m, 1),
            TimeEntry.entry_date <= date(year, m, last),
        ).all()

        summary = None
        if ref:
            summary = calc_month_summary(entries, float(employee.service_degree),
                                         float(ref.reference_hours), balance)
            balance = summary['outgoing_flex']

        months.append({
            'month': m,
            'month_name': MONTH_NAMES_SV[m],
            'entry_count': len(entries),
            'summary': summary,
        })

    return jsonify({'year': year, 'employee': employee.name, 'months': months})


@bp.route('/api/v1/entry', methods=['POST'])
@_api_key_required
def save_entry():
    employee, err, code = _get_employee()
    if err:
        return err, code

    data = request.get_json(silent=True) or {}

    try:
        entry_date = date.fromisoformat(data.get('date', ''))
    except ValueError:
        return jsonify({'error': 'Invalid or missing date (expected YYYY-MM-DD)'}), 400

    existing = TimeEntry.query.filter_by(
        employee_id=employee.id, entry_date=entry_date
    ).first()
    entry = existing or TimeEntry(employee_id=employee.id, entry_date=entry_date)
    if not existing:
        db.session.add(entry)

    entry.start_time = _parse_time(data.get('start_time'))
    entry.end_time = _parse_time(data.get('end_time'))
    entry.comment = data.get('comment') or None
    entry.adj_from = _parse_time(data.get('adj_from'))
    entry.adj_to = _parse_time(data.get('adj_to'))
    adj_sign = data.get('adj_sign', '')
    entry.adj_sign = adj_sign if adj_sign in ('+', '-') else None
    entry.notes = data.get('notes') or None
    try:
        entry.day_norm_hours = float(data['day_norm_hours']) if data.get('day_norm_hours') else None
    except (ValueError, TypeError):
        entry.day_norm_hours = None

    db.session.commit()

    calc = calc_entry(entry, float(employee.service_degree))
    return jsonify({'saved': _entry_to_dict(entry), 'calc': calc}), 200
