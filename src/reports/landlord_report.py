"""
Landlord report generator - pure function that queries DB and returns structured dict.
No side effects - just data transformation.
"""
import json
from datetime import datetime
from src.database.db import get_connection


def generate_landlord_report(conn, property_id, period_start, period_end):
    """Generate structured report data for a property and date range.
    
    Args:
        conn: Database connection (from get_connection())
        property_id: Property ID
        period_start: Start date (YYYY-MM-DD)
        period_end: End date (YYYY-MM-DD)
    
    Returns:
        dict with report sections: collections, occupancy, arrears, tenant_movement, claims, charges
    """
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not prop:
        raise ValueError(f"Property {property_id} not found")

    report = {
        'property_name': prop['name'],
        'property_id': property_id,
        'period_start': period_start,
        'period_end': period_end,
        'generated_at': datetime.now().isoformat(),
        'collections': {},
        'occupancy': {},
        'arrears': {},
        'tenant_movement': {},
        'claims': {},
        'charges': {},
    }

    # Section 1: Collections in period
    collections_row = conn.execute("""
        SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count
        FROM payments
        WHERE property_id = ? AND payment_date BETWEEN ? AND ?
    """, (property_id, period_start, period_end)).fetchone()
    
    total_verified = float(collections_row['total'])
    payment_count = collections_row['count']

    report['collections'] = {
        'total_verified': total_verified,
        'payment_count': payment_count,
    }

    # Section 2: Occupancy snapshot (current state at time of report)
    total_units = conn.execute(
        "SELECT COUNT(*) FROM units WHERE property_id = ?", (property_id,)
    ).fetchone()[0]
    occupied_units = conn.execute(
        "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'occupied'", (property_id,)
    ).fetchone()[0]
    vacant_units = conn.execute(
        "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'vacant'", (property_id,)
    ).fetchone()[0]
    office_units = conn.execute(
        "SELECT COUNT(*) FROM units WHERE property_id = ? AND status IN ('owner_use', 'office', 'short_term')", (property_id,)
    ).fetchone()[0]

    rentable_units = total_units - office_units
    occupancy_rate = round(occupied_units / rentable_units * 100, 1) if rentable_units > 0 else 0

    # Expected monthly income from occupied units
    expected_row = conn.execute("""
        SELECT COALESCE(SUM(monthly_rent + service_charge), 0) as expected
        FROM units WHERE property_id = ? AND status = 'occupied'
    """, (property_id,)).fetchone()
    expected_income = float(expected_row['expected'])

    vacant_unit_rows = conn.execute("""
        SELECT unit_number, apartment_size, monthly_rent, service_charge
        FROM units WHERE property_id = ? AND status = 'vacant'
        ORDER BY unit_number
    """, (property_id,)).fetchall()

    report['occupancy'] = {
        'total_units': total_units,
        'occupied_units': occupied_units,
        'vacant_units': vacant_units,
        'office_units': office_units,
        'rentable_units': rentable_units,
        'occupancy_rate': occupancy_rate,
        'vacant_unit_details': [{
            'unit_number': r['unit_number'],
            'apartment_size': r['apartment_size'] or '—',
            'monthly_rent': float(r['monthly_rent'] or 0),
            'service_charge': float(r['service_charge'] or 0),
        } for r in vacant_unit_rows],
    }
    report['collections']['expected_income'] = expected_income

    # Section 3: Arrears snapshot (current state at time of report)
    arrears_rows = conn.execute("""
        SELECT
            u.unit_number,
            t.name as tenant_name,
            t.phone as tenant_phone,
            COALESCE(charges.total, 0) as total_charged,
            COALESCE(payments.total, 0) as total_paid,
            COALESCE(charges.total, 0) - COALESCE(payments.total, 0) as balance,
            u.monthly_rent
        FROM units u
        LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
        LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM rent_charges GROUP BY unit_id) charges ON charges.unit_id = u.id
        LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM payments GROUP BY unit_id) payments ON payments.unit_id = u.id
        WHERE u.property_id = ?
          AND COALESCE(charges.total, 0) - COALESCE(payments.total, 0) > 0
        ORDER BY balance DESC
    """, (property_id,)).fetchall()

    arrears_list = []
    for row in arrears_rows:
        balance = float(row['balance'])
        monthly_rent = float(row['monthly_rent']) if row['monthly_rent'] else 0
        months_behind = round(balance / monthly_rent, 1) if monthly_rent > 0 else 0
        
        arrears_list.append({
            'unit_number': row['unit_number'],
            'tenant_name': row['tenant_name'] or 'Vacant',
            'tenant_phone': row['tenant_phone'],
            'balance': balance,
            'months_behind': months_behind,
        })

    total_arrears = sum(a['balance'] for a in arrears_list)
    units_behind = len(arrears_list)
    units_on_time = occupied_units - units_behind

    report['arrears'] = {
        'units_in_arrears': units_behind,
        'total_arrears': total_arrears,
        'units': arrears_list,
    }
    report['occupancy']['units_on_time'] = units_on_time
    report['occupancy']['units_behind'] = units_behind

    # Section 4: Tenant movement in period
    # New tenants (move_in_date in period, or created_at fallback)
    new_tenants = conn.execute("""
        SELECT t.name, u.unit_number, COALESCE(t.move_in_date, date(t.created_at)) as date
        FROM tenants t
        JOIN units u ON t.unit_id = u.id
        WHERE t.property_id = ? AND t.status = 'active'
          AND (t.move_in_date BETWEEN ? AND ?
               OR (t.move_in_date IS NULL AND date(t.created_at) BETWEEN ? AND ?))
    """, (property_id, period_start, period_end, period_start, period_end)).fetchall()

    # Departed tenants
    departed_tenants = conn.execute("""
        SELECT t.name, u.unit_number, COALESCE(t.move_out_date, date(t.created_at)) as date
        FROM tenants t
        LEFT JOIN units u ON t.unit_id = u.id
        WHERE t.property_id = ? AND t.status != 'active'
          AND (t.move_out_date BETWEEN ? AND ?
               OR (t.move_out_date IS NULL AND date(t.created_at) BETWEEN ? AND ?))
    """, (property_id, period_start, period_end, period_start, period_end)).fetchall()

    report['tenant_movement'] = {
        'move_ins': [{'name': t['name'], 'unit_number': t['unit_number'], 'date': t['date']} for t in new_tenants],
        'move_outs': [{'name': t['name'], 'unit_number': t['unit_number'], 'date': t['date']} for t in departed_tenants],
    }

    # Section 5: Claim resolution in period
    claims_row = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END) as verified,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
        FROM payment_claims
        WHERE property_id = ? AND date(created_at) BETWEEN ? AND ?
    """, (property_id, period_start, period_end)).fetchone()

    report['claims'] = {
        'total': claims_row['total'] or 0,
        'verified': claims_row['verified'] or 0,
        'pending': claims_row['pending'] or 0,
    }

    # Section 6: Charges generated in period
    # Get all unique periods in the date range (by charge_type)
    period_start_month = period_start[:7]
    period_end_month = period_end[:7]
    charges_by_type = conn.execute("""
        SELECT charge_type, COUNT(*) as count, SUM(amount) as total
        FROM rent_charges
        WHERE property_id = ? AND period BETWEEN ? AND ?
        GROUP BY charge_type
    """, (property_id, period_start_month, period_end_month)).fetchall()
    
    charges_dict = {}
    for row in charges_by_type:
        charges_dict[row['charge_type']] = {
            'count': row['count'],
            'total': float(row['total']),
        }
    
    # Also get breakdown by period for more detail
    charges_detail = conn.execute("""
        SELECT period, charge_type, COUNT(*) as count, SUM(amount) as total
        FROM rent_charges
        WHERE property_id = ? AND period BETWEEN ? AND ?
        GROUP BY period, charge_type
        ORDER BY period, charge_type
    """, (property_id, period_start_month, period_end_month)).fetchall()
    
    # How much of these charges (for this period) has actually been paid?
    allocations_by_type = conn.execute("""
        SELECT rc.charge_type, SUM(pa.amount) as total
        FROM payment_allocations pa
        JOIN rent_charges rc ON pa.charge_id = rc.id
        WHERE rc.property_id = ? AND rc.period BETWEEN ? AND ?
        GROUP BY rc.charge_type
    """, (property_id, period_start_month, period_end_month)).fetchall()
    
    collected_by_type = {}
    for row in allocations_by_type:
        collected_by_type[row['charge_type']] = float(row['total'])
    
    total_collected_on_period_charges = sum(collected_by_type.values())
    water_total = charges_dict.get('water', {}).get('total', 0.0)
    
    report['charges'] = {
        'by_type': charges_dict,
        'water_total': water_total,
        'detail': [{
            'period': r['period'],
            'charge_type': r['charge_type'],
            'count': r['count'],
            'total': float(r['total']),
        } for r in charges_detail],
        'collected_by_type': collected_by_type,
        'total_collected_on_period_charges': total_collected_on_period_charges,
    }
    
    # Calculate collection rate: how much of this period's billed charges
    # (rent + service + water) has been allocated from payments (regardless of when the payment happened).
    total_charged_in_period = sum(float(r['total']) for r in charges_by_type)
    collection_rate = 0.0
    if total_charged_in_period > 0:
        collection_rate = round((total_collected_on_period_charges / total_charged_in_period) * 100, 1)
    
    report['collection_rate'] = collection_rate
    report['total_charged_in_period'] = total_charged_in_period

    # Additional communication metrics:
    # 1. How this period's payments compare to expected monthly income
    vs_expected_income_pct = 0.0
    if expected_income > 0:
        vs_expected_income_pct = round((total_verified / expected_income) * 100, 1)
    report['collections']['vs_expected_income_pct'] = vs_expected_income_pct

    # 2. How collected money splits between this month's bills and older arrears.
    #    "Settled toward this month's invoices" = total allocated to this period's charges (from any payment).
    #    "Applied to older arrears" = total collected in period minus the above.
    settled_current = min(total_collected_on_period_charges, total_verified)
    applied_arrears = max(0.0, total_verified - settled_current)

    report['collections']['allocation_breakdown'] = {
        'to_current_period_charges': settled_current,
        'to_past_period_charges': applied_arrears,
    }

    return report


def enrich_report_data(report, conn, property_id, period_start, period_end):
    """Back-fill derived fields for reports generated before these fields existed.

    Mutates *report* in place and returns it for convenience.
    Safe to call on reports that already have the fields (no-op).
    """
    collections = report.setdefault('collections', {})
    total_verified = float(collections.get('total_verified', 0))
    expected_income = float(collections.get('expected_income', 0))

    if not collections.get('vs_expected_income_pct') and expected_income > 0:
        collections['vs_expected_income_pct'] = round(
            (total_verified / expected_income) * 100, 1
        )

    needs_breakdown = (
        not collections.get('allocation_breakdown')
        or 'to_future_period_charges' in collections.get('allocation_breakdown', {})
    )
    if needs_breakdown and total_verified > 0:
        period_start_month = period_start[:7]
        period_end_month = period_end[:7]

        alloc_row = conn.execute("""
            SELECT COALESCE(SUM(pa.amount), 0) AS total
            FROM payment_allocations pa
            JOIN rent_charges rc ON pa.charge_id = rc.id
            WHERE rc.property_id = ? AND rc.period BETWEEN ? AND ?
        """, (property_id, period_start_month, period_end_month)).fetchone()

        settled_current = min(float(alloc_row['total']), total_verified)
        applied_arrears = max(0.0, total_verified - settled_current)

        collections['allocation_breakdown'] = {
            'to_current_period_charges': settled_current,
            'to_past_period_charges': applied_arrears,
        }

    occupancy = report.setdefault('occupancy', {})
    if 'vacant_unit_details' not in occupancy:
        vacant_rows = conn.execute("""
            SELECT unit_number, apartment_size, monthly_rent, service_charge
            FROM units WHERE property_id = ? AND status = 'vacant'
            ORDER BY unit_number
        """, (property_id,)).fetchall()
        occupancy['vacant_unit_details'] = [{
            'unit_number': r['unit_number'],
            'apartment_size': r['apartment_size'] or '—',
            'monthly_rent': float(r['monthly_rent'] or 0),
            'service_charge': float(r['service_charge'] or 0),
        } for r in vacant_rows]

    return report
