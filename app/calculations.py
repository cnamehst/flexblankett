from typing import Optional
from datetime import time

SPECIAL_STATUSES = {'Semester', 'Sjuk', 'Vård av barn'}

MONTH_NAMES_SV = {
    1: 'Januari', 2: 'Februari', 3: 'Mars', 4: 'April',
    5: 'Maj', 6: 'Juni', 7: 'Juli', 8: 'Augusti',
    9: 'September', 10: 'Oktober', 11: 'November', 12: 'December',
}

WEEKDAY_NAMES_SV = ['Mån', 'Tis', 'Ons', 'Tor', 'Fre', 'Lör', 'Sön']


def _time_to_hours(t: Optional[time]) -> float:
    if t is None:
        return 0.0
    return t.hour + t.minute / 60 + t.second / 3600


def calc_entry(entry, service_degree: float) -> dict:
    presence = 0.0
    deviation = 0.0

    if entry and entry.start_time and entry.end_time:
        raw = _time_to_hours(entry.end_time) - _time_to_hours(entry.start_time) - 0.5
        presence = max(0.0, raw)

    if entry and entry.comment in SPECIAL_STATUSES:
        day_norm = float(entry.day_norm_hours) if entry.day_norm_hours else 8.0
        deviation = day_norm * float(service_degree)
    elif entry and entry.adj_sign and entry.adj_from and entry.adj_to:
        hours = _time_to_hours(entry.adj_to) - _time_to_hours(entry.adj_from)
        deviation = hours if entry.adj_sign == '+' else -hours

    return {
        'presence': round(presence, 4),
        'deviation': round(deviation, 4),
        'total': round(presence + deviation, 4),
    }


def calc_month_summary(entries, service_degree: float, reference_hours: float, incoming_flex: float) -> dict:
    total_presence = 0.0
    total_deviation = 0.0

    for entry in entries:
        day = calc_entry(entry, service_degree)
        total_presence += day['presence']
        total_deviation += day['deviation']

    total_hours = total_presence + total_deviation
    ref_hours = float(reference_hours) * float(service_degree)
    monthly_saldo = total_hours - ref_hours
    outgoing_flex = float(incoming_flex) + monthly_saldo

    return {
        'total_presence': round(total_presence, 2),
        'total_deviation': round(total_deviation, 2),
        'total_hours': round(total_hours, 2),
        'reference_hours': round(ref_hours, 2),
        'monthly_saldo': round(monthly_saldo, 2),
        'incoming_flex': round(float(incoming_flex), 2),
        'outgoing_flex': round(outgoing_flex, 2),
    }
