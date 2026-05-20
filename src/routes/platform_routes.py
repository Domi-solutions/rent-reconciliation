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


@platform_bp.context_processor
def inject_pending_deletion_count():
    try:
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM properties WHERE status='deletion_requested'"
            ).fetchone()[0]
        return {"pending_deletion_count": n}
    except Exception:
        return {"pending_deletion_count": 0}


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
            "SELECT id, name, slug, contact_email, is_active, created_at, platform_fee_rate, admin_password_hash IS NOT NULL AS has_password FROM organizations ORDER BY created_at DESC"
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
                    "platform_fee_rate": float(org["platform_fee_rate"] or 0.01),
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
        open_alerts_critical = conn.execute(
            "SELECT COUNT(*) FROM platform_alerts WHERE status = 'open' AND severity = 'critical'"
        ).fetchone()[0]
        open_alerts_total = conn.execute(
            "SELECT COUNT(*) FROM platform_alerts WHERE status = 'open'"
        ).fetchone()[0]
        open_disputes = conn.execute(
            "SELECT COUNT(*) FROM tenant_disputes WHERE status = 'open'"
        ).fetchone()[0]

    return render_template(
        "platform/dashboard.html",
        orgs=org_stats,
        total_properties=total_properties,
        total_tenants=total_tenants,
        recent_error_count=recent_error_count,
        parse_error_count=parse_error_count,
        open_alerts_critical=open_alerts_critical,
        open_alerts_total=open_alerts_total,
        open_disputes=open_disputes,
        active_tab="dashboard",
    )


