"""Platform admin routes — Domi operator layer.

Auth: PLATFORM_ADMIN_PASSWORD env var.
Session key: session['platform_admin'] = True.
All /platform/* routes are exempt from the org admin before_request check.
"""
import os
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for

from src.database.db import generate_id, get_connection
from src.parsers.banks.registry import bank_display_name as _bank_label

platform_bp = Blueprint("platform", __name__, url_prefix="/platform")


@platform_bp.before_request
def require_platform_auth():
    exempt = ("platform.login", "platform.logout")
    if request.endpoint in exempt:
        return None
    if session.get("platform_admin"):
        return None
    return redirect(url_for("platform.login", next=request.url))


@platform_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        platform_pw = os.environ.get("PLATFORM_ADMIN_PASSWORD", "")
        if platform_pw and password == platform_pw:
            session["platform_admin"] = True
            next_url = request.form.get("next") or url_for("platform.dashboard")
            return redirect(next_url)
        error = "Incorrect password."
    return render_template("platform/login.html", error=error)


@platform_bp.route("/logout")
def logout():
    session.pop("platform_admin", None)
    return redirect(url_for("platform.login"))


@platform_bp.route("/")
def dashboard():
    with get_connection() as conn:
        orgs = conn.execute(
            "SELECT id, name, slug, contact_email, is_active, created_at, admin_password_hash IS NOT NULL AS has_password FROM organizations ORDER BY created_at DESC"
        ).fetchall()

        org_stats = []
        for org in orgs:
            prop_count = conn.execute(
                "SELECT COUNT(*) FROM properties WHERE organization_id = ?", (org["id"],)
            ).fetchone()[0]
            unit_count = conn.execute(
                """SELECT COUNT(*) FROM units u
                   JOIN properties p ON u.property_id = p.id
                   WHERE p.organization_id = ?""",
                (org["id"],),
            ).fetchone()[0]
            last_activity = conn.execute(
                """SELECT MAX(a.timestamp) FROM audit_log a
                   WHERE a.entity_type = 'property'
                   AND a.entity_id IN (
                       SELECT id FROM properties WHERE organization_id = ?
                   )""",
                (org["id"],),
            ).fetchone()[0]
            org_stats.append(
                {
                    "id": org["id"],
                    "name": org["name"],
                    "slug": org["slug"],
                    "contact_email": org["contact_email"],
                    "is_active": org["is_active"],
                    "has_password": org["has_password"],
                    "created_at": org["created_at"],
                    "property_count": prop_count,
                    "unit_count": unit_count,
                    "last_activity": last_activity,
                }
            )

        total_properties = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        total_tenants = conn.execute("SELECT COUNT(*) FROM tenants WHERE status = 'active'").fetchone()[0]
        recent_error_count = conn.execute(
            "SELECT COUNT(*) FROM platform_errors WHERE created_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]
        parse_error_count = conn.execute(
            "SELECT COUNT(*) FROM statement_parse_errors WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]

    return render_template(
        "platform/dashboard.html",
        orgs=org_stats,
        total_properties=total_properties,
        total_tenants=total_tenants,
        recent_error_count=recent_error_count,
        parse_error_count=parse_error_count,
        active_tab="dashboard",
    )


@platform_bp.route("/errors")
def errors():
    error_type = request.args.get("type", "")
    with get_connection() as conn:
        if error_type:
            rows = conn.execute(
                "SELECT * FROM platform_errors WHERE error_type = ? ORDER BY created_at DESC LIMIT 200",
                (error_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM platform_errors ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        error_types = conn.execute(
            "SELECT DISTINCT error_type FROM platform_errors ORDER BY error_type"
        ).fetchall()

    return render_template(
        "platform/errors.html",
        errors=rows,
        error_types=[r["error_type"] for r in error_types],
        selected_type=error_type,
        active_tab="errors",
    )


@platform_bp.route("/orgs/new", methods=["POST"])
def create_org():
    """Create a new organisation. Email is the login identifier — must be unique."""
    from werkzeug.security import generate_password_hash
    name = request.form.get("name", "").strip()
    contact_email = request.form.get("contact_email", "").strip().lower() or None
    contact_phone = request.form.get("contact_phone", "").strip() or None
    slug = request.form.get("slug", "").strip().lower().replace(" ", "-")
    password = request.form.get("password", "").strip()
    if not name:
        flash("Organisation name is required.", "danger")
        return redirect(url_for("platform.dashboard"))
    if not contact_email:
        flash("A login email is required.", "danger")
        return redirect(url_for("platform.dashboard"))
    with get_connection() as conn:
        if conn.execute("SELECT id FROM organizations WHERE LOWER(contact_email) = ?", (contact_email,)).fetchone():
            flash(f"Email '{contact_email}' is already in use by another organisation.", "danger")
            return redirect(url_for("platform.dashboard"))
        if slug and conn.execute("SELECT id FROM organizations WHERE slug = ?", (slug,)).fetchone():
            flash(f"Slug '{slug}' is already taken.", "danger")
            return redirect(url_for("platform.dashboard"))
        org_id = generate_id("ORG")
        conn.execute(
            "INSERT INTO organizations (id, name, slug, contact_email, contact_phone, admin_password_hash) VALUES (?, ?, ?, ?, ?, ?)",
            (org_id, name, slug or None, contact_email, contact_phone,
             generate_password_hash(password) if password else None),
        )
    flash(f"Organisation '{name}' created." + (" Login password set." if password else " No login password set yet."), "success")
    return redirect(url_for("platform.dashboard"))


@platform_bp.route("/orgs/<org_id>/set-password", methods=["POST"])
def set_org_password(org_id):
    """Set or change an organisation's admin login password."""
    from werkzeug.security import generate_password_hash
    password = request.form.get("password", "").strip()
    if not password:
        flash("Password cannot be empty.", "danger")
        return redirect(url_for("platform.dashboard"))
    with get_connection() as conn:
        org = conn.execute("SELECT name FROM organizations WHERE id = ?", (org_id,)).fetchone()
        if not org:
            flash("Organisation not found.", "danger")
            return redirect(url_for("platform.dashboard"))
        conn.execute(
            "UPDATE organizations SET admin_password_hash = ? WHERE id = ?",
            (generate_password_hash(password), org_id),
        )
    flash(f"Password updated for '{org['name']}'.", "success")
    return redirect(url_for("platform.dashboard"))


@platform_bp.route("/orgs/<org_id>/set-email", methods=["POST"])
def set_org_email(org_id):
    """Update the login email for an organisation."""
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Email cannot be empty.", "danger")
        return redirect(url_for("platform.dashboard"))
    with get_connection() as conn:
        org = conn.execute("SELECT name FROM organizations WHERE id = ?", (org_id,)).fetchone()
        if not org:
            flash("Organisation not found.", "danger")
            return redirect(url_for("platform.dashboard"))
        conflict = conn.execute(
            "SELECT id FROM organizations WHERE LOWER(contact_email) = ? AND id != ?",
            (email, org_id),
        ).fetchone()
        if conflict:
            flash(f"Email '{email}' is already used by another organisation.", "danger")
            return redirect(url_for("platform.dashboard"))
        conn.execute("UPDATE organizations SET contact_email = ? WHERE id = ?", (email, org_id))
    flash(f"Login email updated for '{org['name']}'.", "success")
    return redirect(url_for("platform.dashboard"))


@platform_bp.route("/orgs/<org_id>/toggle", methods=["POST"])
def toggle_org(org_id):
    """Activate or deactivate an organisation."""
    with get_connection() as conn:
        org = conn.execute("SELECT id, name, is_active FROM organizations WHERE id = ?", (org_id,)).fetchone()
        if not org:
            flash("Organisation not found.", "danger")
            return redirect(url_for("platform.dashboard"))
        new_state = 0 if org["is_active"] else 1
        conn.execute("UPDATE organizations SET is_active = ? WHERE id = ?", (new_state, org_id))
    state_label = "activated" if new_state else "deactivated"
    flash(f"'{org['name']}' {state_label}.", "success")
    return redirect(url_for("platform.dashboard"))


@platform_bp.route("/parse-errors")
def parse_errors_view():
    org_filter = request.args.get("org_id", "")
    with get_connection() as conn:
        if org_filter:
            rows = conn.execute("""
                SELECT spe.*, o.name AS org_name
                FROM statement_parse_errors spe
                JOIN organizations o ON spe.org_id = o.id
                WHERE spe.org_id = ?
                ORDER BY spe.created_at DESC LIMIT 500
            """, (org_filter,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT spe.*, o.name AS org_name
                FROM statement_parse_errors spe
                JOIN organizations o ON spe.org_id = o.id
                ORDER BY spe.created_at DESC LIMIT 500
            """).fetchall()
        orgs = conn.execute(
            "SELECT id, name FROM organizations ORDER BY name"
        ).fetchall()
        # Count per org for the summary bar
        counts_by_org = {
            r[0]: r[1] for r in conn.execute(
                "SELECT org_id, COUNT(*) FROM statement_parse_errors GROUP BY org_id"
            ).fetchall()
        }

    return render_template(
        "platform/parse_errors.html",
        parse_errors=rows,
        orgs=orgs,
        selected_org=org_filter,
        counts_by_org=counts_by_org,
        bank_label=_bank_label,
        active_tab="parse_errors",
    )


@platform_bp.route("/statements/<statement_id>/pdf")
def download_statement_pdf(statement_id):
    """Serve a stored bank statement PDF for platform-side review."""
    with get_connection() as conn:
        stmt = conn.execute(
            "SELECT file_path, filename FROM bank_statements WHERE id = ?", (statement_id,)
        ).fetchone()
    if not stmt or not stmt["file_path"]:
        abort(404)
    if not os.path.exists(stmt["file_path"]):
        abort(404)
    return send_file(stmt["file_path"], mimetype="application/pdf", download_name=stmt["filename"])


@platform_bp.route("/impersonate/<org_id>", methods=["POST"])
def impersonate(org_id):
    with get_connection() as conn:
        org = conn.execute(
            "SELECT id, name FROM organizations WHERE id = ?", (org_id,)
        ).fetchone()
    if not org:
        flash("Organisation not found.", "danger")
        return redirect(url_for("platform.dashboard"))
    session["org_id"] = org["id"]
    session["admin_authenticated"] = True
    session["org_selection_done"] = True
    session.pop("property_id", None)
    flash(f"Now viewing as {org['name']}. Return to /platform to switch.", "info")
    return redirect("/")
