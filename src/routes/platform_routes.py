"""Platform admin routes — Domi operator layer.

Auth: PLATFORM_ADMIN_PASSWORD env var.
Session key: session['platform_admin'] = True.
All /platform/* routes are exempt from the org admin before_request check.
"""
import os
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from src.database.db import generate_id, get_connection

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
            "SELECT id, name, slug, contact_email, is_active, created_at FROM organizations ORDER BY created_at DESC"
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
                """SELECT MAX(a.created_at) FROM audit_log a
                   JOIN properties p ON a.property_id = p.id
                   WHERE p.organization_id = ?""",
                (org["id"],),
            ).fetchone()[0]
            org_stats.append(
                {
                    "id": org["id"],
                    "name": org["name"],
                    "slug": org["slug"],
                    "contact_email": org["contact_email"],
                    "is_active": org["is_active"],
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

    return render_template(
        "platform/dashboard.html",
        orgs=org_stats,
        total_properties=total_properties,
        total_tenants=total_tenants,
        recent_error_count=recent_error_count,
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
    flash(f"Now viewing as {org['name']}. Return to /platform to switch.", "info")
    return redirect("/")
