"""Money out — expenses and payments to the owner, and the cash book they feed.

Who does what:
  * The admin records an expense (already paid, or approved to pay later), or a
    payment to the owner.
  * The caretaker submits an expense with a receipt photo; it is only a request
    until the admin approves it. If the caretaker paid out of pocket, they are
    paid back after approval (the admin marks it paid, with how and a reference).
  * Nothing is edited or deleted: a wrong entry is voided with a reason.

Cash book (per property):  collected − paid out = balance held.
"Collected" is rent matched to bank / M-Pesa records (payments); "paid out" is
expenses with status 'paid' (owner payouts included, shown separately).
"""

import calendar
import os
from datetime import date

from src.database.db import generate_id, get_db_path

CATEGORIES = (
    ('repairs', 'Repairs & maintenance'),
    ('wages', 'Caretaker / staff wages'),
    ('water', 'Water bill'),
    ('electricity', 'Electricity (common areas)'),
    ('security', 'Security'),
    ('cleaning', 'Cleaning'),
    ('garbage', 'Garbage collection'),
    ('owner_payout', 'Paid to owner'),
    ('other', 'Other'),
)
CATEGORY_LABELS = dict(CATEGORIES)
METHODS = (('bank', 'Bank transfer'), ('mpesa', 'M-Pesa'), ('cash', 'Cash'), ('cheque', 'Cheque'), ('other', 'Other'))
STATUS_LABELS = {'submitted': 'Waiting for approval', 'approved': 'Approved — not yet paid',
                 'paid': 'Paid', 'rejected': 'Rejected', 'void': 'Voided'}
MAX_RECEIPTS = 3


class ExpenseError(ValueError):
    pass


def receipts_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(get_db_path())), 'expense_receipts')
    os.makedirs(d, exist_ok=True)
    return d


def _amount(raw):
    try:
        v = round(float(str(raw or '').replace(',', '').strip()), 2)
    except ValueError:
        raise ExpenseError('Amount: enter a number.')
    if v <= 0:
        raise ExpenseError('Amount must be more than 0.')
    return v


def _day(raw):
    try:
        return date.fromisoformat((raw or '').strip()).isoformat()
    except ValueError:
        raise ExpenseError('Choose the date of the expense.')


def _audit(conn, action, expense_id, details, actor):
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 (action, 'expense', expense_id, details, actor))


def add_receipts(conn, expense_id, uploads):
    from src.leases.intake import store_document
    saved = rejected = 0
    for up in [u for u in uploads if u and u.filename][:MAX_RECEIPTS]:
        stored = store_document(up, receipts_dir())
        if not stored:
            rejected += 1
            continue
        name, ctype, size = stored
        conn.execute("""INSERT INTO expense_receipts (id, expense_id, file_name, content_type, original_name, size_bytes)
                        VALUES (?, ?, ?, ?, ?, ?)""", (generate_id('ERCP'), expense_id, name, ctype, up.filename[:200], size))
        saved += 1
    return saved, rejected


