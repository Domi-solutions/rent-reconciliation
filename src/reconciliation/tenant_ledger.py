"""
Per-tenant ledger review and rebuild.

Every charge, payment and claim carries the tenant it belongs to (stamped by
migrate_add_ledger_tenant_id). This module shows where that attribution needs a
human, and repairs payment allocations made before it existed — when a new
tenant's payment could settle the previous occupant's unpaid months.

Read-only except reallocate_unit(), which the caller must invoke explicitly.
"""

from src.database.db import allocate_payment


def unattributed_rows(conn, property_id=None):
    """Charges, payments and claims no tenant could be matched to.

    Usually a payment made while the unit was empty, or rows from a tenant who
    left without a move-out date being recorded. Each needs a person to decide.
    """
    scope = "AND x.property_id = ?" if property_id else ""
    args = (property_id,) if property_id else ()
    charges = conn.execute(f"""
        SELECT x.id, u.unit_number, x.period, x.charge_type, x.amount
        FROM rent_charges x JOIN units u ON u.id = x.unit_id
        WHERE x.tenant_id IS NULL {scope}
        ORDER BY u.unit_number, x.period
    """, args).fetchall()
    payments = conn.execute(f"""
        SELECT x.id, u.unit_number, x.payment_date, x.amount, x.assignment_type
        FROM payments x JOIN units u ON u.id = x.unit_id
        WHERE x.tenant_id IS NULL {scope}
        ORDER BY u.unit_number, x.payment_date
    """, args).fetchall()
    claims = conn.execute(f"""
        SELECT x.id, u.unit_number, x.created_at, x.claimed_amount, x.status
        FROM payment_claims x JOIN units u ON u.id = x.unit_id
        WHERE x.tenant_id IS NULL {scope}
        ORDER BY u.unit_number, x.created_at
    """, args).fetchall()
    return {
        'charges': [dict(r) for r in charges],
        'payments': [dict(r) for r in payments],
        'claims': [dict(r) for r in claims],
    }


def cross_tenant_allocations(conn, property_id=None):
    """Allocations where one tenant's payment settled another tenant's charge."""
    scope = "AND p.property_id = ?" if property_id else ""
    args = (property_id,) if property_id else ()
    rows = conn.execute(f"""
        SELECT pa.id, p.unit_id, u.unit_number, p.id AS payment_id, p.payment_date,
               pa.amount, rc.period, rc.charge_type,
               pt.name AS payer_name, ct.name AS charge_owner_name
        FROM payment_allocations pa
        JOIN payments p ON p.id = pa.payment_id
        JOIN rent_charges rc ON rc.id = pa.charge_id
        JOIN units u ON u.id = p.unit_id
        LEFT JOIN tenants pt ON pt.id = p.tenant_id
        LEFT JOIN tenants ct ON ct.id = rc.tenant_id
        WHERE p.tenant_id IS NOT rc.tenant_id {scope}
        ORDER BY u.unit_number, p.payment_date
    """, args).fetchall()
    return [dict(r) for r in rows]


def balance_changes(conn, property_id=None):
    """Active tenants whose balance differs between the old move-in-date rule and
    the per-tenant ledger. Each difference is a row that moved between people."""
    scope = "AND t.property_id = ?" if property_id else ""
    args = (property_id,) if property_id else ()
    rows = conn.execute(f"""
        SELECT t.id AS tenant_id, t.name, u.unit_number, t.move_in_date,
               COALESCE((SELECT SUM(rc.amount) FROM rent_charges rc
                         WHERE rc.unit_id = t.unit_id
                           AND rc.period >= COALESCE(substr(t.move_in_date, 1, 7), '0000-00')), 0)
             - COALESCE((SELECT SUM(p.amount) FROM payments p
                         WHERE p.unit_id = t.unit_id
                           AND p.payment_date >= COALESCE(t.move_in_date, '0000-00-00')), 0)
               AS old_balance,
               tb.balance AS new_balance
        FROM tenants t
        JOIN units u ON u.id = t.unit_id
        JOIN tenant_balances tb ON tb.tenant_id = t.id
        WHERE t.status = 'active' {scope}
        ORDER BY u.unit_number
    """, args).fetchall()
    return [dict(r) for r in rows if abs(float(r['old_balance']) - float(r['new_balance'])) >= 0.01]


def departed_balances(conn, property_id=None):
    """Former tenants who still owe, or are owed, money on the ledger."""
    scope = "AND t.property_id = ?" if property_id else ""
    args = (property_id,) if property_id else ()
    rows = conn.execute(f"""
        SELECT t.id AS tenant_id, t.name, t.phone, t.status, t.move_out_date,
               u.unit_number, tb.balance
        FROM tenant_balances tb
        JOIN tenants t ON t.id = tb.tenant_id
        JOIN units u ON u.id = t.unit_id
        WHERE t.status != 'active' AND ABS(tb.balance) >= 0.01 {scope}
        ORDER BY tb.balance DESC
    """, args).fetchall()
    return [dict(r) for r in rows]


def reallocate_unit(conn, unit_id):
    """Redo every allocation on a unit so each payment settles only its own
    tenant's charges, oldest payment first. Returns the number of payments.

    Allocations are derived data — the payments and charges themselves are not
    touched, so balances do not change; only which charge each shilling settled.
    """
    payments = conn.execute("""
        SELECT id, amount FROM payments
        WHERE unit_id = ?
        ORDER BY payment_date, created_at
    """, (unit_id,)).fetchall()
    conn.execute("""
        DELETE FROM payment_allocations
        WHERE payment_id IN (SELECT id FROM payments WHERE unit_id = ?)
    """, (unit_id,))
    for p in payments:
        allocate_payment(conn, p['id'], unit_id, p['amount'])
    return len(payments)
