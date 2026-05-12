#!/usr/bin/env python3
"""
Seed script for Phase 6 test run.

Creates:
  - 2 organisations (Agency A, Agency B)
  - Owner A (person + owner record) → Org 1
  - Owner B (person + owner record) → Org 2
  - Property 1 + 2 (Org 1, Owner A), 6 units each
  - Property 3 + 4 (Org 2, Owner B), 6 units each
  - 5 tenants per property (with person_id auto-linked where phone matches)
  - Tenant X: unit in Property 1 AND unit in Property 3

Run from project root:
    ./venv/bin/python scripts/seed_phase6.py

Set DATABASE_PATH env var to target a specific DB:
    DATABASE_PATH=data/dev.db ./venv/bin/python scripts/seed_phase6.py

Passwords for this test data:
  - Owner A Domi Login:  phone +254701000001 / password ownerA123
  - Owner B Domi Login:  phone +254701000002 / password ownerB123
  - Tenant X Domi Login: phone +254701000010 / password tenantX123
"""
import os
import sys
import secrets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash
from src.database.db import get_connection, generate_id, init_database

init_database()


def run():
    with get_connection() as conn:

        # ── Organisations ──────────────────────────────────────────────
        org_a_id = generate_id("ORG")
        org_b_id = generate_id("ORG")
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name, slug, contact_email) VALUES (?, ?, ?, ?)",
            (org_a_id, "Agency Alfa", "agency-alfa", "admin@agencyalfa.co.ke"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name, slug, contact_email) VALUES (?, ?, ?, ?)",
            (org_b_id, "Agency Beta", "agency-beta", "admin@agencybeta.co.ke"),
        )
        print(f"Created orgs: {org_a_id} (Agency Alfa), {org_b_id} (Agency Beta)")

        # ── Persons: Owner A, Owner B, Tenant X ───────────────────────
        person_oa = generate_id("PERS")
        person_ob = generate_id("PERS")
        person_tx = generate_id("PERS")
        conn.execute(
            "INSERT OR IGNORE INTO persons (id, name, phone, password_hash) VALUES (?, ?, ?, ?)",
            (person_oa, "Amara Waweru", "+254701000001", generate_password_hash("ownerA123")),
        )
        conn.execute(
            "INSERT OR IGNORE INTO persons (id, name, phone, password_hash) VALUES (?, ?, ?, ?)",
            (person_ob, "Benjamin Omondi", "+254701000002", generate_password_hash("ownerB123")),
        )
        conn.execute(
            "INSERT OR IGNORE INTO persons (id, name, phone, password_hash) VALUES (?, ?, ?, ?)",
            (person_tx, "Xenia Kamau", "+254701000010", generate_password_hash("tenantX123")),
        )
        print(f"Created persons: Owner A ({person_oa}), Owner B ({person_ob}), Tenant X ({person_tx})")

        # ── Owners ────────────────────────────────────────────────────
        owner_a = secrets.token_hex(8)
        owner_b = secrets.token_hex(8)
        conn.execute(
            "INSERT OR IGNORE INTO owners (id, name, phone, email, person_id) VALUES (?, ?, ?, ?, ?)",
            (owner_a, "Amara Waweru", "0701000001", None, person_oa),
        )
        conn.execute(
            "INSERT OR IGNORE INTO owners (id, name, phone, email, person_id) VALUES (?, ?, ?, ?, ?)",
            (owner_b, "Benjamin Omondi", "0701000002", None, person_ob),
        )
        print(f"Created owners: {owner_a} (Amara), {owner_b} (Benjamin)")

        # ── Properties ────────────────────────────────────────────────
        def make_property(name, address, org_id, owner_id):
            pid = generate_id("PROP")
            conn.execute(
                "INSERT INTO properties (id, name, address, organization_id, owner_id) VALUES (?, ?, ?, ?, ?)",
                (pid, name, address, org_id, owner_id),
            )
            jid = generate_id("POWN")
            conn.execute(
                "INSERT OR IGNORE INTO property_owners (id, property_id, owner_id) VALUES (?, ?, ?)",
                (jid, pid, owner_id),
            )
            return pid

        p1 = make_property("Riverside Courts", "Riverside Drive, Nairobi", org_a_id, owner_a)
        p2 = make_property("Garden View Apartments", "Kileleshwa, Nairobi", org_a_id, owner_a)
        p3 = make_property("Parklands Estate", "3rd Parklands Ave, Nairobi", org_b_id, owner_b)
        p4 = make_property("Westlands Flats", "Westlands Road, Nairobi", org_b_id, owner_b)
        print(f"Created properties: P1={p1}, P2={p2}, P3={p3}, P4={p4}")

        # ── Units + Tenants ───────────────────────────────────────────
        def make_units_and_tenants(prop_id, prefix, count, rent=15000, service=2000):
            unit_ids = []
            for i in range(1, count + 1):
                uid = generate_id("UNIT")
                conn.execute(
                    "INSERT INTO units (id, property_id, unit_number, monthly_rent, service_charge, status) VALUES (?, ?, ?, ?, ?, 'occupied')",
                    (uid, prop_id, f"{prefix}{i}", rent, service),
                )
                unit_ids.append((uid, f"{prefix}{i}"))
            return unit_ids

        # Tenants for P1 (Riverside Courts)
        p1_units = make_units_and_tenants(p1, "A", 6)
        tenants_p1 = [
            ("Kariuki Mwangi", "0712000001"), ("Fatuma Hassan", "0712000002"),
            ("David Otieno", "0712000003"), ("Grace Njeri", "0712000004"),
            ("James Kamau", "0712000005"),
        ]
        for (uid, unum), (tname, tphone) in zip(p1_units[:5], tenants_p1):
            tid = generate_id("TENANT")
            conn.execute(
                "INSERT INTO tenants (id, property_id, unit_id, name, phone, status, move_in_date) VALUES (?, ?, ?, ?, ?, 'active', date('now','-6 months'))",
                (tid, p1, uid, tname, tphone),
            )

        # Tenant X gets unit A6 in P1
        tx_unit_p1, _ = p1_units[5]
        tx_tenant_p1 = generate_id("TENANT")
        conn.execute(
            "INSERT INTO tenants (id, property_id, unit_id, name, phone, status, move_in_date, person_id) VALUES (?, ?, ?, ?, ?, 'active', date('now','-3 months'), ?)",
            (tx_tenant_p1, p1, tx_unit_p1, "Xenia Kamau", "0701000010", person_tx),
        )

        # Tenants for P2 (Garden View)
        p2_units = make_units_and_tenants(p2, "B", 6, rent=18000, service=2500)
        tenants_p2 = [
            ("Samuel Kipchoge", "0722000001"), ("Mary Wanjiku", "0722000002"),
            ("Peter Njoroge", "0722000003"), ("Agnes Auma", "0722000004"),
            ("Michael Mutua", "0722000005"),
        ]
        for (uid, unum), (tname, tphone) in zip(p2_units[:5], tenants_p2):
            tid = generate_id("TENANT")
            conn.execute(
                "INSERT INTO tenants (id, property_id, unit_id, name, phone, status, move_in_date) VALUES (?, ?, ?, ?, ?, 'active', date('now','-4 months'))",
                (tid, p2, uid, tname, tphone),
            )
        # B6 left vacant
        conn.execute("UPDATE units SET status = 'vacant' WHERE id = ?", (p2_units[5][0],))

        # Tenants for P3 (Parklands Estate)
        p3_units = make_units_and_tenants(p3, "C", 6, rent=20000, service=3000)
        tenants_p3 = [
            ("Hassan Abdi", "0733000001"), ("Lydia Chebet", "0733000002"),
            ("Robert Ochieng", "0733000003"), ("Esther Wambua", "0733000004"),
            ("Felix Nduta", "0733000005"),
        ]
        for (uid, unum), (tname, tphone) in zip(p3_units[:5], tenants_p3):
            tid = generate_id("TENANT")
            conn.execute(
                "INSERT INTO tenants (id, property_id, unit_id, name, phone, status, move_in_date) VALUES (?, ?, ?, ?, ?, 'active', date('now','-5 months'))",
                (tid, p3, uid, tname, tphone),
            )

        # Tenant X also has C6 in P3
        tx_unit_p3, _ = p3_units[5]
        tx_tenant_p3 = generate_id("TENANT")
        conn.execute(
            "INSERT INTO tenants (id, property_id, unit_id, name, phone, status, move_in_date, person_id) VALUES (?, ?, ?, ?, ?, 'active', date('now','-2 months'), ?)",
            (tx_tenant_p3, p3, tx_unit_p3, "Xenia Kamau", "0701000010", person_tx),
        )

        # Tenants for P4 (Westlands Flats)
        p4_units = make_units_and_tenants(p4, "D", 6, rent=25000, service=3500)
        tenants_p4 = [
            ("Joyce Akinyi", "0744000001"), ("Charles Mwaura", "0744000002"),
            ("Diana Wachira", "0744000003"), ("Eric Onyango", "0744000004"),
            ("Beatrice Korir", "0744000005"),
        ]
        for (uid, unum), (tname, tphone) in zip(p4_units[:5], tenants_p4):
            tid = generate_id("TENANT")
            conn.execute(
                "INSERT INTO tenants (id, property_id, unit_id, name, phone, status, move_in_date) VALUES (?, ?, ?, ?, ?, 'active', date('now','-7 months'))",
                (tid, p4, uid, tname, tphone),
            )
        # D6 vacant
        conn.execute("UPDATE units SET status = 'vacant' WHERE id = ?", (p4_units[5][0],))

        print("Created units and tenants for all 4 properties.")
        print(f"Tenant X (Xenia Kamau): unit A6 in P1, unit C6 in P3")

    print("\n── Seed complete ──────────────────────────────────────────")
    print("  Orgs:      Agency Alfa, Agency Beta")
    print("  Owner A:   Amara Waweru  | +254701000001 / ownerA123")
    print("  Owner B:   Benjamin Omondi | +254701000002 / ownerB123")
    print("  Tenant X:  Xenia Kamau   | +254701000010 / tenantX123")
    print("  Properties: Riverside Courts, Garden View (Alfa)")
    print("              Parklands Estate, Westlands Flats (Beta)")
    print()
    print("  Next steps:")
    print("  1. Start dev server:  ./scripts/run_dev.sh")
    print("  2. Platform admin:    http://localhost:5001/platform/login")
    print("     (set PLATFORM_ADMIN_PASSWORD env var first)")
    print("  3. Org admin:         http://localhost:5001/login")
    print("     (set ADMIN_PASSWORD env var, then pick Agency Alfa or Beta)")
    print("  4. Owner A portal:    http://localhost:5001/owner/login")
    print("  5. Tenant X portal:   http://localhost:5001/tenant/login")


if __name__ == "__main__":
    run()
