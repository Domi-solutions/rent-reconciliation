"""
Database module for rent reconciliation.
SQLite with schema in schema.sql; raw SQL via get_connection().
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager


def get_db_path():
    """Path to SQLite database file. Configurable via DATABASE_PATH env var."""
    return os.environ.get(
        'DATABASE_PATH',
        os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'rent.db'),
    )


@contextmanager
def get_connection():
    """Get database connection with foreign keys enabled. Commits on success, rolls back on error."""
    conn = sqlite3.connect(get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """Create data dir if needed and initialize database with schema.sql."""
    db_path = get_db_path()
    data_dir = os.path.dirname(db_path)
    os.makedirs(data_dir, exist_ok=True)

    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, 'r') as f:
        schema = f.read()

    with get_connection() as conn:
        conn.executescript(schema)

    print("Database initialized.")


def generate_id(prefix):
    """Generate unique ID with prefix (e.g. PROP-A1B2C3D4)."""
    short_uuid = uuid.uuid4().hex[:8].upper()
    return f"{prefix}-{short_uuid}"


def migrate_add_charge_type():
    """Add charge_type to rent_charges. Recreates table to change UNIQUE constraint.
    Idempotent - safe to call on every startup."""
    import shutil
    with get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(rent_charges)").fetchall()]
        if 'charge_type' in columns:
            return  # Already migrated

        # Back up database
        db_path = get_db_path()
        shutil.copy2(db_path, db_path + '.backup_before_charge_type')

        try:
            # Clean up any leftover from a failed previous attempt
            conn.execute("DROP TABLE IF EXISTS rent_charges_new")
            conn.execute("DROP VIEW IF EXISTS unit_balances")

            conn.execute("""
                CREATE TABLE rent_charges_new (
                    id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    charge_type TEXT NOT NULL DEFAULT 'rent',
                    amount DECIMAL(10,2) NOT NULL,
                    due_date DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (property_id) REFERENCES properties(id),
                    FOREIGN KEY (unit_id) REFERENCES units(id),
                    UNIQUE (unit_id, period, charge_type)
                )
            """)

            conn.execute("""
                INSERT INTO rent_charges_new (id, property_id, unit_id, period, charge_type, amount, due_date, created_at)
                SELECT id, property_id, unit_id, period, 'rent', amount, due_date, created_at
                FROM rent_charges
            """)

            conn.execute("DROP TABLE rent_charges")
            conn.execute("ALTER TABLE rent_charges_new RENAME TO rent_charges")

            # Recreate the unit_balances view (same definition as schema.sql)
            conn.execute("""
                CREATE VIEW IF NOT EXISTS unit_balances AS
                SELECT
                    u.id AS unit_id,
                    u.property_id,
                    u.unit_number,
                    u.monthly_rent,
                    t.id AS tenant_id,
                    t.name AS tenant_name,
                    t.phone AS tenant_phone,
                    COALESCE(charges.total, 0) AS total_charged,
                    COALESCE(payments_sum.total, 0) AS total_paid,
                    COALESCE(charges.total, 0) - COALESCE(payments_sum.total, 0) AS balance
                FROM units u
                LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
                LEFT JOIN (
                    SELECT unit_id, SUM(amount) AS total
                    FROM rent_charges
                    GROUP BY unit_id
                ) charges ON u.id = charges.unit_id
                LEFT JOIN (
                    SELECT unit_id, SUM(amount) AS total
                    FROM payments
                    GROUP BY unit_id
                ) payments_sum ON u.id = payments_sum.unit_id
            """)
        except Exception:
            conn.execute("DROP TABLE IF EXISTS rent_charges_new")
            raise

        print("Migration complete: charge_type added to rent_charges.")


def migrate_add_apartment_size():
    """Add apartment_size column to units if not present."""
    with get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(units)").fetchall()]
        if 'apartment_size' not in columns:
            conn.execute("ALTER TABLE units ADD COLUMN apartment_size TEXT DEFAULT ''")
            print("Migration complete: apartment_size added to units.")


def migrate_add_payment_allocations():
    """Create payment_allocations table if not exists."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_allocations (
                id TEXT PRIMARY KEY,
                payment_id TEXT NOT NULL,
                charge_id TEXT NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE,
                FOREIGN KEY (charge_id) REFERENCES rent_charges(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alloc_payment ON payment_allocations(payment_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alloc_charge ON payment_allocations(charge_id)")


def allocate_payment(conn, payment_id, unit_id, amount):
    """
    Allocate a payment across outstanding charges for a unit using FIFO (oldest first).
    Creates payment_allocation records. Returns list of allocation dicts.
    """
    charges = conn.execute("""
        SELECT rc.id, rc.amount, rc.period, rc.charge_type,
               COALESCE(alloc.allocated, 0) as already_allocated
        FROM rent_charges rc
        LEFT JOIN (
            SELECT charge_id, SUM(amount) as allocated
            FROM payment_allocations
            GROUP BY charge_id
        ) alloc ON alloc.charge_id = rc.id
        WHERE rc.unit_id = ?
        ORDER BY rc.created_at ASC
    """, (unit_id,)).fetchall()

    remaining = float(amount)
    allocations = []

    for charge in charges:
        if remaining <= 0:
            break
        outstanding = float(charge['amount']) - float(charge['already_allocated'])
        if outstanding <= 0:
            continue

        alloc_amount = min(remaining, outstanding)
        alloc_id = generate_id('ALLOC')
        conn.execute(
            "INSERT INTO payment_allocations (id, payment_id, charge_id, amount) VALUES (?, ?, ?, ?)",
            (alloc_id, payment_id, charge['id'], alloc_amount)
        )
        allocations.append({
            'charge_id': charge['id'],
            'charge_type': charge['charge_type'],
            'period': charge['period'],
            'allocated': alloc_amount,
            'charge_settled': alloc_amount >= outstanding,
        })
        remaining -= alloc_amount

    return allocations


def migrate_allocate_existing_payments():
    """Create allocation records for any payments that don't have them yet. Idempotent."""
    with get_connection() as conn:
        unallocated = conn.execute("""
            SELECT p.id, p.unit_id, p.amount
            FROM payments p
            WHERE p.id NOT IN (SELECT DISTINCT payment_id FROM payment_allocations)
            ORDER BY p.payment_date ASC
        """).fetchall()
        if unallocated:
            for p in unallocated:
                allocate_payment(conn, p['id'], p['unit_id'], p['amount'])
            print(f"Retroactive allocation: processed {len(unallocated)} payments.")


def migrate_add_status_changed_at():
    """Add status_changed_at to units. Backfill with created_at. Idempotent."""
    with get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(units)").fetchall()]
        if 'status_changed_at' not in columns:
            conn.execute("ALTER TABLE units ADD COLUMN status_changed_at TIMESTAMP")
            conn.execute("UPDATE units SET status_changed_at = created_at")
            print("Migration complete: status_changed_at added to units.")


def migrate_add_landlord_reports():
    """Create report_settings and landlord_reports tables. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_settings (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL UNIQUE,
                frequency TEXT DEFAULT 'monthly',
                send_day INTEGER DEFAULT 1,
                recipient_email TEXT,
                recipient_phone TEXT,
                enabled INTEGER DEFAULT 0,
                last_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS landlord_reports (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                report_type TEXT DEFAULT 'manual',
                report_data TEXT NOT NULL,
                sent_via TEXT,
                sent_to TEXT,
                sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_property ON landlord_reports(property_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_created ON landlord_reports(created_at)")
        print("Migration complete: landlord_reports tables created.")


def migrate_add_tenant_access_token():
    """Add access_token column and index on tenants for portal links. Idempotent."""
    with get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(tenants)").fetchall()]
        if 'access_token' not in columns:
            conn.execute("ALTER TABLE tenants ADD COLUMN access_token TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_access_token ON tenants(access_token)")
        print("Migration complete: access_token added to tenants.")


def migrate_add_owners():
    """Add owners table, owner_id to properties, seed default owner from existing data. Idempotent."""
    import secrets
    with get_connection() as conn:
        # 1. Create owners table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS owners (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                password_hash TEXT,
                access_token TEXT UNIQUE,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_owner_token ON owners(access_token)")

        # 2. Add owner_id column to properties (idempotent)
        prop_columns = [row[1] for row in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if 'owner_id' not in prop_columns:
            conn.execute("ALTER TABLE properties ADD COLUMN owner_id TEXT REFERENCES owners(id)")

        # 3. Seed default owner if none exist yet
        owner_count = conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0]
        if owner_count == 0:
            first_prop = conn.execute(
                "SELECT owner_name, owner_phone, owner_email FROM properties WHERE status = 'active' LIMIT 1"
            ).fetchone()
            owner_id = secrets.token_hex(8)
            owner_name = (first_prop['owner_name'] if first_prop and first_prop['owner_name'] else 'Property Owner')
            owner_phone = first_prop['owner_phone'] if first_prop else None
            owner_email = first_prop['owner_email'] if first_prop else None
            conn.execute(
                "INSERT INTO owners (id, name, phone, email) VALUES (?, ?, ?, ?)",
                (owner_id, owner_name, owner_phone, owner_email),
            )
            # Assign all unowned properties to this default owner
            conn.execute("UPDATE properties SET owner_id = ? WHERE owner_id IS NULL", (owner_id,))
            print(f"Migration: created default owner '{owner_name}' (id={owner_id}), assigned all properties.")
        else:
            print("Migration migrate_add_owners: owners already exist, skipping seed.")

        print("Migration complete: owners table ready.")


def migrate_add_messaging():
    """Create messaging-related tables (messages, message_templates, reminder_settings). Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                batch_id TEXT,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                message_type TEXT NOT NULL,
                delivery_channel TEXT NOT NULL DEFAULT 'portal',
                delivery_status TEXT NOT NULL DEFAULT 'delivered',
                read_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_property ON messages(property_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_tenant ON messages(tenant_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_batch ON messages(batch_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_templates (
                id TEXT PRIMARY KEY,
                property_id TEXT,
                template_key TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (property_id, template_key),
                FOREIGN KEY (property_id) REFERENCES properties(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminder_settings (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                template_key TEXT NOT NULL,
                days_before_due INTEGER NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (property_id, template_key),
                FOREIGN KEY (property_id) REFERENCES properties(id)
            )
        """)

        # Seed system defaults (property_id IS NULL) if none exist yet
        existing = conn.execute(
            "SELECT COUNT(*) FROM message_templates WHERE property_id IS NULL"
        ).fetchone()[0]
        if existing == 0:
            from datetime import datetime as _dt  # local import to avoid polluting module namespace
            now_str = _dt.utcnow().isoformat(timespec='seconds')
            templates = [
                (
                    'rent_due_10d',
                    'Rent reminder — due in 10 days',
                    'Hi {tenant_name}, this is a polite reminder that rent is due in 10 days on {due_date}.\n\n'
                    'Monthly rent: KES {monthly_rent}\n'
                    'Paid to date: KES {total_paid}\n'
                    'Outstanding: KES {balance}\n\n'
                    'Please ensure the outstanding amount is settled before the due date.\n\n'
                    'Kind regards.'
                ),
                (
                    'rent_due_5d',
                    'Rent reminder — due in 5 days',
                    'Hi {tenant_name}, a gentle reminder that rent is due in 5 days on {due_date}.\n\n'
                    'Monthly rent: KES {monthly_rent}\n'
                    'Paid to date: KES {total_paid}\n'
                    'Outstanding: KES {balance}\n\n'
                    'Kindly settle the outstanding balance before {due_date}.\n\n'
                    'Kind regards.'
                ),
                (
                    'rent_due_today',
                    'Rent due today — {due_date}',
                    'Hi {tenant_name}, today ({due_date}) is the rent due date.\n\n'
                    'Monthly rent: KES {monthly_rent}\n'
                    'Paid to date: KES {total_paid}\n'
                    'Outstanding: KES {balance}\n\n'
                    'Kindly settle the outstanding balance at your earliest convenience.\n\n'
                    'Kind regards.'
                ),
                (
                    'water_cutoff',
                    'Water charges recorded — Unit {unit_number}',
                    'Hi {tenant_name}, water charges for {month} have been recorded for unit {unit_number}.\n\n'
                    'Water charge: KES {amount}\n'
                    'Total outstanding: KES {balance}\n\n'
                    'Kindly settle any outstanding balance.\n\n'
                    'Kind regards.'
                ),
                (
                    'custom_broadcast',
                    'Notice — {unit_number}',
                    'Hi {tenant_name},\n\n'
                    'Monthly rent: KES {monthly_rent}\n'
                    'Paid to date: KES {total_paid}\n'
                    'Outstanding: KES {balance}\n\n'
                    'Kind regards.'
                ),
            ]
            for key, subject, body in templates:
                template_id = generate_id('MTPL')
                conn.execute(
                    """
                    INSERT INTO message_templates (id, property_id, template_key, subject, body, enabled, created_at, updated_at)
                    VALUES (?, NULL, ?, ?, ?, 1, ?, ?)
                    """,
                    (template_id, key, subject, body, now_str, now_str),
                )
            print("Migration complete: messaging tables created and default templates seeded.")
        else:
            print("Migration complete: messaging tables ensured; default templates already present.")


def migrate_set_rent_charge_due_dates():
    """Backfill due_date for existing rent_charges based on period (YYYY-MM -> 5th of next month). Idempotent."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, period FROM rent_charges WHERE due_date IS NULL AND period LIKE '____-__'"
        ).fetchall()
        updated = 0
        for row in rows:
            period = row['period']
            try:
                year_str, month_str = period.split('-', 1)
                year = int(year_str)
                month = int(month_str)
                if month < 1 or month > 12:
                    continue
            except Exception:
                continue
            if month == 12:
                due_year = year + 1
                due_month = 1
            else:
                due_year = year
                due_month = month + 1
            due_date = f"{due_year:04d}-{due_month:02d}-05"
            conn.execute(
                "UPDATE rent_charges SET due_date = ? WHERE id = ?",
                (due_date, row['id']),
            )
            updated += 1
        if updated:
            print(f"Migration complete: due_date set for {updated} rent_charges.")


def migrate_update_template_wording():
    """Update system default message templates to use friendly personal wording. Idempotent."""
    with get_connection() as conn:
        # If rent_due_10d already uses the new wording, assume all templates are updated
        existing = conn.execute(
            "SELECT subject FROM message_templates WHERE property_id IS NULL AND template_key = 'rent_due_10d'"
        ).fetchone()
        if existing and existing['subject'].startswith('Rent reminder'):
            return

        updates = [
            (
                'rent_due_10d',
                'Rent reminder — due in 10 days',
                'Hi {tenant_name}, this is a polite reminder that rent is due in 10 days on {due_date}.\n\n'
                'Monthly rent: KES {monthly_rent}\n'
                'Paid to date: KES {total_paid}\n'
                'Outstanding: KES {balance}\n\n'
                'Please ensure the outstanding amount is settled before the due date.\n\n'
                'Kind regards.'
            ),
            (
                'rent_due_5d',
                'Rent reminder — due in 5 days',
                'Hi {tenant_name}, a gentle reminder that rent is due in 5 days on {due_date}.\n\n'
                'Monthly rent: KES {monthly_rent}\n'
                'Paid to date: KES {total_paid}\n'
                'Outstanding: KES {balance}\n\n'
                'Kindly settle the outstanding balance before {due_date}.\n\n'
                'Kind regards.'
            ),
            (
                'rent_due_today',
                'Rent due today — {due_date}',
                'Hi {tenant_name}, today ({due_date}) is the rent due date.\n\n'
                'Monthly rent: KES {monthly_rent}\n'
                'Paid to date: KES {total_paid}\n'
                'Outstanding: KES {balance}\n\n'
                'Kindly settle the outstanding balance at your earliest convenience.\n\n'
                'Kind regards.'
            ),
            (
                'water_cutoff',
                'Water charges recorded — Unit {unit_number}',
                'Hi {tenant_name}, water charges for {month} have been recorded for unit {unit_number}.\n\n'
                'Water charge: KES {amount}\n'
                'Total outstanding: KES {balance}\n\n'
                'Kindly settle any outstanding balance.\n\n'
                'Kind regards.'
            ),
            (
                'custom_broadcast',
                'Notice — {unit_number}',
                'Hi {tenant_name},\n\n'
                'Monthly rent: KES {monthly_rent}\n'
                'Paid to date: KES {total_paid}\n'
                'Outstanding: KES {balance}\n\n'
                'Kind regards.'
            ),
        ]
        for key, subject, body in updates:
            conn.execute(
                "UPDATE message_templates SET subject = ?, body = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE property_id IS NULL AND template_key = ?",
                (subject, body, key),
            )
        print("Migration complete: template wording updated.")


def migrate_add_unit_hint():
    """Add unit_hint column to bank_transactions. Idempotent."""
    with get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(bank_transactions)").fetchall()]
        if 'unit_hint' in columns:
            return
        conn.execute("ALTER TABLE bank_transactions ADD COLUMN unit_hint TEXT")
        print("Migration complete: unit_hint added to bank_transactions.")


def migrate_add_maintenance():
    """Create maintenance_issues table for tracking property/unit issues. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_issues (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                unit_id TEXT,
                source TEXT NOT NULL,
                raised_by_tenant_id TEXT,
                category TEXT NOT NULL DEFAULT 'general',
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                resolved_at TIMESTAMP,
                resolved_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id),
                FOREIGN KEY (unit_id) REFERENCES units(id),
                FOREIGN KEY (raised_by_tenant_id) REFERENCES tenants(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maint_property ON maintenance_issues(property_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maint_status ON maintenance_issues(status)")
        print("Migration complete: maintenance_issues table ready.")


def migrate_add_property_owners():
    """Create property_owners M:M junction table and migrate existing properties.owner_id. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS property_owners (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(id),
                owner_id TEXT NOT NULL REFERENCES owners(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (property_id, owner_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pown_property ON property_owners(property_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pown_owner ON property_owners(owner_id)")

        rows = conn.execute(
            "SELECT id AS property_id, owner_id FROM properties WHERE owner_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
                (row['property_id'], row['owner_id'])
            ).fetchone()
            if not exists:
                jid = generate_id('POWN')
                conn.execute(
                    "INSERT INTO property_owners (id, property_id, owner_id) VALUES (?, ?, ?)",
                    (jid, row['property_id'], row['owner_id'])
                )
        print("Migration complete: property_owners junction table ready.")


def migrate_add_rent_due_day():
    """Add rent_due_day (day of month for rent due) to properties. Default 5. Idempotent."""
    with get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if 'rent_due_day' not in columns:
            conn.execute("ALTER TABLE properties ADD COLUMN rent_due_day INTEGER DEFAULT 5")
            print("Migration complete: rent_due_day added to properties.")
        else:
            print("Migration complete: rent_due_day already present.")


def migrate_add_reminder_schedules():
    """Add reminder_schedules table (flexible multi-trigger reminders) + rent_due_reminder template. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminder_schedules (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(id),
                label TEXT NOT NULL DEFAULT 'Rent reminder',
                template_key TEXT NOT NULL DEFAULT 'rent_due_reminder',
                days_before_due INTEGER NOT NULL,
                send_to TEXT NOT NULL DEFAULT 'arrears',
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rsch_property ON reminder_schedules(property_id)")

        exists = conn.execute(
            "SELECT 1 FROM message_templates WHERE property_id IS NULL AND template_key = 'rent_due_reminder'"
        ).fetchone()
        if not exists:
            from datetime import datetime as _dt
            tmpl_id = generate_id('MTPL')
            now_iso = _dt.utcnow().isoformat()
            conn.execute("""
                INSERT INTO message_templates
                    (id, property_id, template_key, subject, body, enabled, created_at, updated_at)
                VALUES (?, NULL, 'rent_due_reminder',
                    'Rent reminder — due on {due_date}',
                    'Hi {tenant_name}, this is a reminder that your rent is due on {due_date}.\n\nMonthly rent: KES {monthly_rent}\nPaid to date: KES {total_paid}\nOutstanding: KES {balance}\n\nKindly settle the outstanding balance before {due_date}.\n\nKind regards.',
                    1, ?, ?)
            """, (tmpl_id, now_iso, now_iso))
        print("Migration complete: reminder_schedules table and rent_due_reminder template ready.")


def migrate_add_sms_delivery():
    """Add delivered_at column to messages table for SMS/WhatsApp delivery tracking. Idempotent."""
    with get_connection() as conn:
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN delivered_at TEXT")
            print("Migration complete: messages.delivered_at column added.")
        except Exception:
            pass  # Column already exists


def migrate_add_template_body():
    """Add template_body column to messages — stores original unsubstituted broadcast template. Idempotent."""
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
        if 'template_body' not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN template_body TEXT")
            print("Migration complete: messages.template_body column added.")
        else:
            print("Migration complete: messages.template_body already present.")


def migrate_add_owner_messages():
    """Create owner_messages table for storing portal notifications sent to property owners. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS owner_messages (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                template_body TEXT,
                message_type TEXT NOT NULL DEFAULT 'notification',
                channel TEXT,
                recipient_count INTEGER,
                read_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id),
                FOREIGN KEY (owner_id) REFERENCES owners(id)
            )
        """)
        # Add columns to existing tables if missing (idempotent)
        existing = [r[1] for r in conn.execute("PRAGMA table_info(owner_messages)").fetchall()]
        for col, typedef in [('template_body', 'TEXT'), ('channel', 'TEXT'), ('recipient_count', 'INTEGER'), ('sent_by', 'TEXT')]:
            if col not in existing:
                conn.execute(f"ALTER TABLE owner_messages ADD COLUMN {col} {typedef}")
        print("Migration complete: owner_messages table ready.")


def migrate_add_caretakers():
    """Create caretakers table for named caretaker accounts per property. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS caretakers (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(id),
                name TEXT NOT NULL,
                phone TEXT,
                password_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_caretakers_property ON caretakers(property_id)")
        print("Migration complete: caretakers table ready.")


def migrate_add_balance_snapshots():
    """Create balance_snapshots table for daily unit snapshots. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS balance_snapshots (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(id),
                unit_id TEXT NOT NULL REFERENCES units(id),
                snapshot_date TEXT NOT NULL,
                balance REAL NOT NULL,
                total_charged REAL NOT NULL,
                total_paid REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (unit_id, snapshot_date)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_balance_snapshots_property_date "
            "ON balance_snapshots(property_id, snapshot_date)"
        )
        print("Migration complete: balance_snapshots table ready.")