@platform_bp.route("/outbox")
def outbox():
    channel = request.args.get("channel", "")
    status = request.args.get("status", "")
    with get_connection() as conn:
        clauses = []
        params = []
        if channel:
            clauses.append("channel = ?")
            params.append(channel)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        entries = conn.execute(
            f"SELECT * FROM platform_outbox {where} ORDER BY created_at DESC LIMIT 300",
            params,
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM platform_outbox").fetchone()[0]
    return render_template(
        "platform/outbox.html",
        entries=entries,
        total=total,
        filter_channel=channel,
        filter_status=status,
        active_tab="outbox",
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
    try:
        platform_fee_rate = float(request.form.get("platform_fee_rate", "1").strip()) / 100
    except (ValueError, AttributeError):
        platform_fee_rate = 0.01
    platform_fee_rate = max(0.0, min(0.10, platform_fee_rate))  # clamp 0–10%

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
            """INSERT INTO organizations
               (id, name, slug, contact_email, contact_phone, admin_password_hash, platform_fee_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (org_id, name, slug or None, contact_email, contact_phone,
             generate_password_hash(password) if password else None,
             platform_fee_rate),
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


@platform_bp.route("/shadow-log")
def shadow_log():
    org_filter = request.args.get("org_id", "").strip()
    with get_connection() as conn:
        params = []
        where = ""
        if org_filter:
            where = "WHERE psl.org_id = ?"
            params.append(org_filter)
        rows = conn.execute(
            f"""SELECT psl.*, o.name AS org_name
                FROM platform_shadow_log psl
                LEFT JOIN organizations o ON o.id = psl.org_id
                {where}
                ORDER BY psl.created_at DESC LIMIT 500""",
            params,
        ).fetchall()
        orgs = conn.execute("SELECT id, name FROM organizations ORDER BY name").fetchall()
    return render_template("platform/shadow_log.html", rows=rows, orgs=orgs,
                           org_filter=org_filter, active_tab="shadow_log")


@platform_bp.route("/disputes")
def disputes():
    status_filter = request.args.get("status", "open")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT td.*, t.name AS tenant_name, p.name AS property_name, o.name AS org_name
               FROM tenant_disputes td
               LEFT JOIN tenants t ON t.id = td.tenant_id
               LEFT JOIN properties p ON p.id = td.property_id
               LEFT JOIN organizations o ON o.id = td.org_id
               WHERE td.status = ?
               ORDER BY td.created_at DESC""",
            (status_filter,),
        ).fetchall()
        open_count = conn.execute(
            "SELECT COUNT(*) FROM tenant_disputes WHERE status = 'open'"
        ).fetchone()[0]
    return render_template("platform/disputes.html", disputes=rows,
                           status_filter=status_filter, open_count=open_count,
                           active_tab="disputes")


@platform_bp.route("/disputes/<dispute_id>/resolve", methods=["POST"])
def resolve_dispute(dispute_id):
    note = request.form.get("note", "").strip()
    with get_connection() as conn:
        conn.execute(
            """UPDATE tenant_disputes
               SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolution_note = ?
               WHERE id = ?""",
            (note, dispute_id),
        )
    flash("Dispute marked resolved.", "success")
    return redirect(url_for("platform.disputes"))


@platform_bp.route("/alerts")
def alerts():
    status_filter = request.args.get("status", "open")
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT pa.*, o.name AS org_name, p.name AS property_name
               FROM platform_alerts pa
               LEFT JOIN organizations o ON o.id = pa.org_id
               LEFT JOIN properties p ON p.id = pa.property_id
               WHERE pa.status = ?
               ORDER BY pa.severity DESC, pa.created_at DESC""",
            (status_filter,),
        ).fetchall()
        open_counts = conn.execute(
            """SELECT severity, COUNT(*) AS cnt FROM platform_alerts
               WHERE status = 'open' GROUP BY severity"""
        ).fetchall()
    counts = {r["severity"]: r["cnt"] for r in open_counts}
    return render_template("platform/alerts.html", alerts=rows,
                           status_filter=status_filter, counts=counts,
                           active_tab="alerts")


@platform_bp.route("/alerts/<alert_id>/dismiss", methods=["POST"])
def dismiss_alert(alert_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE platform_alerts SET status = 'dismissed', dismissed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (alert_id,),
        )
    return ("", 204)


@platform_bp.route("/trust")
def trust():
    with get_connection() as conn:
        orgs = conn.execute(
            "SELECT id, name FROM organizations WHERE is_active = 1 ORDER BY name"
        ).fetchall()
        scores = []
        for org in orgs:
            open_critical = conn.execute(
                "SELECT COUNT(*) FROM platform_alerts WHERE org_id = ? AND status = 'open' AND severity = 'critical'",
                (org["id"],),
            ).fetchone()[0]
            open_warnings = conn.execute(
                "SELECT COUNT(*) FROM platform_alerts WHERE org_id = ? AND status = 'open' AND severity = 'warning'",
                (org["id"],),
            ).fetchone()[0]
            open_disputes = conn.execute(
                "SELECT COUNT(*) FROM tenant_disputes WHERE org_id = ? AND status = 'open'",
                (org["id"],),
            ).fetchone()[0]
            total_tenants = conn.execute(
                """SELECT COUNT(*) FROM tenants t
                   JOIN properties p ON p.id = t.property_id
                   WHERE p.organization_id = ? AND t.status = 'active'""",
                (org["id"],),
            ).fetchone()[0]
            raw = 100 - (open_critical * 20) - (open_warnings * 5) - (open_disputes * 10)
            trust_score = max(0, min(100, raw))
            scores.append({
                "id": org["id"],
                "name": org["name"],
                "trust_score": trust_score,
                "open_critical": open_critical,
                "open_warnings": open_warnings,
                "open_disputes": open_disputes,
                "total_tenants": total_tenants,
            })
    return render_template("platform/trust.html", scores=scores, active_tab="trust")


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


@platform_bp.route("/parsers", methods=["GET", "POST"])
def parsers():
    import tempfile
    import os as _os
    from decimal import Decimal
    from werkzeug.utils import secure_filename

    tab = request.form.get("tab", request.args.get("tab", "pdf"))
    result = None
    message = None

    if request.method == "POST":
        tab = request.form.get("tab", "pdf")

        if tab == "pdf":
            file = request.files.get("file")
            if file and file.filename:
                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                file.save(tmp.name)
                try:
                    from src.parsers.pdf_parser import parse_bank_statement
                    r = parse_bank_statement(tmp.name)
                    # Convert Transaction namedtuples to plain dicts for the template
                    txns = []
                    for t in r.get('transactions', []):
                        txns.append({
                            'transaction_date': getattr(t, 'transaction_date', None),
                            'reference': getattr(t, 'reference', None),
                            'amount': float(getattr(t, 'amount', 0) or 0),
                            'txn_type': getattr(t, 'txn_type', ''),
                            'sender': getattr(t, 'sender', None),
                            'narration': getattr(t, 'narration', None),
                            'unit_hint': getattr(t, 'unit_hint', None),
                        })
                    summary = r.get('summary') or {}
                    result = {
                        'success': r.get('success', False),
                        'statement_format': r.get('statement_format', 'unknown'),
                        'opening_balance': float(r['opening_balance']) if r.get('opening_balance') else None,
                        'closing_balance': float(r['closing_balance']) if r.get('closing_balance') else None,
                        'validation': r.get('validation') or {},
                        'summary': {k: (float(v) if isinstance(v, Decimal) else v) for k, v in summary.items()},
                        'transactions': txns,
                        'errors': r.get('errors') or [],
                        'warnings': r.get('warnings') or [],
                    }
                except Exception as e:
                    result = {'success': False, 'errors': [f'Parser exception: {e}'], 'transactions': [], 'summary': {}, 'validation': {}}
                finally:
                    if _os.path.exists(tmp.name):
                        _os.unlink(tmp.name)

        elif tab == "sms":
            message = request.form.get("message", "").strip()
            if message:
                from src.parsers.sms_parser import parse_mpesa_message
                r = parse_mpesa_message(message)
                amount = r.get('amount')
                ts = r.get('timestamp')
                result = {
                    'success': r.get('success', False),
                    'reference': r.get('reference'),
                    'amount': float(amount) if amount is not None else None,
                    'timestamp': ts.isoformat() if hasattr(ts, 'isoformat') else ts,
                    'format_detected': r.get('format_detected'),
                    'sender_hint': r.get('sender_hint'),
                    'parse_warnings': r.get('parse_warnings', []),
                    'error': r.get('error'),
                }

        elif tab == "excel":
            file = request.files.get("file")
            if file and file.filename:
                fname = secure_filename(file.filename)
                suffix = ".xlsx" if fname.endswith(".xlsx") else ".xls"
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                file.save(tmp.name)
                try:
                    from src.parsers.excel_parser import parse_tenant_excel
                    r = parse_tenant_excel(tmp.name)
                    result = {
                        'success': r.get('success', False),
                        'rows': r.get('rows', []),
                        'total_arrears': r.get('total_arrears', 0),
                        'errors': r.get('errors', []),
                        'warnings': r.get('warnings', []),
                    }
                finally:
                    _os.unlink(tmp.name)

    return render_template(
        "platform/parsers.html",
        active_tab_nav="parsers",
        tab=tab,
        result=result,
        message=message,
    )


@platform_bp.route("/deletions")
def deletions():
    with get_connection() as conn:
        pending = conn.execute("""
            SELECT p.id, p.name, p.address, p.deletion_requested_at,
                   o.name AS org_name, o.id AS org_id,
                   CAST(julianday('now') - julianday(p.deletion_requested_at) AS INTEGER) AS days_elapsed
            FROM properties p
            LEFT JOIN organizations o ON o.id = p.organization_id
            WHERE p.status = 'deletion_requested'
            ORDER BY p.deletion_requested_at ASC
        """).fetchall()

        orgs = conn.execute(
            "SELECT id, name FROM organizations WHERE is_active = 1 ORDER BY name"
        ).fetchall()

    return render_template("platform/deletions.html",
        pending=pending, orgs=orgs, active_tab="deletions")


@platform_bp.route("/properties/<property_id>/cancel-deletion", methods=["POST"])
def cancel_deletion(property_id):
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ? AND status = 'deletion_requested'", (property_id,)
        ).fetchone()
        if not prop:
            flash("Property not found or not pending deletion.", "danger")
            return redirect(url_for("platform.deletions"))

        conn.execute(
            "UPDATE properties SET status='active', deletion_requested_at=NULL WHERE id=?",
            (property_id,))

        from src.platform.guardian import platform_log, notify_owner_change
        notify_owner_change(conn, property_id,
            f'Deletion cancelled: {prop["name"]}',
            f'The deletion request for your property "{prop["name"]}" has been reviewed and cancelled by Domi. The property remains active.')
        platform_log(conn, 'property_deletion_cancelled', 'property', property_id,
            f'Deletion cancelled for "{prop["name"]}" by platform',
            org_id=prop['organization_id'], property_id=property_id)
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?,?,?,?,?)",
            ('property_deletion_cancelled', 'property', property_id,
             f'Platform cancelled deletion of "{prop["name"]}"', 'platform'))

    flash(f'Deletion cancelled. "{prop["name"]}" restored to active.', 'success')
    return redirect(url_for("platform.deletions"))


@platform_bp.route("/properties/<property_id>/transfer", methods=["POST"])
def transfer_property(property_id):
    new_org_id = request.form.get("new_org_id", "").strip()
    if not new_org_id:
        flash("Please select a target organisation.", "danger")
        return redirect(url_for("platform.deletions"))

    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ? AND status = 'deletion_requested'", (property_id,)
        ).fetchone()
        if not prop:
            flash("Property not found or not pending deletion.", "danger")
            return redirect(url_for("platform.deletions"))

        new_org = conn.execute(
            "SELECT name FROM organizations WHERE id = ?", (new_org_id,)
        ).fetchone()
        if not new_org:
            flash("Target organisation not found.", "danger")
            return redirect(url_for("platform.deletions"))

        conn.execute(
            "UPDATE properties SET status='active', organization_id=?, deletion_requested_at=NULL WHERE id=?",
            (new_org_id, property_id))

        from src.platform.guardian import platform_log, notify_owner_change
        notify_owner_change(conn, property_id,
            f'Property transferred: {prop["name"]}',
            f'Your property "{prop["name"]}" has been transferred to a new managing agency by Domi. All historical data has been preserved.')
        platform_log(conn, 'property_transferred', 'property', property_id,
            f'"{prop["name"]}" transferred from org {prop["organization_id"]} to {new_org_id}',
            org_id=new_org_id, property_id=property_id)
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?,?,?,?,?)",
            ('property_transferred', 'property', property_id,
             f'Transferred "{prop["name"]}" to {new_org["name"]}', 'platform'))

    flash(f'"{prop["name"]}" transferred to {new_org["name"]} with all data intact.', 'success')
    return redirect(url_for("platform.deletions"))


@platform_bp.route("/properties/<property_id>/approve-deletion", methods=["POST"])
def approve_deletion(property_id):
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ? AND status = 'deletion_requested'", (property_id,)
        ).fetchone()
        if not prop:
            flash("Property not found or not pending deletion.", "danger")
            return redirect(url_for("platform.deletions"))

        days_elapsed = conn.execute(
            "SELECT CAST(julianday('now') - julianday(deletion_requested_at) AS INTEGER) FROM properties WHERE id=?",
            (property_id,)
        ).fetchone()[0] or 0

        if days_elapsed < 14:
            flash(f"Cannot approve yet — {14 - days_elapsed} day(s) remaining in the 14-day owner dispute window.", "danger")
            return redirect(url_for("platform.deletions"))

        org_id = prop['organization_id']
        from src.platform.guardian import platform_log, raise_alert

        # Cascade delete in FK-safe order
        conn.execute("DELETE FROM payment_allocations WHERE payment_id IN (SELECT id FROM payments WHERE property_id=?)", (property_id,))
        conn.execute("DELETE FROM payment_allocations WHERE charge_id IN (SELECT id FROM rent_charges WHERE property_id=?)", (property_id,))
        conn.execute("DELETE FROM payment_transactions WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM payments WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM disbursements WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM balance_snapshots WHERE property_id=? OR unit_id IN (SELECT id FROM units WHERE property_id=?)", (property_id, property_id))
        conn.execute("DELETE FROM water_readings WHERE unit_id IN (SELECT id FROM units WHERE property_id=?)", (property_id,))
        conn.execute("DELETE FROM water_uploads WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM rent_charges WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM maintenance_issues WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM messages WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM checkin_responses WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM inbound_sessions WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM inbound_messages WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM tenants WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM payment_claims WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM bank_transactions WHERE statement_id IN (SELECT id FROM bank_statements WHERE property_id=?)", (property_id,))
        conn.execute("DELETE FROM statement_parse_errors WHERE statement_id IN (SELECT id FROM bank_statements WHERE property_id=?)", (property_id,))
        conn.execute("DELETE FROM bank_statements WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM property_owners WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM caretakers WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM landlord_reports WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM message_templates WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM owner_messages WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM reminder_schedules WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM reminder_settings WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM report_settings WHERE property_id=?", (property_id,))
        conn.execute("DELETE FROM units WHERE property_id=?", (property_id,))

        platform_log(conn, 'property_deleted', 'property', property_id,
            f'"{prop["name"]}" permanently deleted after 14-day window (platform approved)',
            org_id=org_id, property_id=property_id)
        raise_alert(conn, 'property_deleted',
            f'"{prop["name"]}" permanently deleted by platform after 14-day window.',
            org_id=org_id, property_id=property_id, severity='critical')
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?,?,?,?,?)",
            ('property_deleted', 'property', property_id,
             f'Platform approved deletion of "{prop["name"]}" after 14-day window', 'platform'))

        conn.execute("DELETE FROM properties WHERE id=?", (property_id,))

    flash(f'"{prop["name"]}" permanently deleted.', 'success')
    return redirect(url_for("platform.deletions"))
