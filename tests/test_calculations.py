"""Regression tests for app.calculations.

These exercise calc_entry()/calc_month_summary() directly as pure functions.
TimeEntry instances are constructed with plain keyword args (no DB/app
context needed for attribute access) so these tests need no Flask app,
no database, and no fixtures beyond the model class itself.
"""
from datetime import time
from decimal import Decimal

from app.calculations import calc_entry, calc_month_summary
from app.models import TimeEntry


def make_entry(**kwargs):
    defaults = dict(
        employee_id=1,
        start_time=None,
        end_time=None,
        comment=None,
        adj_from=None,
        adj_to=None,
        adj_sign=None,
        day_norm_hours=None,
    )
    defaults.update(kwargs)
    return TimeEntry(**defaults)


class TestVacationDoubleCounting:
    def test_vacation_day_with_prefilled_times_does_not_double_count(self):
        entry = make_entry(
            start_time=time(7, 0),
            end_time=time(15, 30),
            comment='Semester',
        )

        result = calc_entry(entry, service_degree=1.0)

        assert result['presence'] == 0.0
        assert result['deviation'] == 8.0
        assert result['total'] == 8.0

    def test_sjuk_with_prefilled_times_does_not_double_count(self):
        entry = make_entry(
            start_time=time(7, 0),
            end_time=time(15, 30),
            comment='Sjuk',
        )

        result = calc_entry(entry, service_degree=1.0)

        assert result['presence'] == 0.0
        assert result['deviation'] == 8.0
        assert result['total'] == 8.0

    def test_vard_av_barn_with_prefilled_times_does_not_double_count(self):
        entry = make_entry(
            start_time=time(7, 0),
            end_time=time(15, 30),
            comment='Vård av barn',
        )

        result = calc_entry(entry, service_degree=1.0)

        assert result['presence'] == 0.0
        assert result['deviation'] == 8.0
        assert result['total'] == 8.0


class TestReducedDayNorm:
    def test_vacation_day_uses_day_norm_hours_when_set(self):
        entry = make_entry(
            start_time=time(7, 0),
            end_time=time(11, 0),
            comment='Semester',
            day_norm_hours=Decimal('4'),
        )

        result = calc_entry(entry, service_degree=1.0)

        assert result['presence'] == 0.0
        assert result['deviation'] == 4.0
        assert result['total'] == 4.0


class TestNormalWorkdayRegression:
    def test_normal_workday_presence_accounts_for_lunch_deduction(self):
        entry = make_entry(start_time=time(7, 0), end_time=time(17, 30))

        result = calc_entry(entry, service_degree=1.0)

        assert result['presence'] == 10.0
        assert result['deviation'] == 0.0
        assert result['total'] == 10.0

    def test_normal_workday_produces_expected_flex_at_month_level(self):
        entry = make_entry(start_time=time(7, 0), end_time=time(17, 30))

        summary = calc_month_summary(
            [entry], service_degree=1.0, reference_hours=8, incoming_flex=0,
        )

        assert summary['total_hours'] == 10.0
        assert summary['reference_hours'] == 8.0
        assert summary['monthly_saldo'] == 2.0
        assert summary['outgoing_flex'] == 2.0


class TestMonthlyRegression:
    def test_replacing_normal_day_with_semester_day_does_not_change_saldo(self):
        # Scenario A: two normal 8h days (07:00-15:30, minus 0.5h lunch = 8h presence).
        normal_days = [
            make_entry(start_time=time(7, 0), end_time=time(15, 30)),
            make_entry(start_time=time(7, 0), end_time=time(15, 30)),
        ]

        # Scenario B: day 2 replaced by a full Semester day carrying the
        # same nominal 07:00-15:30 schedule (as stored by the app today).
        semester_days = [
            make_entry(start_time=time(7, 0), end_time=time(15, 30)),
            make_entry(start_time=time(7, 0), end_time=time(15, 30), comment='Semester'),
        ]

        summary_normal = calc_month_summary(
            normal_days, service_degree=1.0, reference_hours=16, incoming_flex=0,
        )
        summary_semester = calc_month_summary(
            semester_days, service_degree=1.0, reference_hours=16, incoming_flex=0,
        )

        assert summary_semester['monthly_saldo'] == summary_normal['monthly_saldo']
        assert summary_semester['outgoing_flex'] == summary_normal['outgoing_flex']
        assert summary_semester['monthly_saldo'] == 0.0
        assert summary_semester['outgoing_flex'] == 0.0
