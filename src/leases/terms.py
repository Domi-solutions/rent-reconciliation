"""
Lease terms: month to month (the Kenyan norm — a deposit, then rent each month until notice)
or fixed-term (an end date, often a yearly rent increase and early-exit terms).

Everything stays reactive: lists for management to act on, never automatic texts.

* ending_soon(): fixed leases ending within 60 days with nothing decided yet. Management
  decides: renew (new end date), let it end, or switch to month to month.
* reviews_due(): leases with a yearly increase whose next review falls within 60 days.
  Management confirms the new rent and the month it starts (schedule_rent_change); the tenant
  gets a notice in their portal at once, and the unit's rent changes only when that month's
  charges are generated (apply_due_rent_changes, called by generate_charges).
* for_tenant(): what the tenant's "Your lease" page shows, built from the record.
"""

from datetime import date, datetime

from src.database.db import generate_id

TERM_TYPES = {'periodic': 'Month to month', 'fixed': 'Fixed term'}
DECISIONS = {'renew': 'Renew', 'end': 'Let it end', 'monthly': 'Switch to month to month'}
SOON_DAYS = 60


class TermsError(ValueError):
    """Shown to the admin as-is."""


def _day(value, label):
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        raise TermsError(f'{label}: enter a date.')


def add_months(day, months):
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue


def parse(form, start):
    """The terms fields from a lease form → a dict for the leases row. Raises TermsError."""
    term_type = form.get('term_type') or 'periodic'
    if term_type not in TERM_TYPES:
        raise TermsError('Choose month to month or fixed term.')
    end = None
    if term_type == 'fixed':
        end = _day(form.get('end_date'), 'End date (fixed term)')
        if start and end <= start:
            raise TermsError('The end date must be after the start date.')
    try:
        notice = int(form.get('notice_days') or 30)
    except ValueError:
        raise TermsError('Notice period: enter a number of days.')
    if not 0 <= notice <= 365:
        raise TermsError('Notice period: between 0 and 365 days.')
    pct_raw = (form.get('rent_increase_pct') or '').strip()
    try:
        pct = float(pct_raw) if pct_raw else None
    except ValueError:
        raise TermsError('Rent increase: enter a percentage, e.g. 7.')
    if pct is not None and not 0 < pct <= 50:
        raise TermsError('Rent increase: between 0 and 50 percent, or leave it empty.')
    try:
        every = int(form.get('rent_increase_every_months') or 12)
    except ValueError:
        every = 12
    return {'term_type': term_type, 'end_date': end.isoformat() if end else None, 'notice_days': notice,
            'rent_increase_pct': pct, 'rent_increase_every_months': max(every, 1),
            'early_exit_terms': (form.get('early_exit_terms') or '').strip()[:600] or None}


def save(conn, lease, terms, actor, property_row):
    """Update a lease's terms (recorded with the lease, or added later). Audited."""
    conn.execute("""UPDATE leases SET term_type = ?, end_date = ?, notice_days = ?, rent_increase_pct = ?,
                    rent_increase_every_months = ?, early_exit_terms = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?""",
                 (terms['term_type'], terms['end_date'], terms['notice_days'], terms['rent_increase_pct'],
                  terms['rent_increase_every_months'], terms['early_exit_terms'], lease['id']))
    details = (f"Unit {lease['unit_number']} | {TERM_TYPES[terms['term_type']]}"
               + (f" to {terms['end_date']}" if terms['end_date'] else '')
               + f" | notice {terms['notice_days']} days"
               + (f" | rent +{terms['rent_increase_pct']:g}% every {terms['rent_increase_every_months']} months"
                  if terms['rent_increase_pct'] else ''))
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('lease_terms_set', 'lease', lease['id'], details, actor))
    from src.platform.guardian import platform_log
    platform_log(conn, 'lease_terms_set', 'lease', lease['id'], details,
                 org_id=property_row['organization_id'], property_id=property_row['id'], actor=actor)


# ── Reviews and endings ─────────────────────────────────────────────────────

