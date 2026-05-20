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

        # 3. Seed default owner only when migrating from a legacy DB that had owner fields on properties
        owner_count = conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0]
        if owner_count == 0:
            prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
            has_legacy_owner = 'owner_name' in prop_cols
            if has_legacy_owner:
                first_prop = conn.execute(
                    "SELECT owner_name, owner_phone, owner_email FROM properties WHERE status = 'active' AND owner_name IS NOT NULL LIMIT 1"
                ).fetchone()
                if first_prop and first_prop['owner_name']:
                    owner_id = secrets.token_hex(8)
                    conn.execute(
                        "INSERT INTO owners (id, name, phone, email) VALUES (?, ?, ?, ?)",
                        (owner_id, first_prop['owner_name'], first_prop['owner_phone'], first_prop['owner_email']),
                    )
                    conn.execute("UPDATE properties SET owner_id = ? WHERE owner_id IS NULL", (owner_id,))
                    print(f"Migration: created default owner '{first_prop['owner_name']}' (id={owner_id}), assigned all properties.")
                    return
            print("Migration migrate_add_owners: fresh install, no owner seed needed.")
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


def migrate_add_inbound_messages():
    """Create inbound_messages table for async inbound processing. Idempotent."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_messages (
                id TEXT PRIMARY KEY,
                property_id TEXT,
                sender_phone TEXT NOT NULL,
                sender_role TEXT,
                sender_entity_id TEXT,
                raw_body TEXT NOT NULL,
                channel TEXT NOT NULL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                classified_intent TEXT,
                confidence REAL,
                action_taken TEXT,
                response_sent TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbound_messages_processed "
            "ON inbound_messages(processed_at)"
        )
        print("Migration complete: inbound_messages table ready.")


def migrate_add_inbound_sessions():
    """Create inbound_sessions state table. Idempotent."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_sessions (
                phone TEXT PRIMARY KEY,
                property_id TEXT,
                last_intent TEXT,
                awaiting_confirmation TEXT,
                context_json TEXT,
                expires_at TIMESTAMP
            )
            """
        )
        print("Migration complete: inbound_sessions table ready.")


def migrate_add_checkin_responses():
    """Create checkin_responses table. Idempotent."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_responses (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                property_id TEXT NOT NULL,
                period TEXT NOT NULL,
                numeric_response INTEGER,
                free_text TEXT,
                classified_category TEXT,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkin_property_period "
            "ON checkin_responses(property_id, period)"
        )
        print("Migration complete: checkin_responses table ready.")


