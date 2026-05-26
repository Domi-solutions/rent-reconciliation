"""Tenant portal: token URL access (no session) + optional persons-based holistic login."""

from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from src.database.db import get_connection, generate_id
from src.payments.daraja import stk_push
from src.utils.phone import normalize_to_e164 as _normalize_phone

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


@tenant_bp.route("/login", methods=["GET", "POST"])
def login():
    """Persons-based login: phone + password → find linked tenant records."""
    error = None
    if request.method == "POST":
        raw_phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        phone_norm = _normalize_phone(raw_phone)

        with get_connection() as conn:
            person = conn.execute(
                "SELECT id, name, password_hash FROM persons WHERE phone = ? OR phone = ?",
                (raw_phone, phone_norm),
            ).fetchone()

        if not person or not person["password_hash"]:
            error = "No Domi account found for this phone number."
        elif not check_password_hash(person["password_hash"], password):
            error = "Incorrect password."
        else:
            with get_connection() as conn:
                tenants = conn.execute(
                    """SELECT t.access_token, t.id, u.unit_number, p.name AS property_name, p.id AS property_id
                       FROM tenants t
                       JOIN units u ON t.unit_id = u.id
                       JOIN properties p ON u.property_id = p.id
                       WHERE t.person_id = ? AND t.status = 'active'
                       ORDER BY p.name, u.unit_number""",
                    (person["id"],),
                ).fetchall()

            if len(tenants) == 0:
                error = "No active tenancy found for this account."
            elif len(tenants) == 1 and tenants[0]["access_token"]:
                return redirect(url_for("tenant.portal", token=tenants[0]["access_token"]))
            else:
                session["tenant_person_id"] = person["id"]
                session["tenant_person_name"] = person["name"]
                return redirect(url_for("tenant.dashboard"))

    return render_template("tenant/login.html", error=error)


@tenant_bp.route("/logout")
def logout():
    """Clear tenant person session."""
    session.pop("tenant_person_id", None)
    session.pop("tenant_person_name", None)
    return redirect(url_for("tenant.login"))


