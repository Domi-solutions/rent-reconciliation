"""
Shared property metrics helpers.
Single source of truth for occupancy counts, expected income, and arrears depth.
Import from here — never define these inline in route files.
"""
from math import ceil


def get_property_occupancy(conn, property_id):
    """
    Returns occupancy counts for a property in a single query.
    Keys: total, occupied, vacant, office, rentable, occupancy_rate
    """
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'occupied' THEN 1 ELSE 0 END) AS occupied,
            SUM(CASE WHEN status = 'vacant'   THEN 1 ELSE 0 END) AS vacant,
            SUM(CASE WHEN status = 'office'   THEN 1 ELSE 0 END) AS office
        FROM units WHERE property_id = ?
    """, (property_id,)).fetchone()
    total    = row['total']    or 0
    occupied = row['occupied'] or 0
    vacant   = row['vacant']   or 0
    office   = row['office']   or 0
    rentable = total - office
    occupancy_rate = round(occupied / rentable * 100, 1) if rentable > 0 else 0.0
    return {
        'total': total, 'occupied': occupied, 'vacant': vacant,
        'office': office, 'rentable': rentable, 'occupancy_rate': occupancy_rate,
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