def migrate_add_payment_transactions():
    """Phase G: payment_transactions, disbursements, payments.source, management_fee_rate,
    portal_pin_hash, and make payments.bank_txn_id nullable for Daraja payments. Idempotent."""
    with get_connection() as conn:
        # 1. payment_transactions — raw Daraja/Pesapal callbacks before processing
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id TEXT PRIMARY KEY,
                property_id TEXT,
                unit_id TEXT,
                tenant_id TEXT,
                source TEXT NOT NULL,
                external_reference TEXT UNIQUE,
                phone TEXT,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'KES',
                raw_callback TEXT,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processing_status TEXT DEFAULT 'pending',
                processed_at TIMESTAMP,
                error_detail TEXT,
                payment_id TEXT REFERENCES payments(id),
                FOREIGN KEY (property_id) REFERENCES properties(id),
                FOREIGN KEY (unit_id) REFERENCES units(id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ptxn_status ON payment_transactions(processing_status)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ptxn_ext_ref ON payment_transactions(external_reference)")

        # 2. disbursements — monthly landlord payouts
        conn.execute("""
            CREATE TABLE IF NOT EXISTS disbursements (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL REFERENCES properties(id),
                period TEXT NOT NULL,
                total_collected REAL NOT NULL,
                fee_rate REAL NOT NULL,
                fee_amount REAL NOT NULL,
                net_amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                method TEXT,
                recipient_account TEXT,
                daraja_transaction_id TEXT,
                disbursed_at TIMESTAMP,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_disb_property_period ON disbursements(property_id, period)")

        # 3. management_fee_rate on properties
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if 'management_fee_rate' not in prop_cols:
            conn.execute("ALTER TABLE properties ADD COLUMN management_fee_rate REAL DEFAULT 0.08")

        # 4. portal_pin_hash on tenants
        tenant_cols = [r[1] for r in conn.execute("PRAGMA table_info(tenants)").fetchall()]
        if 'portal_pin_hash' not in tenant_cols:
            conn.execute("ALTER TABLE tenants ADD COLUMN portal_pin_hash TEXT")

        # 5. source column on payments (distinguishes daraja/pesapal/manual origin)
        payment_cols = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(payments)").fetchall()}
        if 'source' not in payment_cols:
            conn.execute("ALTER TABLE payments ADD COLUMN source TEXT DEFAULT 'manual'")
            payment_cols['source'] = 0  # just added, now nullable

        # 6. Make bank_txn_id and statement_id nullable in payments so Daraja payments
        #    (which have no bank statement) can be recorded without FK violation.
        #    SQLite requires a full table rebuild to drop NOT NULL constraints.
        if payment_cols.get('bank_txn_id') == 1:  # 1 = NOT NULL, needs rebuilding
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DROP VIEW IF EXISTS unit_balances")
            conn.execute("DROP TABLE IF EXISTS payments_new")
            conn.execute("""
                CREATE TABLE payments_new (
                    id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    claim_id TEXT,
                    bank_txn_id TEXT,
                    statement_id TEXT,
                    amount DECIMAL(10,2) NOT NULL,
                    payment_date DATE NOT NULL,
                    assignment_type TEXT NOT NULL,
                    assignment_reason TEXT,
                    assigned_by TEXT,
                    source TEXT DEFAULT 'manual',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (property_id) REFERENCES properties(id),
                    FOREIGN KEY (unit_id) REFERENCES units(id),
                    FOREIGN KEY (claim_id) REFERENCES payment_claims(id),
                    FOREIGN KEY (bank_txn_id) REFERENCES bank_transactions(id),
                    FOREIGN KEY (statement_id) REFERENCES bank_statements(id)
                )
            """)
            conn.execute("""
                INSERT INTO payments_new
                    (id, property_id, unit_id, claim_id, bank_txn_id, statement_id,
                     amount, payment_date, assignment_type, assignment_reason, assigned_by, source, created_at)
                SELECT id, property_id, unit_id, claim_id, bank_txn_id, statement_id,
                       amount, payment_date, assignment_type, assignment_reason, assigned_by,
                       COALESCE(source, 'manual'), created_at
                FROM payments
            """)
            conn.execute("DROP TABLE payments")
            conn.execute("ALTER TABLE payments_new RENAME TO payments")
            # Recreate indexes; partial index ensures uniqueness only for non-NULL bank_txn_id
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_bank_txn_payment "
                "ON payments(bank_txn_id) WHERE bank_txn_id IS NOT NULL"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_unit ON payments(unit_id)")
            conn.execute("PRAGMA foreign_keys = ON")
            # Recreate unit_balances VIEW (same definition as schema.sql)
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

        print("Migration complete: payment_transactions, disbursements, payments.source ready.")


def migrate_add_language_preference():
    """Add language_preference column to tenants. Idempotent."""
    with get_connection() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(tenants)").fetchall()]
        if 'language_preference' not in cols:
            conn.execute("ALTER TABLE tenants ADD COLUMN language_preference TEXT")
        print("Migration complete: tenants.language_preference ready.")


def migrate_add_organizations():
    """Add organizations table and organization_id to properties. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS organizations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE,
                admin_password_hash TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_org_slug ON organizations(slug)")
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if 'organization_id' not in prop_cols:
            conn.execute("ALTER TABLE properties ADD COLUMN organization_id TEXT REFERENCES organizations(id)")
        print("Migration complete: organizations table and properties.organization_id ready.")


def migrate_add_persons():
    """Add persons table and person_id FK to tenants and owners. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS persons (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                password_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_persons_phone ON persons(phone)")
        tenant_cols = [r[1] for r in conn.execute("PRAGMA table_info(tenants)").fetchall()]
        if 'person_id' not in tenant_cols:
            conn.execute("ALTER TABLE tenants ADD COLUMN person_id TEXT REFERENCES persons(id)")
        owner_cols = [r[1] for r in conn.execute("PRAGMA table_info(owners)").fetchall()]
        if 'person_id' not in owner_cols:
            conn.execute("ALTER TABLE owners ADD COLUMN person_id TEXT REFERENCES persons(id)")
        print("Migration complete: persons table, tenants.person_id, owners.person_id ready.")


def migrate_add_platform_errors():
    """Create platform_errors table for surfacing errors to the platform admin. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_errors (
                id TEXT PRIMARY KEY,
                error_type TEXT NOT NULL DEFAULT '500',
                route TEXT,
                method TEXT,
                org_id TEXT,
                property_id TEXT,
                user_role TEXT,
                message TEXT,
                traceback TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_perr_created ON platform_errors(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_perr_type ON platform_errors(error_type)")
        print("Migration complete: platform_errors table ready.")


def migrate_add_org_scoped_statements():
    """Add org_id and bank_format to bank_statements for org-level statement ownership. Idempotent."""
    with get_connection() as conn:
        for col, typedef in [('org_id', 'TEXT'), ('bank_format', 'TEXT')]:
            try:
                conn.execute(f"ALTER TABLE bank_statements ADD COLUMN {col} {typedef}")
            except Exception:
                pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stmts_org ON bank_statements(org_id)")
    print("Migration complete: bank_statements.org_id + bank_format.")


def migrate_add_statement_parse_errors():
    """Create statement_parse_errors table for persisting PDF parse failures. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS statement_parse_errors (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                statement_id TEXT,
                filename TEXT NOT NULL,
                file_path TEXT,
                bank_format TEXT,
                error_type TEXT NOT NULL,
                error_message TEXT,
                raw_text TEXT,
                page_number INTEGER,
                txn_index INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spe_org ON statement_parse_errors(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spe_stmt ON statement_parse_errors(statement_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spe_created ON statement_parse_errors(created_at)")
    print("Migration complete: statement_parse_errors table ready.")


def migrate_add_platform_shadow_log():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_shadow_log (
                id          TEXT PRIMARY KEY,
                org_id      TEXT,
                property_id TEXT,
                action      TEXT NOT NULL,
                entity_type TEXT,
                entity_id   TEXT,
                details     TEXT,
                actor       TEXT DEFAULT 'agency',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_psl_org ON platform_shadow_log(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_psl_created ON platform_shadow_log(created_at)")
    print("Migration complete: platform_shadow_log table ready.")


def migrate_add_tenant_disputes():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenant_disputes (
                id              TEXT PRIMARY KEY,
                tenant_id       TEXT NOT NULL,
                property_id     TEXT NOT NULL,
                org_id          TEXT,
                subject         TEXT,
                message         TEXT NOT NULL,
                status          TEXT DEFAULT 'open',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at     DATETIME,
                resolution_note TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_td_org ON tenant_disputes(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_td_status ON tenant_disputes(status)")
    print("Migration complete: tenant_disputes table ready.")


def migrate_add_platform_alerts():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_alerts (
                id          TEXT PRIMARY KEY,
                org_id      TEXT,
                property_id TEXT,
                alert_type  TEXT NOT NULL,
                details     TEXT,
                severity    TEXT DEFAULT 'warning',
                status      TEXT DEFAULT 'open',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                dismissed_at DATETIME
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pa_org ON platform_alerts(org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pa_status ON platform_alerts(status)")
    print("Migration complete: platform_alerts table ready.")


def migrate_add_payout_fields():
    """Add owner payout account fields. Only the owner can set these via their portal. Idempotent."""
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(owners)").fetchall()]
        if 'payout_mpesa' not in cols:
            conn.execute("ALTER TABLE owners ADD COLUMN payout_mpesa TEXT")
        if 'payout_confirmed' not in cols:
            conn.execute("ALTER TABLE owners ADD COLUMN payout_confirmed INTEGER DEFAULT 0")
        if 'payout_active_at' not in cols:
            conn.execute("ALTER TABLE owners ADD COLUMN payout_active_at TIMESTAMP")
        if 'payout_otp' not in cols:
            conn.execute("ALTER TABLE owners ADD COLUMN payout_otp TEXT")
        if 'payout_otp_expires_at' not in cols:
            conn.execute("ALTER TABLE owners ADD COLUMN payout_otp_expires_at TIMESTAMP")
    print("Migration complete: owner payout fields ready.")


def migrate_add_water_readings():
    """Add water_rate to properties; create water_uploads and water_readings tables. Idempotent."""
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if 'water_rate' not in cols:
            conn.execute("ALTER TABLE properties ADD COLUMN water_rate REAL DEFAULT 300")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS water_uploads (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                reading_period TEXT NOT NULL,
                charge_period TEXT NOT NULL,
                unit_count INTEGER DEFAULT 0,
                total_amount REAL DEFAULT 0,
                source TEXT DEFAULT 'caretaker_web',
                submitted_by TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wu_property ON water_uploads(property_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wu_charge_period ON water_uploads(charge_period)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS water_readings (
                id TEXT PRIMARY KEY,
                upload_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                previous_reading REAL NOT NULL,
                current_reading REAL NOT NULL,
                units_consumed REAL NOT NULL,
                rate REAL NOT NULL,
                amount REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (upload_id) REFERENCES water_uploads(id),
                FOREIGN KEY (unit_id) REFERENCES units(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wr_upload ON water_readings(upload_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wr_unit ON water_readings(unit_id)")


def migrate_bank_statements_nullable_property():
    """Remove NOT NULL constraint from bank_statements.property_id. Idempotent.

    SQLite cannot ALTER a column constraint directly — we recreate the table.
    This allows statements to be truly org-scoped with no required property tag.
    """
    with get_connection() as conn:
        info = conn.execute("PRAGMA table_info(bank_statements)").fetchall()
        prop = next((c for c in info if c[1] == 'property_id'), None)
        if not prop or prop[3] == 0:
            print("Migration: bank_statements.property_id already nullable, skipping.")
            return

        # Drop any leftover temp table from a previous failed attempt
        conn.execute("DROP TABLE IF EXISTS _bank_statements_new")

    # executescript issues an implicit COMMIT — use separately from get_connection context
    import sqlite3 as _sqlite3, os as _os
    db_path = _os.environ.get('DATABASE_PATH', 'data/rent.db')
    raw = _sqlite3.connect(db_path)
    try:
        raw.executescript("""
            PRAGMA foreign_keys = OFF;

            CREATE TABLE _bank_statements_new (
                id TEXT PRIMARY KEY,
                org_id TEXT,
                property_id TEXT,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                period_start DATE,
                period_end DATE,
                opening_balance DECIMAL(12,2),
                closing_balance DECIMAL(12,2),
                total_transactions INTEGER,
                rent_transactions INTEGER,
                bank_format TEXT,
                status TEXT DEFAULT 'active',
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (property_id) REFERENCES properties(id)
            );

            INSERT INTO _bank_statements_new
                (id, org_id, property_id, filename, file_path,
                 period_start, period_end, opening_balance, closing_balance,
                 total_transactions, rent_transactions, bank_format, status, uploaded_at)
            SELECT id, org_id, property_id, filename, file_path,
                   period_start, period_end, opening_balance, closing_balance,
                   total_transactions, rent_transactions, bank_format, status, uploaded_at
            FROM bank_statements;

            DROP TABLE bank_statements;
            ALTER TABLE _bank_statements_new RENAME TO bank_statements;

            CREATE INDEX IF NOT EXISTS idx_stmts_org ON bank_statements(org_id);

            PRAGMA foreign_keys = ON;
        """)
    finally:
        raw.close()

    print("Migration complete: bank_statements.property_id now nullable.")


def migrate_add_deletion_requested():
    """Add deletion_requested_at to properties for platform-reviewed soft-delete. Idempotent."""
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if 'deletion_requested_at' not in cols:
            conn.execute("ALTER TABLE properties ADD COLUMN deletion_requested_at TIMESTAMP")
        print("Migration complete: properties.deletion_requested_at ready.")


def migrate_add_platform_fee_rate():
    """Add platform_fee_rate to organizations, activation_token to persons, platform fee columns
    to disbursements. Idempotent."""
    with get_connection() as conn:
        org_cols = [r[1] for r in conn.execute("PRAGMA table_info(organizations)").fetchall()]
        if 'platform_fee_rate' not in org_cols:
            conn.execute("ALTER TABLE organizations ADD COLUMN platform_fee_rate REAL DEFAULT 0.01")

        per_cols = [r[1] for r in conn.execute("PRAGMA table_info(persons)").fetchall()]
        if 'activation_token' not in per_cols:
            conn.execute("ALTER TABLE persons ADD COLUMN activation_token TEXT")

        dis_cols = [r[1] for r in conn.execute("PRAGMA table_info(disbursements)").fetchall()]
        if 'platform_fee_rate' not in dis_cols:
            conn.execute("ALTER TABLE disbursements ADD COLUMN platform_fee_rate REAL")
        if 'platform_fee_amount' not in dis_cols:
            conn.execute("ALTER TABLE disbursements ADD COLUMN platform_fee_amount REAL")
        print("Migration complete: platform_fee_rate, activation_token, disbursement fee columns ready.")


def migrate_add_owner_otp():
    """Add OTP fields and phone_verified flag to persons. Idempotent."""
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(persons)").fetchall()]
        if 'otp_code' not in cols:
            conn.execute("ALTER TABLE persons ADD COLUMN otp_code TEXT")
        if 'otp_expires_at' not in cols:
            conn.execute("ALTER TABLE persons ADD COLUMN otp_expires_at TIMESTAMP")
        if 'phone_verified' not in cols:
            conn.execute("ALTER TABLE persons ADD COLUMN phone_verified INTEGER DEFAULT 0")
        print("Migration complete: persons OTP fields ready.")


def migrate_add_primary_owner():
    """Add is_primary flag to property_owners. First assigned owner per property becomes primary. Idempotent."""
    with get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(property_owners)").fetchall()]
        if 'is_primary' not in cols:
            conn.execute("ALTER TABLE property_owners ADD COLUMN is_primary INTEGER DEFAULT 0")
            # Mark earliest assignment per property as primary for existing data
            conn.execute("""
                UPDATE property_owners SET is_primary = 1
                WHERE rowid IN (
                    SELECT MIN(rowid) FROM property_owners GROUP BY property_id
                )
            """)
        print("Migration complete: property_owners.is_primary ready.")


def migrate_add_platform_outbox():
    """Create platform_outbox table for logging all outbound messages. Idempotent."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_outbox (
                id          TEXT PRIMARY KEY,
                to_name     TEXT,
                to_email    TEXT,
                to_phone    TEXT,
                channel     TEXT NOT NULL,
                subject     TEXT,
                body        TEXT NOT NULL,
                status      TEXT DEFAULT 'simulated',
                error       TEXT,
                org_id      TEXT,
                property_id TEXT,
                message_type TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_created ON platform_outbox(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_channel ON platform_outbox(channel)")
        print("Migration complete: platform_outbox table ready.")
