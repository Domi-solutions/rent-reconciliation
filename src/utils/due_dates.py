"""When a month's charges fall due — one rule for rent, service charge and water.

The lease (Mowin, 2026): rent "payable on or before the 5th day of the month in
advance", water "together with the rent". So a period's charges are due on the
property's rent_due_day of that SAME month: November's rent and the water billed
for November are due 5 November. rent_due_day 0 = last day of the month.

(Until 2026-10-27 the code used the 5th of the following month; charges created
before then keep the date they were issued with.)
"""

import calendar


def charge_due_date(period, rent_due_day=5):
    """'2026-11' → '2026-11-05'. Returns None for a period that is not YYYY-MM (e.g. ARREARS)."""
    try:
        year, month = (int(x) for x in str(period).split('-'))
        if not 1 <= month <= 12:
            return None
    except (ValueError, TypeError):
        return None
    last = calendar.monthrange(year, month)[1]
    day = rent_due_day if rent_due_day is not None else 5
    day = last if int(day) == 0 else min(int(day), last)
    return f'{year:04d}-{month:02d}-{day:02d}'
