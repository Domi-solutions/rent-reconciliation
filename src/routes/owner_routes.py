"""Owner holistic dashboard — persons-based login across all properties.

Auth: session['person_id'] + session['person_role'] == 'owner'.
Distinct from the existing /view/* token-based flow (session['owner_id']).
Per-property drilldown bridges into the existing viewer by setting session['owner_id'].
"""
from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from src.database.db import get_connection
from src.utils.phone import normalize_to_e164 as _normalize_phone
from src.utils.metrics import get_property_occupancy, get_expected_monthly_income

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")


@owner_bp.before_request
def require_owner_auth():
    exempt = ("owner.login", "owner.logout")
    if request.endpoint in exempt:
        return None
    if session.get("person_id") and session.get("person_role") == "owner":
        return None
    return redirect(url_for("owner.login"))


@owner_bp.route("/login", methods=["GET", "POST"])
def login():
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
            session["person_id"] = person["id"]
            session["person_role"] = "owner"
            session["person_name"] = person["name"]
            return redirect(url_for("owner.dashboard"))

    return render_template("owner/login.html", error=error)


@owner_bp.route("/logout")
def logout():
    session.pop("person_id", None)
    session.pop("person_role", None)
    session.pop("person_name", None)
    session.pop("owner_id", None)
    return redirect(url_for("owner.login"))


@owner_bp.route("/dashboard")
def dashboard():
    person_id = session["person_id"]
    with get_connection() as conn:
        owner_rows = conn.execute(
            "SELECT id FROM owners WHERE person_id = ?", (person_id,)
        ).fetchall()
        owner_ids = [r["id"] for r in owner_rows]

        if not owner_ids:
            return render_template("owner/dashboard.html", properties=[], portfolio=None,
                                   person_name=session.get("person_name", ""))

        placeholders = ",".join("?" * len(owner_ids))
        properties = conn.execute(
            f"""SELECT DISTINCT p.id, p.name, p.address
                FROM properties p
                JOIN property_owners po ON po.property_id = p.id
                WHERE po.owner_id IN ({placeholders}) AND p.status = 'active'
                ORDER BY p.name""",
            owner_ids,
        ).fetchall()

        _today = datetime.today()
        _month_start = _today.replace(day=1).strftime("%Y-%m-%d")
        _month_label = _today.strftime("%B %Y")

        portfolio = {
            "total_units": 0, "occupied": 0, "vacant": 0,
            "expected": 0.0, "collected": 0.0, "arrears": 0.0,
        }
        prop_stats = []

        for prop in properties:
            pid = prop["id"]

            occ = get_property_occupancy(conn, pid)
            total    = occ['total']
            occupied = occ['occupied']
            vacant   = occ['vacant']
            office   = occ['office']
            expected = get_expected_monthly_income(conn, pid)

            collected = float(conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = ? AND payment_date >= ?",
                (pid, _month_start),
            ).fetchone()[0])

            arrears = float(conn.execute(
                "SELECT COALESCE(SUM(balance), 0) FROM unit_balances WHERE property_id = ? AND balance > 0",
                (pid,),
            ).fetchone()[0])

            top_arrears = conn.execute(
                "SELECT unit_number, balance FROM unit_balances WHERE property_id = ? AND balance > 0 ORDER BY balance DESC LIMIT 1",
                (pid,),
            ).fetchone()

            rentable = total - office
            collection_rate = round(collected / expected * 100, 1) if expected > 0 else 0
            occupancy_rate = round(occupied / rentable * 100, 1) if rentable > 0 else 0

            prop_stats.append({
                "id": pid,
                "name": prop["name"],
                "total": total,
                "occupied": occupied,
                "vacant": vacant,
                "office": office,
                "expected": expected,
                "collected": collected,
                "arrears": arrears,
                "collection_rate": collection_rate,
                "occupancy_rate": occupancy_rate,
                "top_arrears": top_arrears,
            })

            portfolio["total_units"] += total
            portfolio["occupied"] += occupied
            portfolio["vacant"] += vacant
            portfolio["expected"] += expected
            portfolio["collected"] += collected
            portfolio["arrears"] += arrears

        if portfolio["expected"] > 0:
            portfolio["collection_rate"] = round(portfolio["collected"] / portfolio["expected"] * 100, 1)
        else:
            portfolio["collection_rate"] = 0
        rentable_total = portfolio["total_units"] - sum(p["office"] for p in prop_stats)
        portfolio["occupancy_rate"] = round(portfolio["occupied"] / rentable_total * 100, 1) if rentable_total > 0 else 0
        portfolio["month_label"] = _month_label

    return render_template(
        "owner/dashboard.html",
        properties=prop_stats,
        portfolio=portfolio,
        person_name=session.get("person_name", ""),
    )


@owner_bp.route("/<property_id>/")
def property_view(property_id):
    """Verify person owns this property then bridge to the existing viewer."""
    person_id = session["person_id"]
    with get_connection() as conn:
        row = conn.execute(
            """SELECT o.id AS owner_id
               FROM owners o
               JOIN property_owners po ON po.owner_id = o.id
               WHERE o.person_id = ? AND po.property_id = ?""",
            (person_id, property_id),
        ).fetchone()
    if not row:
        abort(403)
    session["owner_id"] = row["owner_id"]
    return redirect(url_for("viewer.property_dashboard", property_id=property_id))
