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
    maintainer_digest_job,
    monthly_checkins_job,
    morning_briefings_job,
    process_payment_queue,
    weekly_digest_job,
)
from src.agent.inbound import process_inbound_message
from src.database.db import generate_id, get_connection

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
        "monthly_checkins_job": monthly_checkins_job,
        "process_payment_queue": process_payment_queue,
        "maintainer_digest_job": maintainer_digest_job,
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


@agent_bp.route("/simulator", methods=["GET", "POST"])
def simulator():
    """Inbound simulator for admin testing."""
    with get_connection() as conn:
        current_property = get_current_property(conn)
        properties = conn.execute(
            "SELECT id, name FROM properties WHERE status = 'active' ORDER BY name"
        ).fetchall()

        selected_property_id = request.form.get("property_id") or (current_property["id"] if current_property else None)
        selected_role = request.form.get("sender_role", "tenant")
        selected_unit_id = request.form.get("unit_id")
        sender_phone = request.form.get("sender_phone", "").strip()
        raw_text = request.form.get("raw_text", "").strip()

        units = []
        if selected_property_id:
            units = conn.execute(
                "SELECT id, unit_number FROM units WHERE property_id = ? ORDER BY unit_number",
                (selected_property_id,),
            ).fetchall()

        result = None
        if request.method == "POST":
            if not selected_property_id:
                flash("Select a property.", "error")
                return redirect(url_for("agent.simulator"))
            if not raw_text:
                flash("Type a message to simulate.", "error")
                return redirect(url_for("agent.simulator"))

            sender_entity_id = None
            if selected_role == "tenant" and selected_unit_id:
                tenant = conn.execute(
                    """
                    SELECT id FROM tenants
                    WHERE property_id = ? AND unit_id = ? AND status = 'active'
                    LIMIT 1
                    """,
                    (selected_property_id, selected_unit_id),
                ).fetchone()
                sender_entity_id = tenant["id"] if tenant else None

            inbound_id = generate_id("INB")
            conn.execute(
                """
                INSERT INTO inbound_messages
                (id, property_id, sender_phone, sender_role, sender_entity_id, raw_body, channel)
                VALUES (?, ?, ?, ?, ?, ?, 'sms')
                """,
                (
                    inbound_id,
                    selected_property_id,
                    sender_phone or "+254700000000",
                    selected_role,
                    sender_entity_id,
                    raw_text,
                ),
            )

            result = process_inbound_message(
                conn,
                message_id=inbound_id,
                raw_text=raw_text,
                context={
                    "property_id": selected_property_id,
                    "sender_role": selected_role,
                    "sender_entity_id": sender_entity_id,
                    "sender_phone": sender_phone or "+254700000000",
                    "unit_id": selected_unit_id,
                },
            )

        return render_template(
            "agent/simulator.html",
            properties=properties,
            units=units,
            selected_property_id=selected_property_id,
            selected_role=selected_role,
            selected_unit_id=selected_unit_id,
            sender_phone=sender_phone,
            raw_text=raw_text,
            result=result,
        )

