"""Owner holistic dashboard — persons-based login across all properties.

Auth: session['person_id'] + session['person_role'] == 'owner'.
Distinct from the existing /view/* token-based flow (session['owner_id']).
Per-property drilldown bridges into the existing viewer by setting session['owner_id'].
"""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from src.database.db import get_connection
from src.utils.metrics import get_property_occupancy, get_expected_monthly_income

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")


@owner_bp.before_request
def require_owner_auth():
    exempt = ("owner.login", "owner.logout", "owner.activate", "owner.verify_email", "owner.token_login")
    if request.endpoint in exempt:
        return None
    if session.get("person_id") and session.get("person_role") == "owner":
        return None
    return redirect(url_for("admin_login"))


@owner_bp.route("/login")
def login():
    """Legacy URL — redirect to the unified login page."""
    return redirect(url_for("admin_login"))


@owner_bp.route("/l/<token>", methods=["GET", "POST"])
def token_login(token):
    """Password-only login via a pre-shared link. Owner enters password, no email needed."""
    from werkzeug.security import check_password_hash

    with get_connection() as conn:
        row = conn.execute(
            """SELECT o.id AS owner_id, o.name AS owner_name,
                      p.id AS person_id, p.name AS person_name,
                      p.password_hash, p.activation_token
               FROM owners o
               LEFT JOIN persons p ON p.id = o.person_id
               WHERE o.access_token = ?""",
            (token,),
        ).fetchone()

    if not row:
        return render_template("owner/token_login.html", owner_name=None,
                               error="This link is invalid or has been revoked. Contact your property manager.")

    # Account not yet activated — direct them to the activation flow
    if not row["password_hash"]:
        activation_url = url_for("owner.activate", token=row["activation_token"], _external=True) if row["activation_token"] else None
        return render_template("owner/token_login.html", owner_name=row["owner_name"],
                               error=None, not_activated=True, activation_url=activation_url)

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if check_password_hash(row["password_hash"], password):
            session["person_id"] = row["person_id"]
            session["person_role"] = "owner"
            session["person_name"] = row["person_name"]
            return redirect(url_for("owner.dashboard"))
        error = "Incorrect password. Try again."

    return render_template("owner/token_login.html", owner_name=row["owner_name"],
                           error=error, not_activated=False, activation_url=None)


@owner_bp.route("/logout")
def logout():
    session.pop("person_id", None)
    session.pop("person_role", None)
    session.pop("person_name", None)
    session.pop("owner_id", None)
    return redirect(url_for("admin_login"))


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
            office   = occ['owner_use'] + occ['short_term']
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


def _send_email_otp(person_id, email, name):
    """Generate a 6-digit OTP, store it with a 10-min expiry, send via email.

    Also logs to platform_outbox regardless of whether email is configured.
    Returns (sent: bool, error_msg: str | None).
    """
    import random
    from datetime import datetime, timedelta, timezone
    from src.messaging.email import send_email
    from src.messaging.outbox import log_outbox

    otp = str(random.randint(100000, 999999))
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        conn.execute(
            "UPDATE persons SET otp_code = ?, otp_expires_at = ? WHERE id = ?",
            (otp, expires, person_id),
        )

    subject = "Your Domi verification code"
    body = (
        f"Hi {name},\n\n"
        f"Your Domi verification code is: {otp}\n\n"
        f"This code is valid for 10 minutes. If you didn't request this, ignore this email.\n\n"
        f"— Domi"
    )

    ok, err = send_email(email, subject, body, to_name=name)
    status = 'sent' if ok else 'simulated'
    log_outbox(
        channel='email',
        to_name=name,
        to_email=email,
        subject=subject,
        body=body,
        status=status,
        error=err,
        message_type='owner_otp',
    )
    # Treat simulated as success so the flow continues without SMTP configured
    return True, None


@owner_bp.route("/activate/<token>", methods=["GET", "POST"])
def activate(token):
    """Step 1: Owner sets their password via the activation link."""
    with get_connection() as conn:
        person = conn.execute(
            "SELECT id, name, email, password_hash FROM persons WHERE activation_token = ?", (token,)
        ).fetchone()

    if not person:
        return render_template("owner/activate.html",
                               error="This link is invalid or has already been used.",
                               person_name=None)

    if person["password_hash"]:
        flash("Your account is already activated. Please log in.", "info")
        return redirect(url_for("owner.login"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE persons SET password_hash = ?, activation_token = NULL WHERE id = ?",
                    (generate_password_hash(password), person["id"]),
                )
            session["pending_verification_id"] = person["id"]
            session["pending_verification_name"] = person["name"]
            session["pending_verification_email"] = person["email"]

            _send_email_otp(person["id"], person["email"], person["name"])
            session["otp_sent"] = True
            return redirect(url_for("owner.verify_email"))

    return render_template("owner/activate.html", person_name=person["name"], error=error)


@owner_bp.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    """Step 2: Verify identity via 6-digit email OTP."""
    person_id = session.get("pending_verification_id")
    if not person_id:
        flash("Session expired. Please use your activation link again.", "warning")
        return redirect(url_for("owner.login"))

    person_name = session.get("pending_verification_name", "")
    email = session.get("pending_verification_email", "")
    error = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "verify_otp":
            from datetime import datetime, timezone
            entered = request.form.get("otp", "").strip()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with get_connection() as conn:
                person = conn.execute(
                    "SELECT otp_code, otp_expires_at FROM persons WHERE id = ?", (person_id,)
                ).fetchone()
                if not person or not person["otp_code"]:
                    error = "No code found. Request a new one."
                elif person["otp_expires_at"] < now:
                    error = "Code has expired. Request a new one."
                elif person["otp_code"] != entered:
                    error = "Incorrect code. Check your email and try again."
                else:
                    conn.execute(
                        "UPDATE persons SET phone_verified = 1, otp_code = NULL, otp_expires_at = NULL WHERE id = ?",
                        (person_id,),
                    )
                    session.pop("pending_verification_id", None)
                    session.pop("pending_verification_name", None)
                    session.pop("pending_verification_email", None)
                    session.pop("otp_sent", None)
                    session["person_id"] = person_id
                    session["person_role"] = "owner"
                    session["person_name"] = person_name
                    flash(f"Welcome, {person_name}! Your account is active.", "success")
                    return redirect(url_for("owner.dashboard"))

        elif action == "resend_otp":
            _send_email_otp(person_id, email, person_name)
            flash("A new code has been sent to your email.", "success")

    return render_template("owner/verify_email.html",
                           person_name=person_name, email=email, error=error)
