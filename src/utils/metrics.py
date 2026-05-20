"""
Shared property metrics helpers.
Single source of truth for occupancy counts, expected income, and arrears depth.
Import from here — never define these inline in route files.
"""
from math import ceil


def get_property_occupancy(conn, property_id):
    """
    Returns occupancy counts for a property in a single query.
    Keys: total, occupied, vacant, owner_use, short_term, rentable, occupancy_rate
    'owner_use' counts both 'owner_use' and legacy 'office' rows.
    """
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'occupied'  THEN 1 ELSE 0 END) AS occupied,
            SUM(CASE WHEN status = 'vacant'    THEN 1 ELSE 0 END) AS vacant,
            SUM(CASE WHEN status IN ('owner_use', 'office') THEN 1 ELSE 0 END) AS owner_use,
            SUM(CASE WHEN status = 'short_term' THEN 1 ELSE 0 END) AS short_term
        FROM units WHERE property_id = ?
    """, (property_id,)).fetchone()
    total      = row['total']      or 0
    occupied   = row['occupied']   or 0
    vacant     = row['vacant']     or 0
    owner_use  = row['owner_use']  or 0
    short_term = row['short_term'] or 0
    rentable   = total - owner_use - short_term
    occupancy_rate = round(occupied / rentable * 100, 1) if rentable > 0 else 0.0
    return {
        'total': total, 'occupied': occupied, 'vacant': vacant,
        'owner_use': owner_use, 'short_term': short_term,
        'rentable': rentable, 'occupancy_rate': occupancy_rate,
        'office': owner_use,  # backward-compat alias
    }


def get_expected_monthly_income(conn, property_id):
    """Sum of monthly_rent + service_charge for all currently occupied units."""
    row = conn.execute(
        "SELECT COALESCE(SUM(monthly_rent), 0) AS rent, COALESCE(SUM(service_charge), 0) AS svc "
        "FROM units WHERE property_id = ? AND status = 'occupied'",
        (property_id,)
    ).fetchone()
    return float(row['rent']) + float(row['svc'])


def get_months_behind(balance, monthly_rent):
    """Months in arrears rounded up. Returns 0 when monthly_rent is zero or unknown."""
    if not monthly_rent or float(monthly_rent) <= 0:
        return 0
    return ceil(float(balance) / float(monthly_rent))