@tenant_bp.route("/dashboard")
def dashboard():
    """Holistic tenant dashboard: all units for the logged-in person."""
    person_id = session.get("tenant_person_id")
    if not person_id:
        return redirect(url_for("tenant.login"))

    from datetime import datetime
    with get_connection() as conn:
        units = conn.execute(
            """SELECT t.id AS tenant_id, t.name AS tenant_name, t.access_token,
                      u.id AS unit_id, u.unit_number,
                      p.name AS property_name, p.id AS property_id,
                      COALESCE(ub.total_charged, 0) AS total_charged,
                      COALESCE(ub.total_paid, 0) AS total_paid,
                      COALESCE(ub.balance, 0) AS balance
               FROM tenants t
               JOIN units u ON t.unit_id = u.id
               JOIN properties p ON u.property_id = p.id
               LEFT JOIN unit_balances ub ON ub.unit_id = u.id
               WHERE t.person_id = ? AND t.status = 'active'
               ORDER BY p.name, u.unit_number""",
            (person_id,),
        ).fetchall()

        unit_details = []
        for row in units:
            last_payment = conn.execute(
                "SELECT amount, payment_date FROM payments WHERE unit_id = ? ORDER BY payment_date DESC LIMIT 1",
                (row["unit_id"],),
            ).fetchone()

            pending = float(conn.execute(
                "SELECT COALESCE(SUM(claimed_amount), 0) FROM payment_claims WHERE unit_id = ? AND status = 'pending'",
                (row["unit_id"],),
            ).fetchone()[0])

            unit_details.append({
                "tenant_id": row["tenant_id"],
                "tenant_name": row["tenant_name"],
                "access_token": row["access_token"],
                "unit_number": row["unit_number"],
                "property_name": row["property_name"],
                "property_id": row["property_id"],
                "total_charged": float(row["total_charged"]),
                "total_paid": float(row["total_paid"]),
                "balance": max(0.0, float(row["balance"]) - pending),
                "pending": pending,
                "last_payment": last_payment,
            })

    return render_template(
        "tenant/dashboard.html",
        units=unit_details,
        person_name=session.get("tenant_person_name", ""),
    )


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

        caretaker = conn.execute("""
            SELECT name, phone FROM caretakers
            WHERE property_id = ? AND phone IS NOT NULL AND TRIM(phone) != ''
            ORDER BY created_at LIMIT 1
        """, (prop['id'],)).fetchone()

    return render_template(
        'tenant/portal.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        total_charged=total_charged,
        total_paid=total_paid_display,
        balance=balance,
        recent_messages=recent_messages,
        caretaker=caretaker,
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
                'status': 'verified',
            })

        # Unverified claims (pending + flagged) — skip refs already in verified payments
        verified_refs = {p['mpesa_ref'] for p in payment_rows if p['mpesa_ref']}
        claim_rows = conn.execute("""
            SELECT mpesa_ref, claimed_amount, status, created_at
            FROM payment_claims
            WHERE unit_id = ? AND status IN ('pending', 'flagged')
            ORDER BY created_at DESC
        """, (unit['id'],)).fetchall()

        for c in claim_rows:
            if c['mpesa_ref'] in verified_refs:
                continue
            payments_with_allocations.append({
                'date': c['created_at'][:10] if c['created_at'] else '',
                'amount': float(c['claimed_amount'] or 0),
                'mpesa_ref': c['mpesa_ref'] or '-',
                'sender_name': '-',
                'allocations': [],
                'status': c['status'],
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


@tenant_bp.route('/<token>/pay', methods=['GET'])
def pay(token):
    """Payment tab — tenant self-reports M-Pesa payment."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        balance_row = conn.execute(
            "SELECT balance FROM unit_balances WHERE unit_id = ?", (unit['id'],)
        ).fetchone()
        balance = float(balance_row['balance']) if balance_row else 0.0

        claims_raw = conn.execute("""
            SELECT pc.id, pc.mpesa_ref, pc.claimed_amount, pc.status, pc.created_at,
                   p.amount AS verified_amount, p.id AS payment_id
            FROM payment_claims pc
            LEFT JOIN payments p ON p.claim_id = pc.id
            WHERE pc.unit_id = ?
            ORDER BY pc.created_at DESC LIMIT 20
        """, (unit['id'],)).fetchall()

        claims = []
        for c in claims_raw:
            allocs = []
            if c['status'] == 'verified' and c['payment_id']:
                allocs = conn.execute("""
                    SELECT pa.amount AS alloc_amount, rc.charge_type, rc.period,
                           pa.charge_settled
                    FROM payment_allocations pa
                    JOIN rent_charges rc ON pa.charge_id = rc.id
                    WHERE pa.payment_id = ?
                    ORDER BY rc.period, rc.charge_type
                """, (c['payment_id'],)).fetchall()
            claims.append({**dict(c), 'allocations': [dict(a) for a in allocs]})

        caretaker = conn.execute("""
            SELECT name, phone FROM caretakers
            WHERE property_id = ? AND phone IS NOT NULL AND TRIM(phone) != ''
            ORDER BY created_at LIMIT 1
        """, (prop['id'],)).fetchone()

    success = request.args.get('success')
    error = request.args.get('error')

    return render_template(
        'tenant/pay.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        active_tab='pay',
        balance=balance,
        claims=claims,
        caretaker=caretaker,
        success=success,
        error=error,
    )


@tenant_bp.route('/<token>/report-payment', methods=['POST'])
def report_payment(token):
    """Tenant self-reports an M-Pesa payment by pasting their confirmation SMS."""
    from src.parsers.sms_parser import parse_mpesa_message
    from src.database.db import allocate_payment

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        message = (request.form.get('mpesa_sms') or '').strip()
        if not message:
            return redirect(url_for('tenant.pay', token=token, error='Please paste your M-Pesa SMS.'))

        parsed = parse_mpesa_message(message)
        if not parsed.get('success'):
            return redirect(url_for('tenant.pay', token=token,
                                    error='Could not read an M-Pesa reference from that message. Please paste the full SMS.'))

        reference = parsed['reference']
        try:
            amount = float(parsed['amount']) if parsed.get('amount') is not None else None
        except (TypeError, ValueError):
            amount = None

        # Duplicate check
        existing = conn.execute(
            "SELECT id FROM payment_claims WHERE property_id = ? AND mpesa_ref = ?",
            (prop['id'], reference),
        ).fetchone()
        if existing:
            return redirect(url_for('tenant.pay', token=token,
                                    error=f'Reference {reference} has already been reported.'))

        _mpesa_ts = parsed.get('timestamp')
        _has_real_ts = not parsed.get('parse_warnings') or not any(
            'Timestamp not found' in w for w in parsed.get('parse_warnings', [])
        )
        mpesa_date = _mpesa_ts.date().isoformat() if (_mpesa_ts and _has_real_ts) else None
        mpesa_period = _mpesa_ts.strftime('%Y-%m') if (_mpesa_ts and _has_real_ts) else None

        claim_id = generate_id('CLM')
        conn.execute("""
            INSERT INTO payment_claims
            (id, property_id, mpesa_ref, unit_id, claimed_amount, raw_message, source, mpesa_date, mpesa_period)
            VALUES (?, ?, ?, ?, ?, ?, 'tenant', ?, ?)
        """, (claim_id, prop['id'], reference, unit['id'], amount, message, mpesa_date, mpesa_period))

        amt_str = f'{amount:,.0f}' if amount is not None else '-'
        unit_number = unit['unit_number']
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('claim_created', 'claim', claim_id,
             f'Ref: {reference} | Unit: {unit_number} | Amount: KES {amt_str} | source: tenant', 'tenant'),
        )

        # At-submission bank check — same logic as caretaker flow
        _org_id = prop['organization_id'] if prop.get('organization_id') else None
        property_id = prop['id']

        if mpesa_period:
            _mp_y, _mp_m = int(mpesa_period[:4]), int(mpesa_period[5:7])
            _mp_start = f"{mpesa_period}-01"
            _mp_end = f"{_mp_y + 1}-01-01" if _mp_m == 12 else f"{_mp_y}-{_mp_m + 1:02d}-01"
            if _org_id:
                _has_bank_data = bool(conn.execute("""
                    SELECT 1 FROM bank_transactions bt
                    JOIN bank_statements bs ON bs.id = bt.statement_id
                    WHERE bs.org_id = ? AND bt.txn_date >= ? AND bt.txn_date < ?
                      AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                """, (_org_id, _mp_start, _mp_end)).fetchone())
            else:
                _has_bank_data = bool(conn.execute("""
                    SELECT 1 FROM bank_transactions bt
                    JOIN bank_statements bs ON bs.id = bt.statement_id
                    JOIN properties p ON p.id = bs.property_id
                    WHERE p.id = ? AND bt.txn_date >= ? AND bt.txn_date < ?
                      AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                """, (property_id, _mp_start, _mp_end)).fetchone())
        else:
            if _org_id:
                _has_bank_data = bool(conn.execute("""
                    SELECT 1 FROM bank_transactions bt
                    JOIN bank_statements bs ON bs.id = bt.statement_id
                    WHERE bs.org_id = ? AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                """, (_org_id,)).fetchone())
            else:
                _has_bank_data = bool(conn.execute("""
                    SELECT 1 FROM bank_transactions bt
                    JOIN bank_statements bs ON bs.id = bt.statement_id
                    JOIN properties p ON p.id = bs.property_id
                    WHERE p.id = ? AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                """, (property_id,)).fetchone())

        _notify = None  # ('verified'|'flagged', sms_body) — sent after commit

        if _has_bank_data:
            if _org_id:
                _bank_txn = conn.execute("""
                    SELECT bt.* FROM bank_transactions bt
                    JOIN bank_statements bs ON bs.id = bt.statement_id
                    WHERE bs.org_id = ? AND bt.mpesa_ref = ? LIMIT 1
                """, (_org_id, reference)).fetchone()
            else:
                _bank_txn = conn.execute("""
                    SELECT bt.* FROM bank_transactions bt
                    JOIN bank_statements bs ON bs.id = bt.statement_id
                    JOIN properties p ON p.id = bs.property_id
                    WHERE p.id = ? AND bt.mpesa_ref = ? LIMIT 1
                """, (property_id, reference)).fetchone()

            if _bank_txn:
                _existing_pay = conn.execute(
                    "SELECT id, claim_id FROM payments WHERE bank_txn_id = ?",
                    (_bank_txn['id'],)
                ).fetchone()
                if _existing_pay:
                    if not _existing_pay['claim_id']:
                        conn.execute("UPDATE payments SET claim_id = ? WHERE id = ?",
                                     (claim_id, _existing_pay['id']))
                    conn.execute(
                        "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (claim_id,)
                    )
                    _pay_id_for_sms = _existing_pay['id']
                else:
                    payment_id = generate_id('PAY')
                    conn.execute("""
                        INSERT INTO payments
                        (id, property_id, unit_id, claim_id, bank_txn_id, statement_id, amount, payment_date, assignment_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto')
                    """, (payment_id, property_id, unit['id'], claim_id,
                          _bank_txn['id'], _bank_txn['statement_id'],
                          _bank_txn['amount'], _bank_txn['txn_date'] or ''))
                    allocate_payment(conn, payment_id, unit['id'], _bank_txn['amount'])
                    conn.execute(
                        "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (claim_id,)
                    )
                    conn.execute(
                        "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                        ('payment_verified', 'payment', payment_id,
                         f'Ref: {reference} | Unit: {unit_number} | KES {amt_str} | auto-verified at tenant submission',
                         'system'),
                    )
                    _pay_id_for_sms = payment_id
                if _bank_txn['ignored']:
                    conn.execute(
                        "UPDATE bank_transactions SET ignored=0, ignored_at=NULL, ignored_reason=NULL WHERE id=?",
                        (_bank_txn['id'],)
                    )

                # Build allocation summary for SMS
                _allocs = conn.execute("""
                    SELECT pa.amount AS alloc_amount, rc.charge_type, rc.period
                    FROM payment_allocations pa
                    JOIN rent_charges rc ON pa.charge_id = rc.id
                    WHERE pa.payment_id = ?
                    ORDER BY rc.period, rc.charge_type
                """, (_pay_id_for_sms,)).fetchall()
                _alloc_lines = ', '.join(
                    f"KES {float(a['alloc_amount']):,.0f} {a['charge_type']} {a['period']}"
                    for a in _allocs
                ) if _allocs else f"KES {float(_bank_txn['amount']):,.0f}"

                _base = (os.environ.get('APP_BASE_URL', request.host_url)).rstrip('/')
                _sc = tenant['short_code'] if 'short_code' in tenant.keys() and tenant['short_code'] else None
                _link = f" View: {_base}/t/{_sc}" if _sc else ''
                _notify = ('verified',
                    f"Hi {tenant['name']}, your payment ref {reference} has been confirmed.\n"
                    f"Applied: {_alloc_lines}.{_link}")
            else:
                # Ref absent — flag claim and flag tenant
                conn.execute("""
                    UPDATE payment_claims
                    SET status = 'flagged', flag_reason = 'ref_not_found', flagged_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (claim_id,))
                conn.execute("UPDATE tenants SET flagged = 1 WHERE id = ?", (tenant['id'],))
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('claim_flagged', 'claim', claim_id,
                     f'Ref: {reference} | Unit: {unit_number} | ref not in bank data | source: tenant', 'system'),
                )
                _notify = ('flagged',
                    f"Hi {tenant['name']}, we could not verify payment ref {reference} in bank records. "
                    "Please contact your caretaker to resolve this.")

    # Send notifications outside the DB transaction
    if _notify and tenant.get('phone'):
        try:
            from src.messaging.delivery import send_sms_async
            send_sms_async([{'phone': tenant['phone']}], _notify[1])
        except Exception:
            pass

    if _notify and _notify[0] == 'verified':
        return redirect(url_for('tenant.pay', token=token,
                                success=f'Payment {reference} confirmed and allocated to your charges.'))
    return redirect(url_for('tenant.pay', token=token,
                            success=f'Payment reference {reference} recorded. We will verify it against bank records within 1–5 days.'))


@tenant_bp.route('/<token>/pay/pin/create', methods=['POST'])
def pay_pin_create(token):
    """Create the 4-digit payment PIN."""
    pin = (request.form.get('pin') or '').strip()
    pin_confirm = (request.form.get('pin_confirm') or '').strip()

    if not pin.isdigit() or len(pin) != 4:
        return redirect(url_for('tenant.pay', token=token, error='PIN must be exactly 4 digits'))
    if pin != pin_confirm:
        return redirect(url_for('tenant.pay', token=token, error='PINs do not match'))

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        existing = conn.execute(
            "SELECT portal_pin_hash FROM tenants WHERE id = ?", (tenant['id'],)
        ).fetchone()
        if existing and existing['portal_pin_hash']:
            return redirect(url_for('tenant.pay', token=token, error='PIN already set'))
        pin_hash = generate_password_hash(pin)
        conn.execute(
            "UPDATE tenants SET portal_pin_hash = ? WHERE id = ?",
            (pin_hash, tenant['id']),
        )

    session[f'pay_auth_{token}'] = True
    return redirect(url_for('tenant.pay', token=token))


@tenant_bp.route('/<token>/pay/pin/verify', methods=['POST'])
def pay_pin_verify(token):
    """Verify the 4-digit payment PIN."""
    pin = (request.form.get('pin') or '').strip()

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        row = conn.execute(
            "SELECT portal_pin_hash FROM tenants WHERE id = ?", (tenant['id'],)
        ).fetchone()
        pin_hash = row['portal_pin_hash'] if row else None

    if not pin_hash or not check_password_hash(pin_hash, pin):
        return redirect(url_for('tenant.pay', token=token, error='Incorrect PIN'))

    session[f'pay_auth_{token}'] = True
    return redirect(url_for('tenant.pay', token=token))


@tenant_bp.route('/<token>/pay/stk', methods=['POST'])
def pay_stk(token):
    """Initiate M-Pesa STK Push. Returns JSON. Requires PIN verified in session."""
    if not session.get(f'pay_auth_{token}'):
        return jsonify({'success': False, 'error': 'PIN verification required'}), 403

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return jsonify({'success': False, 'error': 'Invalid token'}), 400
        tenant, unit, prop = ctx

    phone = (request.form.get('phone') or tenant.get('phone') or '').strip()
    try:
        amount = int(float(request.form.get('amount') or 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        return jsonify({'success': False, 'error': 'Enter a valid amount'})
    if not phone:
        return jsonify({'success': False, 'error': 'Phone number required'})

    description = f"Rent — Unit {unit['unit_number']}"
    result = stk_push(phone, amount, unit['unit_number'], description)

    if result['success']:
        checkout_request_id = result['checkout_request_id']
        # Pre-create payment_transactions record so callback can update it
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM payment_transactions WHERE external_reference = ?",
                (checkout_request_id,),
            ).fetchone()
            if not existing:
                txn_id = generate_id('PTXN')
                conn.execute(
                    """
                    INSERT INTO payment_transactions
                        (id, property_id, unit_id, tenant_id, source,
                         external_reference, phone, amount, processing_status)
                    VALUES (?, ?, ?, ?, 'daraja', ?, ?, ?, 'pending')
                    """,
                    (
                        txn_id,
                        prop['id'],
                        unit['id'],
                        tenant['id'],
                        checkout_request_id,
                        phone,
                        float(amount),
                    ),
                )
        return jsonify({'success': True, 'checkout_request_id': checkout_request_id})

    return jsonify({'success': False, 'error': result.get('error', 'Payment initiation failed')})


@tenant_bp.route('/<token>/pay/status/<checkout_request_id>')
def pay_status(token, checkout_request_id):
    """Poll payment status. Returns JSON {status, message}."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return jsonify({'status': 'error', 'message': 'Invalid token'}), 400

        row = conn.execute(
            """
            SELECT processing_status, error_detail, amount
            FROM payment_transactions
            WHERE external_reference = ?
            """,
            (checkout_request_id,),
        ).fetchone()

    if not row:
        return jsonify({'status': 'not_found', 'message': 'Payment not found'})

    status = row['processing_status']
    if status == 'completed':
        amount_fmt = f"{float(row['amount']):,.0f}"
        return jsonify({
            'status': 'completed',
            'message': f"KES {amount_fmt} confirmed.",
        })
    if status == 'failed':
        return jsonify({
            'status': 'failed',
            'message': row['error_detail'] or 'Payment was not completed',
        })
    return jsonify({'status': 'pending', 'message': 'Waiting for confirmation...'})


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


@tenant_bp.route('/<token>/dispute', methods=['POST'])
def raise_dispute(token):
    """Tenant flags a discrepancy — goes directly to platform, not the agency."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        subject = request.form.get('subject', '').strip() or 'Dispute'
        message = request.form.get('message', '').strip()
        if not message:
            from flask import flash
            flash('Please describe the issue.', 'error')
            return redirect(url_for('tenant.portal', token=token))
        org = conn.execute(
            "SELECT organization_id FROM properties WHERE id = ?", (prop['id'],)
        ).fetchone()
        dispute_id = generate_id('DISP')
        conn.execute(
            """INSERT INTO tenant_disputes
                   (id, tenant_id, property_id, org_id, subject, message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (dispute_id, tenant['id'], prop['id'],
             org['organization_id'] if org else None, subject, message),
        )
    from flask import flash
    flash('Your concern has been recorded and will be reviewed by Domi.', 'success')
    return redirect(url_for('tenant.portal', token=token))
