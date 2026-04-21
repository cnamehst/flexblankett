from functools import wraps
from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app import db
from app.admin import bp
from app.models import User, Employee


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Kräver admin-behörighet.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/users')
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=all_users)


@bp.route('/users/new', methods=['GET', 'POST'])
@admin_required
def new_user():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        name = request.form.get('name', '').strip()
        ssn = request.form.get('ssn', '').strip() or None
        service_degree = float(request.form.get('service_degree', 1.0) or 1.0)
        initial_flex = float(request.form.get('initial_flex_balance', 0.0) or 0.0)
        base_year = int(request.form.get('base_year', 2026) or 2026)

        error = None
        if User.query.filter_by(username=username).first():
            error = f'Användarnamn "{username}" redan taget.'
        elif User.query.filter_by(email=email).first():
            error = f'E-post "{email}" redan registrerad.'
        elif not password:
            error = 'Lösenord krävs.'

        if error:
            flash(error, 'danger')
            return render_template('admin/user_form.html', action='new',
                                   edit_user=None, form_data=request.form)

        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        db.session.add(Employee(
            user_id=user.id, name=name, ssn=ssn,
            service_degree=service_degree,
            initial_flex_balance=initial_flex,
            base_year=base_year,
        ))
        db.session.commit()
        flash(f'Användare {username} skapad.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', action='new', edit_user=None, form_data={})


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Användaren hittades inte.', 'danger')
        return redirect(url_for('admin.users'))

    if request.method == 'POST':
        new_username = request.form.get('username', '').strip()
        new_email = request.form.get('email', '').strip()

        conflict = User.query.filter(User.username == new_username, User.id != user.id).first()
        if conflict:
            flash(f'Användarnamn "{new_username}" redan taget.', 'danger')
            return render_template('admin/user_form.html', action='edit',
                                   edit_user=user, form_data=request.form)

        user.username = new_username
        user.email = new_email
        user.role = request.form.get('role', user.role)
        user.active = 'active' in request.form

        password = request.form.get('password', '').strip()
        if password:
            user.set_password(password)

        if not user.employee:
            emp = Employee(user_id=user.id, name='')
            db.session.add(emp)
            db.session.flush()

        user.employee.name = request.form.get('name', '').strip()
        user.employee.ssn = request.form.get('ssn', '').strip() or None
        user.employee.service_degree = float(request.form.get('service_degree', 1.0) or 1.0)
        user.employee.initial_flex_balance = float(request.form.get('initial_flex_balance', 0.0) or 0.0)
        user.employee.base_year = int(request.form.get('base_year', 2026) or 2026)

        db.session.commit()
        flash('Sparat.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', action='edit', edit_user=user, form_data={})


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Användaren hittades inte.', 'danger')
        return redirect(url_for('admin.users'))

    if user.id == current_user.id:
        flash('Kan inte ta bort sig själv.', 'danger')
        return redirect(url_for('admin.users'))

    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'Användare {username} borttagen.', 'success')
    return redirect(url_for('admin.users'))
