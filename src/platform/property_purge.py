"""
The one place that knows how to erase a property's data.

Deleting a property means clearing rows from two dozen tables in an order the
foreign keys will accept. Having that order written down twice is how the two
copies drift apart and one of them starts failing, so both the platform's
approved-deletion route and the duplicate-property cleanup script call this.

Deliberately NOT cleared: platform_shadow_log, platform_alerts, platform_errors
and platform_outbox. Those are the platform's own record of what an agency did,
and CLAUDE.md describes the shadow log as agency-uneditable. A record that
disappears when the agency deletes the evidence is not a record. tenant_disputes
is kept for the same reason — a tenant's complaint to the platform should
outlive the agency's decision to remove the property.
"""

# Ordered so that every row is removed before whatever it points at.
_PURGE_STEPS = [
    ("payment_allocations",
     "DELETE FROM payment_allocations WHERE payment_id IN (SELECT id FROM payments WHERE property_id=?)"),
    ("payment_allocations",
     "DELETE FROM payment_allocations WHERE charge_id IN (SELECT id FROM rent_charges WHERE property_id=?)"),
    ("payment_transactions", "DELETE FROM payment_transactions WHERE property_id=?"),
    ("payments", "DELETE FROM payments WHERE property_id=?"),
    ("disbursements", "DELETE FROM disbursements WHERE property_id=?"),
    ("balance_snapshots",
     "DELETE FROM balance_snapshots WHERE property_id=? OR unit_id IN (SELECT id FROM units WHERE property_id=?)"),
    ("water_readings",
     "DELETE FROM water_readings WHERE unit_id IN (SELECT id FROM units WHERE property_id=?)"),
    ("water_uploads", "DELETE FROM water_uploads WHERE property_id=?"),
    ("rent_charges", "DELETE FROM rent_charges WHERE property_id=?"),
    ("maintenance_issues", "DELETE FROM maintenance_issues WHERE property_id=?"),
    ("messages", "DELETE FROM messages WHERE property_id=?"),
    ("checkin_responses", "DELETE FROM checkin_responses WHERE property_id=?"),
    ("inbound_sessions", "DELETE FROM inbound_sessions WHERE property_id=?"),
    ("inbound_messages", "DELETE FROM inbound_messages WHERE property_id=?"),
    # Before tenants: these rows point at tenant rows.
    ("tenant_departures", "DELETE FROM tenant_departures WHERE property_id=?"),
    ("tenants", "DELETE FROM tenants WHERE property_id=?"),
    ("payment_claims", "DELETE FROM payment_claims WHERE property_id=?"),
    ("bank_transactions",
     "DELETE FROM bank_transactions WHERE statement_id IN (SELECT id FROM bank_statements WHERE property_id=?)"),
    ("statement_parse_errors",
     "DELETE FROM statement_parse_errors WHERE statement_id IN (SELECT id FROM bank_statements WHERE property_id=?)"),
    ("bank_statements", "DELETE FROM bank_statements WHERE property_id=?"),
    ("property_owners", "DELETE FROM property_owners WHERE property_id=?"),
    ("caretakers", "DELETE FROM caretakers WHERE property_id=?"),
    ("landlord_reports", "DELETE FROM landlord_reports WHERE property_id=?"),
    ("message_templates", "DELETE FROM message_templates WHERE property_id=?"),
    ("owner_messages", "DELETE FROM owner_messages WHERE property_id=?"),
    ("reminder_schedules", "DELETE FROM reminder_schedules WHERE property_id=?"),
    ("reminder_settings", "DELETE FROM reminder_settings WHERE property_id=?"),
    ("report_settings", "DELETE FROM report_settings WHERE property_id=?"),
    ("units", "DELETE FROM units WHERE property_id=?"),
]

# Rows that belong to the property, counted for the "what would be deleted"
# preview. Kept separate from the delete statements so a miscounted preview can
# never delete anything.
COUNT_QUERIES = [
    ("units", "SELECT COUNT(*) FROM units WHERE property_id=?"),
    ("tenants", "SELECT COUNT(*) FROM tenants WHERE property_id=?"),
    ("rent_charges", "SELECT COUNT(*) FROM rent_charges WHERE property_id=?"),
    ("payments", "SELECT COUNT(*) FROM payments WHERE property_id=?"),
    ("payment_claims", "SELECT COUNT(*) FROM payment_claims WHERE property_id=?"),
    ("bank_statements", "SELECT COUNT(*) FROM bank_statements WHERE property_id=?"),
    ("bank_transactions",
     "SELECT COUNT(*) FROM bank_transactions WHERE statement_id IN (SELECT id FROM bank_statements WHERE property_id=?)"),
    ("landlord_reports", "SELECT COUNT(*) FROM landlord_reports WHERE property_id=?"),
    ("messages", "SELECT COUNT(*) FROM messages WHERE property_id=?"),
    ("owner_messages", "SELECT COUNT(*) FROM owner_messages WHERE property_id=?"),
    ("property_owners", "SELECT COUNT(*) FROM property_owners WHERE property_id=?"),
    ("caretakers", "SELECT COUNT(*) FROM caretakers WHERE property_id=?"),
    ("water_uploads", "SELECT COUNT(*) FROM water_uploads WHERE property_id=?"),
    ("maintenance_issues", "SELECT COUNT(*) FROM maintenance_issues WHERE property_id=?"),
    ("tenant_departures", "SELECT COUNT(*) FROM tenant_departures WHERE property_id=?"),
]


def count_property_data(conn, property_id):
    """Rows that purge_property_data() would remove, per table. Read-only."""
    counts = {}
    for table, query in COUNT_QUERIES:
        try:
            counts[table] = conn.execute(query, (property_id,)).fetchone()[0]
        except Exception:
            counts[table] = None      # table absent on an older database
    return counts


def purge_property_data(conn, property_id):
    """Delete everything belonging to a property, except the property row itself.

    The caller deletes properties, so it stays responsible for the audit and
    platform-log entries that must accompany that. Returns rows removed per
    table.
    """
    removed = {}
    for table, statement in _PURGE_STEPS:
        params = (property_id, property_id) if statement.count('?') == 2 else (property_id,)
        try:
            cursor = conn.execute(statement, params)
            removed[table] = removed.get(table, 0) + (cursor.rowcount or 0)
        except Exception:
            # An older database may not have every table. Skipping one must not
            # abandon the rest of the cascade half-done.
            removed.setdefault(table, 0)
    return removed