def next_review(lease, today=None):
    """The next date the lease's yearly increase applies, or None."""
    if not lease.get('rent_increase_pct') or not lease.get('start_date'):
        return None
    today = today or date.today()
    start = datetime.strptime(lease['start_date'][:10], '%Y-%m-%d').date()
    every = lease.get('rent_increase_every_months') or 12
    k = 1
    while add_months(start, every * k) <= today:
        k += 1
    review = add_months(start, every * k)
    end = lease.get('end_date')
    if lease.get('term_type') == 'fixed' and end and review >= datetime.strptime(end[:10], '%Y-%m-%d').date():
        return None
    return review


def _open_leases(conn, property_id):
    return [dict(r) for r in conn.execute("""
        SELECT l.*, u.unit_number, u.monthly_rent AS unit_rent, t.name AS tenant_name
        FROM leases l JOIN units u ON u.id = l.unit_id JOIN tenants t ON t.id = l.tenant_id
        WHERE l.property_id = ? AND t.status = 'active'
    """, (property_id,))]


def ending_soon(conn, property_id, today=None):
    today = today or date.today()
    out = []
    for l in _open_leases(conn, property_id):
        if l['term_type'] != 'fixed' or not l['end_date'] or l['end_decision']:
            continue
        end = datetime.strptime(l['end_date'][:10], '%Y-%m-%d').date()
        days = (end - today).days
        if days <= SOON_DAYS:
            l['days_left'] = days
            out.append(l)
    return sorted(out, key=lambda l: l['end_date'])


def reviews_due(conn, property_id, today=None):
    today = today or date.today()
    out = []
    for l in _open_leases(conn, property_id):
        review = next_review(l, today)
        if not review or (review - today).days > SOON_DAYS:
            continue
        period = review.strftime('%Y-%m')
        if conn.execute("""SELECT 1 FROM lease_rent_changes WHERE lease_id = ? AND effective_period = ?
                           AND cancelled_at IS NULL""", (l['id'], period)).fetchone():
            continue
        current = float(l['unit_rent'] or l['monthly_rent'] or 0)
        l.update(review_date=review, review_period=period, current_rent=current,
                 proposed_rent=round(current * (1 + l['rent_increase_pct'] / 100)))
        out.append(l)
    return sorted(out, key=lambda l: l['review_date'])


def decide_ending(conn, lease, decision, actor, new_end=None):
    if decision not in DECISIONS:
        raise TermsError('Choose what happens when the lease ends.')
    if decision == 'renew':
        end = _day(new_end, 'New end date')
        if end <= datetime.strptime(lease['end_date'][:10], '%Y-%m-%d').date():
            raise TermsError('The new end date must be after the current one.')
        conn.execute("UPDATE leases SET end_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (end.isoformat(), lease['id']))
        details = f"Renewed to {end.isoformat()}"
    elif decision == 'monthly':
        conn.execute("""UPDATE leases SET term_type = 'periodic', end_decision = 'monthly', end_decided_at = CURRENT_TIMESTAMP,
                        end_decided_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (actor, lease['id']))
        details = f"Continues month to month after {lease['end_date']}"
    else:
        conn.execute("""UPDATE leases SET end_decision = 'end', end_decided_at = CURRENT_TIMESTAMP, end_decided_by = ?,
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (actor, lease['id']))
        details = f"Ends {lease['end_date']}; not renewed"
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('lease_end_decided', 'lease', lease['id'], f"Unit {lease['unit_number']} | {details}", actor))
    _notice(conn, lease, 'About your lease', {
        'renew': f"Your lease is renewed. It now ends on {new_end}.",
        'monthly': f"After {lease['end_date']} your lease continues month to month, with one month's notice either way.",
        'end': f"Your lease ends on {lease['end_date']} and will not be renewed. Please talk to management about moving out.",
    }[decision])


