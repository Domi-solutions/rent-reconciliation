"""Admin routes for agent preview and manual job triggers."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from src.agent.briefings import (
    generate_admin_checklist,
    generate_caretaker_briefing,
    generate_owner_briefing,
    generate_weekly_digest,
)
from src.agent.coordinator import (
    anomaly_check_job,
    daily_snapshot_job,
    morning_briefings_job,
    weekly_digest_job,
)
from src.database.db import get_connection

agent_bp = Blueprint("agent", __name__, url_prefix="/agent")


def get_current_property(conn):
    """Get current active property row from session."""
    pid = session.get("property_id")
    if pid:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ? AND status = 'active'",
            (pid,),
        ).fetchone()
        if prop:
            return prop
    session.pop("property_id", None)
    return None


def _load_property_or_redirect(conn, property_id):
    prop = conn.execute(
        "SELECT * FROM properties WHERE id = ? AND status = 'active'",
        (property_id,),
    ).fetchone()
    if not prop:
        flash("Property not found.", "error")
        return None
    return prop


@agent_bp.route("/digest/preview/<property_id>")
def digest_preview(property_id):
    with get_connection() as conn:
        prop = _load_property_or_redirect(conn, property_id)
        if not prop:
            return redirect(url_for("property_list"))
        content = generate_weekly_digest(conn, property_id)
        return render_template(
            "agent/preview_text.html",
            preview_title="Weekly Digest Preview",
            property=prop,
            content=content,
        )


@agent_bp.route("/briefing/caretaker/<property_id>/preview")
def caretaker_briefing_preview(property_id):
    caretaker_name = request.args.get("caretaker_name", "Caretaker")
    with get_connection() as conn:
        prop = _load_property_or_redirect(conn, property_id)
        if not prop:
            return redirect(url_for("property_list"))
        content = generate_caretaker_briefing(conn, property_id, caretaker_name)
        return render_template(
            "agent/preview_text.html",
            preview_title="Caretaker Briefing Preview",
            property=prop,
            content=content,
        )


@agent_bp.route("/briefing/owner/<property_id>/preview")
def owner_briefing_preview(property_id):
    with get_connection() as conn:
        prop = _load_property_or_redirect(conn, property_id)
        if not prop:
            return redirect(url_for("property_list"))
        content = generate_owner_briefing(conn, property_id)
        return render_template(
            "agent/preview_text.html",
            preview_title="Owner Briefing Preview",
            property=prop,
            content=content,
        )


@agent_bp.route("/checklist/<property_id>/preview")
def checklist_preview(property_id):
    with get_connection() as conn:
        prop = _load_property_or_redirect(conn, property_id)
        if not prop:
            return redirect(url_for("property_list"))
        content = generate_admin_checklist(conn, property_id)
        return render_template(
            "agent/preview_text.html",
            preview_title="Admin Checklist Preview",
            property=prop,
            content=content,
        )


@agent_bp.route("/trigger/<job_name>", methods=["POST"])
def trigger_job(job_name):
    jobs = {
        "daily_snapshot_job": daily_snapshot_job,
        "anomaly_check_job": anomaly_check_job,
        "morning_briefings_job": morning_briefings_job,
        "weekly_digest_job": weekly_digest_job,
    }
    fn = jobs.get(job_name)
    if not fn:
        flash("Unknown job name.", "error")
        return redirect(request.referrer or url_for("dashboard"))

    try:
        fn()
        flash(f"Triggered {job_name}.", "success")
    except Exception as exc:
        flash(f"Failed to trigger {job_name}: {exc}", "error")
    return redirect(request.referrer or url_for("dashboard"))

