"""Tenant portal: read-only access via shareable token URL. No session or password."""

from flask import Blueprint, render_template, request, redirect, url_for
from src.database.db import get_connection, generate_id

tenant_bp = Blueprint('tenant', __name__, url_prefix='/tenant')


def _get_tenant_by_token(conn, token):
    """Return (tenant, unit, property) row dicts or None if invalid/revoked."""
    if not token or not token.strip():
        return None
    row = conn.execute("""
        SELECT t.id AS tenant_id, t.name AS tenant_name, t.phone AS tenant_phone, t.unit_id,
               u.id AS unit_id, u.unit_number, u.property_id,
               p.id AS property_id, p.name AS property_name
        FROM tenants t
        JOIN units u ON t.unit_id = u.id
        JOIN properties p ON u.property_id = p.id
        WHERE t.access_token = ? AND t.unit_id IS NOT NULL
    """, (token.strip(),)).fetchone()
    if not row:
        return None
    tenant = {'id': row['tenant_id'], 'name': row['tenant_name'], 'phone': row['tenant_phone'], 'unit_id': row['unit_id']}
    unit = {'id': row['unit_id'], 'unit_number': row['unit_number'], 'property_id': row['property_id']}
    prop = {'id': row['property_id'], 'name': row['property_name']}
    return (tenant, unit, prop)


@tenant_bp.route('/<token>')
def portal(token):
    """Balance summary and recent messages."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        balance_row = conn.execute("""
            SELECT total_charged, total_paid, balance
            FROM unit_balances WHERE unit_id = ?
        """, (unit['id'],)).fetchone()
        total_charged = float(balance_row['total_charged']) if balance_row else 0
        total_paid = float(balance_row['total_paid']) if balance_row else 0
        verified_balance = float(balance_row['balance']) if balance_row else 0

        # Sum pending claims not yet verified — subtract from balance for tenant view only
        pending_claims_total = conn.execute("""
            SELECT COALESCE(SUM(claimed_amount), 0) AS total
            FROM payment_claims
            WHERE unit_id = ? AND status = 'pending'
        """, (unit['id'],)).fetchone()['total']
        pending_claims_total = float(pending_claims_total)

        # Tenant-facing balance: subtract pending claims from verified outstanding
        # Floor at 0 — don't show negative balance if claims exceed outstanding
        balance = max(0.0, verified_balance - pending_claims_total)
        total_paid_display = total_paid + pending_claims_total

        recent_messages = conn.execute("""
            SELECT id, subject, body, message_type, read_at, created_at
            FROM messages WHERE tenant_id = ?
            ORDER BY created_at DESC LIMIT 10
        """, (tenant['id'],)).fetchall()

    return render_template(
        'tenant/portal.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        total_charged=total_charged,
        total_paid=total_paid_display,   # includes pending claims
        balance=balance,                 # adjusted for pending claims
        recent_messages=recent_messages,
    )


@tenant_bp.route('/<token>/charges')
def charges(token):
    """All charges by period with paid/unpaid status per charge."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        charge_rows = conn.execute("""
            SELECT rc.id, rc.period, rc.charge_type, rc.amount, rc.due_date,
                   COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa WHERE pa.charge_id = rc.id), 0) AS paid
            FROM rent_charges rc
            WHERE rc.unit_id = ?
            ORDER BY rc.period DESC, rc.charge_type
        """, (unit['id'],)).fetchall()

        raw_by_period = {}
        for r in charge_rows:
            period = r['period']
            if period not in raw_by_period:
                raw_by_period[period] = []
            amt = float(r['amount'])
            paid = float(r['paid'])
            raw_by_period[period].append({
                'charge_type': r['charge_type'],
                'amount': amt,
                'paid': paid,
                'outstanding': amt - paid,
                'due_date': r['due_date'],
            })

        charges_by_period = {}
        for period, items in raw_by_period.items():
            period_total = sum(c['amount'] for c in items)
            period_paid = sum(c['paid'] for c in items)
            period_outstanding = sum(c['outstanding'] for c in items)
            charges_by_period[period] = {
                'charges': items,
                'period_total': period_total,
                'period_paid': period_paid,
                'period_outstanding': period_outstanding,
            }

    return render_template(
        'tenant/charges.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        charges_by_period=charges_by_period,
    )


@tenant_bp.route('/<token>/payments')
def payments(token):
    """Payment history with M-Pesa ref and allocation trail."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        # Verified payments with allocations
        payment_rows = conn.execute("""
            SELECT p.id, p.amount, p.payment_date,
                   bt.mpesa_ref, bt.sender_name
            FROM payments p
            JOIN bank_transactions bt ON p.bank_txn_id = bt.id
            WHERE p.unit_id = ?
            ORDER BY p.payment_date DESC, p.id
        """, (unit['id'],)).fetchall()

        payments_with_allocations = []
        for p in payment_rows:
            allocs = conn.execute("""
                SELECT pa.amount AS alloc_amount, rc.charge_type, rc.period
                FROM payment_allocations pa
                JOIN rent_charges rc ON pa.charge_id = rc.id
                WHERE pa.payment_id = ?
                ORDER BY rc.created_at ASC
            """, (p['id'],)).fetchall()
            payments_with_allocations.append({
                'date': p['payment_date'],
                'amount': float(p['amount']),
                'mpesa_ref': p['mpesa_ref'] or '-',
                'sender_name': p['sender_name'] or '-',
                'allocations': allocs,
            })

        # Pending claims — exclude any already verified (matched to a payment)
        verified_refs = {p['mpesa_ref'] for p in payment_rows if p['mpesa_ref']}
        claim_rows = conn.execute("""
            SELECT mpesa_ref, claimed_amount, created_at
            FROM payment_claims
            WHERE unit_id = ? AND status = 'pending'
            ORDER BY created_at DESC
        """, (unit['id'],)).fetchall()

        for c in claim_rows:
            if c['mpesa_ref'] in verified_refs:
                continue  # Already showing as a verified payment, skip
            payments_with_allocations.append({
                'date': c['created_at'][:10],
                'amount': float(c['claimed_amount']),
                'mpesa_ref': c['mpesa_ref'] or '-',
                'sender_name': '-',
                'allocations': [],
            })

        # Sort combined list by date descending
        payments_with_allocations.sort(key=lambda x: x['date'], reverse=True)

    return render_template(
        'tenant/payments.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        payments_with_allocations=payments_with_allocations,
    )


