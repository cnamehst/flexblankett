from app import create_app, db
from app.models import User, Employee, MonthReference

app = create_app()

MONTH_HOURS_2026 = {
    1: 144, 2: 160, 3: 176, 4: 160,
    5: 144, 6: 168, 7: 184, 8: 168,
    9: 176, 10: 176, 11: 168, 12: 160,
}

with app.app_context():
    db.create_all()

    for month, hours in MONTH_HOURS_2026.items():
        if not MonthReference.query.filter_by(year=2026, month=month).first():
            db.session.add(MonthReference(year=2026, month=month, reference_hours=hours))

    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', email='admin@example.com', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.flush()
        db.session.add(Employee(
            user_id=admin.id,
            name='Admin',
            service_degree=1.0,
            initial_flex_balance=0.0,
            base_year=2026,
        ))

    db.session.commit()
    print("Done. Admin login: admin / admin123")