def schedule_rent_change(conn, lease, property_row, new_rent, effective_period, actor, reason=None):
    """Confirm a new rent from a month. The tenant is told now; the unit changes when that
    month's charges are generated."""
    try:
        new_rent = float(new_rent)
    except (TypeError, ValueError):
        raise TermsError('New rent: enter an amount.')
    if new_rent <= 0:
        raise TermsError('New rent: enter an amount.')
    try:
        first = datetime.strptime(effective_period + '-01', '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise TermsError('Choose the first month at the new rent.')
    if first.strftime('%Y-%m') <= date.today().strftime('%Y-%m'):
        raise TermsError('Choose a coming month — this month may already be billed.')
    old = float(lease['unit_rent'] if lease.get('unit_rent') is not None else lease['monthly_rent'])
    change_id = generate_id('RENT')
    conn.execute("""INSERT INTO lease_rent_changes (id, lease_id, tenant_id, unit_id, property_id, old_rent, new_rent,
                    effective_period, reason, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (change_id, lease['id'], lease['tenant_id'], lease['unit_id'], property_row['id'], old, new_rent,
                  effective_period, (reason or '').strip() or None, actor))
    details = f"Unit {lease['unit_number']} | rent {old:,.0f} → {new_rent:,.0f} from {first:%B %Y}"
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('rent_change_scheduled', 'lease', lease['id'], details, actor))
    from src.platform.guardian import platform_log
    platform_log(conn, 'rent_change_scheduled', 'lease', lease['id'], details,
                 org_id=property_row['organization_id'], property_id=property_row['id'], actor=actor)
    _notice(conn, lease, f'Your rent from {first:%B %Y}',
            f"From {first:%B %Y} your rent is KES {new_rent:,.0f} a month (it was KES {old:,.0f})"
            + (f", as your lease allows a {lease['rent_increase_pct']:g}% increase each year" if lease.get('rent_increase_pct') else '')
            + ". Questions? Write to management in Messages.")
    return change_id


def apply_due_rent_changes(conn, property_row, period):
    """Called by generate_charges before billing `period`: confirmed rent changes due by then
    are applied to the unit and the lease. Returns how many."""
    rows = conn.execute("""SELECT c.*, u.unit_number FROM lease_rent_changes c JOIN units u ON u.id = c.unit_id
                           WHERE c.property_id = ? AND c.effective_period <= ? AND c.applied_at IS NULL
                           AND c.cancelled_at IS NULL ORDER BY c.effective_period""",
                        (property_row['id'], period)).fetchall()
    for c in rows:
        conn.execute("UPDATE units SET monthly_rent = ? WHERE id = ?", (c['new_rent'], c['unit_id']))
        conn.execute("UPDATE leases SET monthly_rent = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (c['new_rent'], c['lease_id']))
        conn.execute("UPDATE lease_rent_changes SET applied_at = CURRENT_TIMESTAMP WHERE id = ?", (c['id'],))
        conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                     ('rent_change_applied', 'unit', c['unit_id'],
                      f"Unit {c['unit_number']} | rent {c['old_rent']:,.0f} → {c['new_rent']:,.0f} from {c['effective_period']}",
                      'system'))
    return len(rows)


# ── The tenant's view ───────────────────────────────────────────────────────

def for_tenant(conn, tenant_id):
    """The tenant's latest lease with what their "Your lease" page shows, or None."""
    row = conn.execute("""SELECT l.*, u.unit_number, u.monthly_rent AS unit_rent, u.service_charge AS unit_service
                          FROM leases l JOIN units u ON u.id = l.unit_id
                          WHERE l.tenant_id = ? ORDER BY l.created_at DESC LIMIT 1""", (tenant_id,)).fetchone()
    if not row:
        return None
    lease = dict(row)
    lease['next_review'] = next_review(lease)
    lease['upcoming_change'] = conn.execute("""SELECT new_rent, effective_period FROM lease_rent_changes
                                               WHERE lease_id = ? AND applied_at IS NULL AND cancelled_at IS NULL
                                               ORDER BY effective_period LIMIT 1""", (lease['id'],)).fetchone()
    lease['documents'] = [dict(d) for d in conn.execute("""SELECT id, kind, original_name, content_type, created_at
                                                           FROM lease_documents WHERE lease_id = ? AND tenant_visible = 1
                                                           AND kind != 'id_copy' ORDER BY created_at""", (lease['id'],))]
    return lease


def _notice(conn, lease, subject, body):
    """A portal notice to the tenant (no SMS — reactive by default)."""
    conn.execute("""INSERT INTO messages (id, property_id, tenant_id, subject, body, message_type)
                    VALUES (?, ?, ?, ?, ?, 'lease')""",
                 (generate_id('MSG'), lease['property_id'], lease['tenant_id'], subject, body))