def record(conn, prop, form, actor, by='admin', caretaker_id=None, uploads=()):
    """Admin: record an expense (paid now, or approved to pay later). Caretaker: submit one for approval.
    Returns (expense_id, receipts_saved, receipts_rejected)."""
    category = form.get('category')
    if category not in CATEGORY_LABELS:
        raise ExpenseError('Choose what the expense was for.')
    if by == 'caretaker' and category == 'owner_payout':
        raise ExpenseError('Payments to the owner are recorded by management.')
    amount = _amount(form.get('amount'))
    day = _day(form.get('expense_date'))
    description = (form.get('description') or '').strip()
    if len(description) < 3:
        raise ExpenseError('Say what it was for (e.g. "replaced tap in 3B").')
    paid_to = (form.get('paid_to') or '').strip() or None

    if by == 'caretaker':
        status, reimburse = 'submitted', 1 if form.get('paid_myself') == '1' else 0
    else:
        status, reimburse = ('paid' if form.get('already_paid') == '1' else 'approved'), 0

    eid = generate_id('EXP')
    conn.execute("""
        INSERT INTO expenses (id, org_id, property_id, category, amount, expense_date, paid_to, description,
                              status, submitted_by_type, submitted_by, caretaker_id, reimburse_caretaker,
                              decided_by, decided_at, paid_by, paid_at, paid_method, paid_reference)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (eid, prop['organization_id'], prop['id'], category, amount, day, paid_to, description, status, by, actor,
          caretaker_id, reimburse,
          actor if by == 'admin' else None, date.today().isoformat() if by == 'admin' else None,
          actor if status == 'paid' else None, date.today().isoformat() if status == 'paid' else None,
          (form.get('paid_method') or None) if status == 'paid' else None,
          ((form.get('paid_reference') or '').strip() or None) if status == 'paid' else None))
    saved, rejected = add_receipts(conn, eid, uploads)
    _audit(conn, 'expense_recorded' if by == 'admin' else 'expense_submitted', eid,
           f"{CATEGORY_LABELS[category]} | KES {amount:,.0f} | {day} | {description[:60]} | {STATUS_LABELS[status]}",
           actor)
    return eid, saved, rejected


def get(conn, expense_id, property_id):
    e = conn.execute("""
        SELECT e.*, c.name AS caretaker_name FROM expenses e
        LEFT JOIN caretakers c ON c.id = e.caretaker_id
        WHERE e.id = ? AND e.property_id = ?
    """, (expense_id, property_id)).fetchone()
    if not e:
        return None
    d = dict(e)
    d['category_label'] = CATEGORY_LABELS.get(d['category'], d['category'])
    d['status_label'] = STATUS_LABELS.get(d['status'], d['status'])
    d['receipts'] = [dict(r) for r in conn.execute(
        "SELECT * FROM expense_receipts WHERE expense_id = ? ORDER BY created_at", (expense_id,))]
    return d


def decide(conn, expense, approve, note, actor):
    if expense['status'] != 'submitted':
        raise ExpenseError('Only an expense waiting for approval can be approved or rejected.')
    note = (note or '').strip()
    if not approve and len(note) < 3:
        raise ExpenseError('Say why it is rejected — the caretaker sees this.')
    status = 'approved' if approve else 'rejected'
    conn.execute("""UPDATE expenses SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP, decision_note = ?
                    WHERE id = ?""", (status, actor, note or None, expense['id']))
    _audit(conn, 'expense_approved' if approve else 'expense_rejected', expense['id'],
           f"KES {expense['amount']:,.0f} | {expense['description'][:60]}" + (f" | {note}" if note else ''), actor)


def mark_paid(conn, expense, method, reference, actor):
    if expense['status'] != 'approved':
        raise ExpenseError('Only an approved expense can be marked paid.')
    if method not in dict(METHODS):
        raise ExpenseError('Choose how it was paid.')
    conn.execute("""UPDATE expenses SET status = 'paid', paid_by = ?, paid_at = CURRENT_TIMESTAMP, paid_method = ?,
                    paid_reference = ? WHERE id = ?""", (actor, method, (reference or '').strip() or None, expense['id']))
    who = (f" to caretaker {expense.get('caretaker_name') or ''}" if expense['reimburse_caretaker'] else '')
    _audit(conn, 'expense_paid', expense['id'],
           f"KES {expense['amount']:,.0f}{who} | {dict(METHODS)[method]}" + (f" | ref {reference}" if reference else ''), actor)


def void(conn, expense, reason, actor):
    if expense['status'] in ('void', 'rejected'):
        raise ExpenseError('This expense is already closed.')
    reason = (reason or '').strip()
    if len(reason) < 5:
        raise ExpenseError('Say why it is being voided.')
    conn.execute("UPDATE expenses SET status = 'void', decision_note = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP "
                 "WHERE id = ?", (reason, actor, expense['id']))
    _audit(conn, 'expense_voided', expense['id'], f"KES {expense['amount']:,.0f} | {reason}", actor)


def listing(conn, property_id, status=None, month=None, caretaker_id=None):
    sql = """SELECT e.*, c.name AS caretaker_name,
                    (SELECT COUNT(*) FROM expense_receipts r WHERE r.expense_id = e.id) AS receipt_count
             FROM expenses e LEFT JOIN caretakers c ON c.id = e.caretaker_id WHERE e.property_id = ?"""
    args = [property_id]
    if status:
        sql += " AND e.status = ?"; args.append(status)
    if month:
        sql += " AND substr(e.expense_date, 1, 7) = ?"; args.append(month)
    if caretaker_id:
        sql += " AND e.caretaker_id = ?"; args.append(caretaker_id)
    sql += """ ORDER BY CASE e.status WHEN 'submitted' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                        e.expense_date DESC, e.created_at DESC"""
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d['category_label'] = CATEGORY_LABELS.get(d['category'], d['category'])
        d['status_label'] = STATUS_LABELS.get(d['status'], d['status'])
        out.append(d)
    return out


def waiting_count(conn, property_id):
    return conn.execute("SELECT COUNT(*) FROM expenses WHERE property_id = ? AND status = 'submitted'",
                        (property_id,)).fetchone()[0]


def _month_bounds(month):
    y, m = map(int, month.split('-'))
    return f'{month}-01', f'{month}-{calendar.monthrange(y, m)[1]:02d}'


def cash_book(conn, property_id, months=6, today=None):
    """Month by month: collected, paid out by category, net; plus all-time balance held."""
    today = today or date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f'{y:04d}-{m:02d}')
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    rows = []
    for key in keys:
        start, end = _month_bounds(key)
        collected = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM payments
                                    WHERE property_id = ? AND substr(payment_date, 1, 10) BETWEEN ? AND ?""",
                                 (property_id, start, end)).fetchone()[0]
        by_cat = {r['category']: r['total'] for r in conn.execute("""
            SELECT category, SUM(amount) AS total FROM expenses
            WHERE property_id = ? AND status = 'paid' AND expense_date BETWEEN ? AND ? GROUP BY category
        """, (property_id, start, end))}
        owner = float(by_cat.pop('owner_payout', 0) or 0)
        spent = float(sum(by_cat.values()))
        rows.append({'month': key, 'label': f"{calendar.month_abbr[int(key[5:])]} {key[:4]}",
                     'collected': float(collected), 'expenses': spent, 'owner_payouts': owner,
                     'net': float(collected) - spent - owner,
                     'by_category': [(CATEGORY_LABELS.get(k, k), float(v)) for k, v in
                                     sorted(by_cat.items(), key=lambda kv: -kv[1])]})
    totals = conn.execute("""
        SELECT (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = :p) AS collected,
               (SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE property_id = :p AND status = 'paid'
                  AND category != 'owner_payout') AS expenses,
               (SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE property_id = :p AND status = 'paid'
                  AND category = 'owner_payout') AS owner_payouts,
               (SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE property_id = :p AND status = 'approved') AS owed_out,
               (SELECT COUNT(*) FROM expenses WHERE property_id = :p AND status = 'submitted') AS waiting
    """, {'p': property_id}).fetchone()
    t = dict(totals)
    t['held'] = float(t['collected']) - float(t['expenses']) - float(t['owner_payouts'])
    return rows, t
