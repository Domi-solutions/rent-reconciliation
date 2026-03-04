-- Enable foreign keys for SQLite
PRAGMA foreign_keys = ON;

-- ============================================================
-- 1. PROPERTIES
-- ============================================================
CREATE TABLE IF NOT EXISTS properties (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    owner_name TEXT,
    owner_phone TEXT,
    owner_email TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active'
);

-- ============================================================
-- 2. UNITS
-- ============================================================
CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL,
    unit_number TEXT NOT NULL,
    monthly_rent DECIMAL(10,2) NOT NULL,
    service_charge DECIMAL(10,2) DEFAULT 0,
    apartment_size TEXT DEFAULT '',
    status TEXT DEFAULT 'occupied',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status_changed_at TIMESTAMP,

    FOREIGN KEY (property_id) REFERENCES properties(id),
    UNIQUE (property_id, unit_number)
);

-- ============================================================
-- 3. TENANTS
-- ============================================================
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL,
    unit_id TEXT,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    move_in_date DATE,
    move_out_date DATE,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_token TEXT,

    FOREIGN KEY (property_id) REFERENCES properties(id),
    FOREIGN KEY (unit_id) REFERENCES units(id)
);

-- idx_tenant_access_token is created by migration migrate_add_tenant_access_token()

-- ============================================================
-- 4. BANK STATEMENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS bank_statements (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    period_start DATE,
    period_end DATE,
    opening_balance DECIMAL(12,2),
    closing_balance DECIMAL(12,2),
    total_transactions INTEGER,
    rent_transactions INTEGER,
    status TEXT DEFAULT 'active',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (property_id) REFERENCES properties(id)
);

-- ============================================================
-- 5. BANK TRANSACTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS bank_transactions (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    mpesa_ref TEXT,
    amount DECIMAL(10,2) NOT NULL,
    txn_type TEXT NOT NULL,
    sender_name TEXT,
    txn_date DATE,
    raw_text TEXT,
    unit_hint TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (statement_id) REFERENCES bank_statements(id)
);

CREATE INDEX IF NOT EXISTS idx_bank_txn_ref ON bank_transactions(mpesa_ref);
CREATE INDEX IF NOT EXISTS idx_bank_txn_stmt ON bank_transactions(statement_id);

-- ============================================================
-- 6. PAYMENT CLAIMS
-- ============================================================
CREATE TABLE IF NOT EXISTS payment_claims (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL,
    mpesa_ref TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    claimed_amount DECIMAL(10,2),
    raw_message TEXT,
    source TEXT DEFAULT 'web',
    status TEXT DEFAULT 'pending',
    verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (property_id) REFERENCES properties(id),
    FOREIGN KEY (unit_id) REFERENCES units(id),
    UNIQUE (property_id, mpesa_ref)
);

CREATE INDEX IF NOT EXISTS idx_claim_status ON payment_claims(status);
CREATE INDEX IF NOT EXISTS idx_claim_ref ON payment_claims(mpesa_ref);

-- ============================================================
-- 7. PAYMENTS (Verified - affects balance)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    claim_id TEXT,
    bank_txn_id TEXT NOT NULL,
    statement_id TEXT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    payment_date DATE NOT NULL,
    assignment_type TEXT NOT NULL,
    assignment_reason TEXT,
    assigned_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (property_id) REFERENCES properties(id),
    FOREIGN KEY (unit_id) REFERENCES units(id),
    FOREIGN KEY (claim_id) REFERENCES payment_claims(id),
    FOREIGN KEY (bank_txn_id) REFERENCES bank_transactions(id),
    FOREIGN KEY (statement_id) REFERENCES bank_statements(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_bank_txn_payment ON payments(bank_txn_id);
CREATE INDEX IF NOT EXISTS idx_payment_unit ON payments(unit_id);

-- ============================================================
-- 8. RENT CHARGES
-- ============================================================
CREATE TABLE IF NOT EXISTS rent_charges (
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
);

-- ============================================================
-- 8B. PAYMENT ALLOCATIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS payment_allocations (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL,
    charge_id TEXT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE,
    FOREIGN KEY (charge_id) REFERENCES rent_charges(id)
);

CREATE INDEX IF NOT EXISTS idx_alloc_payment ON payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_alloc_charge ON payment_allocations(charge_id);

-- ============================================================
-- 9. AUDIT LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details TEXT,
    ip_address TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);

-- ============================================================
-- 10. REPORT SETTINGS
-- ============================================================
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
);

-- ============================================================
-- 11. LANDLORD REPORTS
-- ============================================================
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
);

CREATE INDEX IF NOT EXISTS idx_report_property ON landlord_reports(property_id);
CREATE INDEX IF NOT EXISTS idx_report_created ON landlord_reports(created_at);

-- ============================================================
-- 12. MESSAGING TABLES
-- ============================================================
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
);

CREATE INDEX IF NOT EXISTS idx_messages_property ON messages(property_id);
CREATE INDEX IF NOT EXISTS idx_messages_tenant ON messages(tenant_id);
CREATE INDEX IF NOT EXISTS idx_messages_batch ON messages(batch_id);

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
);

CREATE TABLE IF NOT EXISTS reminder_settings (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL,
    template_key TEXT NOT NULL,
    days_before_due INTEGER NOT NULL,
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (property_id, template_key),
    FOREIGN KEY (property_id) REFERENCES properties(id)
);

-- ============================================================
-- BALANCE VIEW (Calculated, never stored)
-- ============================================================
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
) payments_sum ON u.id = payments_sum.unit_id;
