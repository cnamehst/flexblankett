"""Add day_norm_hours column to time_entries."""
from app import create_app, db
from sqlalchemy import text

app = create_app()
with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(text(
            'ALTER TABLE time_entries ADD COLUMN day_norm_hours DECIMAL(4,2) NULL'
        ))
        conn.commit()
    print('Done.')