@tenant_bp.route('/<token>/messages')
def messages(token):
    """Full message inbox; mark unread as read on view."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        message_list = conn.execute("""
            SELECT id, subject, body, message_type, read_at, created_at
            FROM messages WHERE tenant_id = ?
            ORDER BY created_at DESC
        """, (tenant['id'],)).fetchall()

        conn.execute(
            "UPDATE messages SET read_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND read_at IS NULL",
            (tenant['id'],),
        )

    return render_template(
        'tenant/messages.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        messages=message_list,
    )


@tenant_bp.route('/<token>/maintenance')
def maintenance(token):
    """List tenant-raised maintenance issues with inline submission form."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        issues = conn.execute(
            """
            SELECT id, category, title, description, status, created_at, resolved_at
            FROM maintenance_issues
            WHERE raised_by_tenant_id = ?
            ORDER BY created_at DESC
            """,
            (tenant['id'],),
        ).fetchall()

    return render_template(
        'tenant/maintenance.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        issues=issues,
    )


@tenant_bp.route('/<token>/maintenance/new', methods=['POST'])
def new_maintenance(token):
    """Submit a new maintenance issue from the tenant portal."""
    category = (request.form.get('category') or 'general').strip() or 'general'
    title = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()

    if not title:
        # Title is required; rely on frontend required attribute in practice.
        return redirect(url_for('tenant.maintenance', token=token))

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        issue_id = generate_id('MAINT')
        conn.execute(
            """
            INSERT INTO maintenance_issues
                (id, property_id, unit_id, source, raised_by_tenant_id, category, title, description)
            VALUES
                (?, ?, ?, 'tenant', ?, ?, ?, ?)
            """,
            (issue_id, prop['id'], unit['id'], tenant['id'], category, title, description),
        )

    return redirect(url_for('tenant.maintenance', token=token))
