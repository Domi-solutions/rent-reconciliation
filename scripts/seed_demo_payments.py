"""
Seed realistic dummy payment data for the 4 demo agencies.
Run with: DATABASE_PATH=data/dev.db ./venv/bin/python scripts/seed_demo_payments.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.db import get_connection, generate_id, allocate_payment

# ──────────────────────────────────────────────
# Static tenant/unit data (from existing seed)
# ──────────────────────────────────────────────

PROPS = {
    'PROP-7FE7BEBC': 'Riverside Courts',
    'PROP-A4C07BE5': 'Garden View Apartments',
    'PROP-8FE8E448': 'Parklands Estate',
    'PROP-7680DD9A': 'Westlands Flats',
}

# (unit_id, tenant_name, mpesa_sender, property_id, monthly_rent, service_charge)
UNITS = [
    # Riverside Courts — 15K rent + 2K service = 17K/mo
    ('UNIT-E629A7C6', 'Kariuki Mwangi',   'KARIUKI MWANGI',   'PROP-7FE7BEBC', 15000, 2000),
    ('UNIT-2D75E0E1', 'Fatuma Hassan',    'FATUMA HASSAN',    'PROP-7FE7BEBC', 15000, 2000),
    ('UNIT-9C4B0750', 'David Otieno',     'DAVID OTIENO',     'PROP-7FE7BEBC', 15000, 2000),
    ('UNIT-BCA7840E', 'Grace Njeri',      'GRACE NJERI',      'PROP-7FE7BEBC', 15000, 2000),
    ('UNIT-3F93BA63', 'James Kamau',      'JAMES KAMAU',      'PROP-7FE7BEBC', 15000, 2000),
    ('UNIT-AB38F2A8', 'Xenia Kamau',      'XENIA KAMAU',      'PROP-7FE7BEBC', 15000, 2000),
    # Garden View — 18K + 2.5K = 20.5K/mo
    ('UNIT-6093F3F1', 'Samuel Kipchoge',  'SAMUEL KIPCHOGE',  'PROP-A4C07BE5', 18000, 2500),
    ('UNIT-4A5C1ACC', 'Mary Wanjiku',     'MARY WANJIKU',     'PROP-A4C07BE5', 18000, 2500),
    ('UNIT-931FF789', 'Peter Njoroge',    'PETER NJOROGE',    'PROP-A4C07BE5', 18000, 2500),
    ('UNIT-651FD25A', 'Agnes Auma',       'AGNES AUMA',       'PROP-A4C07BE5', 18000, 2500),
    ('UNIT-89F4C40B', 'Michael Mutua',    'MICHAEL MUTUA',    'PROP-A4C07BE5', 18000, 2500),
    # Parklands Estate — 20K + 3K = 23K/mo
    ('UNIT-AEE0A8EA', 'Hassan Abdi',      'HASSAN ABDI',      'PROP-8FE8E448', 20000, 3000),
    ('UNIT-66021153', 'Lydia Chebet',     'LYDIA CHEBET',     'PROP-8FE8E448', 20000, 3000),
    ('UNIT-758D04C3', 'Robert Ochieng',   'ROBERT OCHIENG',   'PROP-8FE8E448', 20000, 3000),
    ('UNIT-DE1B12CC', 'Esther Wambua',    'ESTHER WAMBUA',    'PROP-8FE8E448', 20000, 3000),
    ('UNIT-54C5D649', 'Felix Nduta',      'FELIX NDUTA',      'PROP-8FE8E448', 20000, 3000),
    ('UNIT-82504DB3', 'Xenia Kamau2',     'XENIA N KAMAU',    'PROP-8FE8E448', 20000, 3000),
    # Westlands Flats — 25K + 3.5K = 28.5K/mo
    ('UNIT-BEA43686', 'Joyce Akinyi',     'JOYCE AKINYI',     'PROP-7680DD9A', 25000, 3500),
    ('UNIT-19FCFCD3', 'Charles Mwaura',   'CHARLES MWAURA',   'PROP-7680DD9A', 25000, 3500),
    ('UNIT-79C1E011', 'Diana Wachira',    'DIANA WACHIRA',    'PROP-7680DD9A', 25000, 3500),
    ('UNIT-BB5321A3', 'Eric Onyango',     'ERIC ONYANGO',     'PROP-7680DD9A', 25000, 3500),
    ('UNIT-5A96D97C', 'Beatrice Korir',   'BEATRICE KORIR',   'PROP-7680DD9A', 25000, 3500),
]

PERIODS = ['2026-03', '2026-04', '2026-05']
DUE_DATES = {'2026-03': '2026-03-05', '2026-04': '2026-04-05', '2026-05': '2026-05-05'}

# ──────────────────────────────────────────────
# Payment scenario per unit
# Keys: 'mar', 'apr', 'may' → amount paid (0 = nothing, or int)
# ──────────────────────────────────────────────
# Scenarios:
#   Full payer: pays rent+service each month on time
#   Late payer: pays full but on day 8-10
#   Missed: 0 that month
#   Partial: partial amount
#   Catchup: pays double to clear arrears

MONTHLY = lambda u: u[4] + u[5]  # rent + service

def scenarios():
    data = {}
    for u in UNITS:
        uid, name, _, prop, rent, svc = u
        mo = rent + svc
        data[uid] = {
            'mar': mo, 'apr': mo, 'may': mo,
            'mar_date': None, 'apr_date': None, 'may_date': None,
        }

    # Overrides — realistic mix

    # A4 Grace Njeri: missed April, caught up in May (paid double)
    data['UNIT-BCA7840E']['apr'] = 0
    data['UNIT-BCA7840E']['may'] = 34000  # 17K Apr + 17K May

    # A6 Xenia Kamau: partial April (10K of 17K), nothing May yet
    data['UNIT-AB38F2A8']['apr'] = 10000
    data['UNIT-AB38F2A8']['may'] = 0

    # A3 David Otieno: hasn't paid May yet
    data['UNIT-9C4B0750']['may'] = 0

    # B4 Agnes Auma: partial April (15K of 20.5K), nothing May yet
    data['UNIT-651FD25A']['apr'] = 15000
    data['UNIT-651FD25A']['may'] = 0

    # B3 Peter Njoroge: hasn't paid May yet
    data['UNIT-931FF789']['may'] = 0

    # C4 Esther Wambua: partial April (18K of 23K), nothing May yet
    data['UNIT-DE1B12CC']['apr'] = 18000
    data['UNIT-DE1B12CC']['may'] = 0

    # C3 Robert Ochieng: hasn't paid May
    data['UNIT-758D04C3']['may'] = 0

    # C6 Xenia N Kamau: hasn't paid May (pending claim - handled separately)
    data['UNIT-82504DB3']['may'] = 0

    # D4 Eric Onyango: paid 20K in March (short), paid 20K in April (trying to clear), nothing May
    data['UNIT-BB5321A3']['mar'] = 20000
    data['UNIT-BB5321A3']['apr'] = 20000
    data['UNIT-BB5321A3']['may'] = 0

    # D5 Beatrice Korir: hasn't paid May
    data['UNIT-5A96D97C']['may'] = 0

    # Late payers — same amount but later dates
    # A2 Fatuma Hassan: pays on day 8-9
    data['UNIT-2D75E0E1']['mar_date'] = '2026-03-08'
    data['UNIT-2D75E0E1']['apr_date'] = '2026-04-09'
    data['UNIT-2D75E0E1']['may_date'] = '2026-05-09'

    # B2 Mary Wanjiku: pays on day 10-11
    data['UNIT-4A5C1ACC']['mar_date'] = '2026-03-10'
    data['UNIT-4A5C1ACC']['apr_date'] = '2026-04-11'
    data['UNIT-4A5C1ACC']['may_date'] = '2026-05-11'

    # C5 Felix Nduta: pays on day 7-8
    data['UNIT-54C5D649']['mar_date'] = '2026-03-07'
    data['UNIT-54C5D649']['apr_date'] = '2026-04-08'
    data['UNIT-54C5D649']['may_date'] = '2026-05-07'

    # D2 Charles Mwaura: pays on day 9
    data['UNIT-19FCFCD3']['mar_date'] = '2026-03-09'
    data['UNIT-19FCFCD3']['apr_date'] = '2026-04-09'
    data['UNIT-19FCFCD3']['may_date'] = '2026-05-10'

    return data


PAYMENT_DATES = {
    # default on-time payers: day 3-5
    'UNIT-E629A7C6': {'mar': '2026-03-03', 'apr': '2026-04-03', 'may': '2026-05-04'},
    'UNIT-3F93BA63': {'mar': '2026-03-04', 'apr': '2026-04-04', 'may': '2026-05-05'},
    'UNIT-BCA7840E': {'mar': '2026-03-05', 'may': '2026-05-07'},  # apr skipped
    'UNIT-6093F3F1': {'mar': '2026-03-02', 'apr': '2026-04-03', 'may': '2026-05-03'},
    'UNIT-89F4C40B': {'mar': '2026-03-04', 'apr': '2026-04-04', 'may': '2026-05-04'},
    'UNIT-AEE0A8EA': {'mar': '2026-03-03', 'apr': '2026-04-03', 'may': '2026-05-03'},
    'UNIT-66021153': {'mar': '2026-03-02', 'apr': '2026-04-02', 'may': '2026-05-02'},
    'UNIT-BEA43686': {'mar': '2026-03-04', 'apr': '2026-04-04', 'may': '2026-05-04'},
    'UNIT-79C1E011': {'mar': '2026-03-04', 'apr': '2026-04-04', 'may': '2026-05-05'},
}


def get_pay_date(unit_id, month, unit_sc):
    override = unit_sc.get(f'{month}_date')
    if override:
        return override
    if unit_id in PAYMENT_DATES and month in PAYMENT_DATES[unit_id]:
        return PAYMENT_DATES[unit_id][month]
    year_mo = {'mar': '2026-03', 'apr': '2026-04', 'may': '2026-05'}[month]
    return f'{year_mo}-05'


import random
import string

random.seed(42)

def mpesa_ref():
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=10))


def run():
    sc = scenarios()

    with get_connection() as conn:
        # ── 1. Rent charges for all occupied units, 3 months ──
        print("Creating rent charges...")
        for uid, name, sender, prop_id, rent, svc in UNITS:
            for period in PERIODS:
                due = DUE_DATES[period]
                for charge_type, amount in [('rent', rent), ('service', svc)]:
                    cid = generate_id('CHRG')
                    try:
                        conn.execute(
                            "INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount, due_date) VALUES (?,?,?,?,?,?,?)",
                            (cid, prop_id, uid, period, charge_type, amount, due)
                        )
                    except Exception:
                        pass  # already exists

        # ── 2. Bank statements — one per property per month ──
        print("Creating bank statements...")
        stmt_ids = {}  # (prop_id, period) → statement_id
        for prop_id in PROPS:
            for period, p_start, p_end in [
                ('mar', '2026-03-01', '2026-03-31'),
                ('apr', '2026-04-01', '2026-04-30'),
                ('may', '2026-05-01', '2026-05-12'),
            ]:
                sid = generate_id('STMT')
                stmt_ids[(prop_id, period)] = sid
                fname = f"{PROPS[prop_id].replace(' ', '_')}_{period.upper()}_2026.pdf"
                conn.execute("""
                    INSERT INTO bank_statements
                    (id, property_id, filename, file_path, period_start, period_end, status, uploaded_at)
                    VALUES (?,?,?,?,?,?,'processed',datetime('now'))
                """, (sid, prop_id, fname, f'uploads/{fname}', p_start, p_end))

        # ── 3. Bank transactions + payments + allocations ──
        print("Creating payments...")
        for uid, name, sender, prop_id, rent, svc in UNITS:
            unit_sc = sc[uid]
            for month, period in [('mar', '2026-03'), ('apr', '2026-04'), ('may', '2026-05')]:
                amount = unit_sc[month]
                if not amount:
                    continue

                pay_date = get_pay_date(uid, month, unit_sc)
                ref = mpesa_ref()
                stmt_key = (prop_id, month)
                stmt_id = stmt_ids[stmt_key]

                # bank_transaction
                txn_id = generate_id('TXN')
                conn.execute("""
                    INSERT INTO bank_transactions
                    (id, statement_id, mpesa_ref, amount, txn_type, sender_name, txn_date, raw_text, unit_hint)
                    VALUES (?,?,?,?,'PAYBILL_CREDIT',?,?,?,?)
                """, (txn_id, stmt_id, ref, amount, sender, pay_date,
                      f"Confirmed. {sender} sent KES {amount:,.0f} on {pay_date}. Ref: {ref}.",
                      None))

                # payment
                pay_id = generate_id('PAY')
                conn.execute("""
                    INSERT INTO payments
                    (id, property_id, unit_id, bank_txn_id, statement_id, amount, payment_date,
                     assignment_type, assignment_reason, assigned_by, source)
                    VALUES (?,?,?,?,?,?,?,'bank_match','Verified from bank statement','system','mpesa')
                """, (pay_id, prop_id, uid, txn_id, stmt_id, amount, pay_date))

                # FIFO allocation
                allocate_payment(conn, pay_id, uid, amount)

        # ── 4. One pending claim for C6 Xenia N Kamau (May, unverified) ──
        print("Creating pending claim...")
        claim_ref = mpesa_ref()
        claim_id = generate_id('CLM')
        conn.execute("""
            INSERT INTO payment_claims
            (id, property_id, mpesa_ref, unit_id, claimed_amount, raw_message, source, status)
            VALUES (?,?,'""" + claim_ref + """','UNIT-82504DB3',23000,
            'Confirmed. KES23,000.00 sent to DOMI PAYBILL on 8/5/26 at 2:34 PM. Ref: """ + claim_ref + """. Your M-PESA balance is KES1,450.00.',
            'sms','pending')
        """, (claim_id, 'PROP-8FE8E448'))

        print("\nDone. Summary:")
        print(" Charges:", conn.execute("SELECT COUNT(*) FROM rent_charges").fetchone()[0])
        print(" Statements:", conn.execute("SELECT COUNT(*) FROM bank_statements").fetchone()[0])
        print(" Transactions:", conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0])
        print(" Payments:", conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0])
        print(" Allocations:", conn.execute("SELECT COUNT(*) FROM payment_allocations").fetchone()[0])
        print(" Claims:", conn.execute("SELECT COUNT(*) FROM payment_claims").fetchone()[0])


if __name__ == '__main__':
    run()
