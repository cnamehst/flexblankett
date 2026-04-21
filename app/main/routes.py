import calendar
from datetime import date, time
from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app import db
from app.main import bp
from app.models import TimeEntry, MonthReference
from app.calculations import (
    calc_entry, calc_month_summary,
    MONTH_NAMES_SV, WEEKDAY_NAMES_SV,
)


def _get_employee():
    emp = current_user.employee
    if not emp:
        flash('Ingen anställdprofil. Kontakta admin.', 'warning')
    return emp


def _get_incoming_flex(employee, year, month):
    balance = float(employee.initial_flex_balance)
    for m in range(1, month):
        ref = MonthReference.query.filter_by(year=year, month=m).first()
        if not ref:
            continue
        start = date(year, m, 1)
        _, last = calendar.monthrange(year, m)
        entries = TimeEntry.query.filter(
            TimeEntry.employee_id == employee.id,
            TimeEntry.entry_date >= start,
            TimeEntry.entry_date <= date(year, m, last),
        ).all()
        summary = calc_month_summary(entries, float(employee.service_degree),
                                     float(ref.reference_hours), balance)
        balance = summary['outgoing_flex']
    return balance


def _parse_time(field):
    val = request.form.get(field, '').strip()
    if not val:
        return None
    try:
        parts = val.split(':')
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


@bp.route('/')
@login_required
def index():
    today = date.today()
    return redirect(url_for('main.month_view', year=today.year, month=today.month))


@bp.route('/month/<int:year>/<int:month>')
@login_required
def month_view(year, month):
    if not 1 <= month <= 12:
        return redirect(url_for('main.index'))

    employee = _get_employee()
    if not employee:
        return redirect(url_for('main.index'))

    _, days_in_month = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, days_in_month)

    entries_raw = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.entry_date >= start_date,
        TimeEntry.entry_date <= end_date,
    ).all()
    entries_by_date = {e.entry_date: e for e in entries_raw}

    ref = MonthReference.query.filter_by(year=year, month=month).first()
    incoming_flex = _get_incoming_flex(employee, year, month)

    summary = None
    if ref:
        summary = calc_month_summary(
            entries_raw, float(employee.service_degree),
            float(ref.reference_hours), incoming_flex,
        )

    today = date.today()
    days = []
    for d in range(1, days_in_month + 1):
        day_date = date(year, month, d)
        entry = entries_by_date.get(day_date)
        days.append({
            'date': day_date,
            'weekday_name': WEEKDAY_NAMES_SV[day_date.weekday()],
            'is_weekend': day_date.weekday() >= 5,
            'is_today': day_date == today,
            'entry': entry,
            'calc': calc_entry(entry, float(employee.service_degree)) if entry else None,
        })

    prev_month = month - 1 or 12
    prev_year = year if month > 1 else year - 1
    next_month = month % 12 + 1
    next_year = year if month < 12 else year + 1

    return render_template('main/month.html',
        employee=employee,
        year=year, month=month,
        month_name=MONTH_NAMES_SV.get(month, str(month)),
        days=days,
        summary=summary,
        ref=ref,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        special_statuses=['Semester', 'Sjuk', 'Vård av barn'],
    )


@bp.route('/entry/save', methods=['POST'])
@login_required
def save_entry():
    employee = _get_employee()
    if not employee:
        return redirect(url_for('main.index'))

    entry_id = request.form.get('entry_id', type=int)
    try:
        entry_date = date.fromisoformat(request.form.get('entry_date', ''))
    except ValueError:
        flash('Ogiltigt datum.', 'danger')
        return redirect(url_for('main.index'))

    if entry_id:
        entry = db.session.get(TimeEntry, entry_id)
        if not entry or entry.employee_id != employee.id:
            flash('Inte behörig.', 'danger')
            return redirect(url_for('main.index'))
    else:
        existing = TimeEntry.query.filter_by(
            employee_id=employee.id, entry_date=entry_date
        ).first()
        entry = existing or TimeEntry(employee_id=employee.id, entry_date=entry_date)
        if not existing:
            db.session.add(entry)

    entry.start_time = _parse_time('start_time')
    entry.end_time = _parse_time('end_time')
    entry.comment = request.form.get('comment', '').strip() or None
    entry.adj_from = _parse_time('adj_from')
    entry.adj_to = _parse_time('adj_to')
    adj_sign = request.form.get('adj_sign', '').strip()
    entry.adj_sign = adj_sign if adj_sign in ('+', '-') else None
    entry.notes = request.form.get('notes', '').strip() or None

    db.session.commit()
    flash('Sparat.', 'success')
    return redirect(url_for('main.month_view', year=entry_date.year, month=entry_date.month))


@bp.route('/entry/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_entry(entry_id):
    employee = _get_employee()
    if not employee:
        return redirect(url_for('main.index'))

    entry = db.session.get(TimeEntry, entry_id)
    if not entry or entry.employee_id != employee.id:
        flash('Inte behörig.', 'danger')
        return redirect(url_for('main.index'))

    year, month = entry.entry_date.year, entry.entry_date.month
    db.session.delete(entry)
    db.session.commit()
    flash('Borttagen.', 'success')
    return redirect(url_for('main.month_view', year=year, month=month))


@bp.route('/overview/<int:year>')
@login_required
def overview(year):
    employee = _get_employee()
    if not employee:
        return redirect(url_for('main.index'))

    months_data = []
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
            summary = calc_month_summary(
                entries, float(employee.service_degree),
                float(ref.reference_hours), balance,
            )
            balance = summary['outgoing_flex']

        months_data.append({
            'month': m,
            'month_name': MONTH_NAMES_SV[m],
            'ref': ref,
            'summary': summary,
            'entry_count': len(entries),
        })

    return render_template('main/overview.html',
        employee=employee,
        year=year,
        months_data=months_data,
    )
