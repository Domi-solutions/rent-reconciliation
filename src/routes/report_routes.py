"""
Admin routes for generating and viewing landlord reports.
"""
import calendar
import json
from datetime import datetime, timedelta, date as _date

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from src.database.db import get_connection, generate_id
from src.reports.landlord_report import generate_landlord_report, enrich_report_data
from src.parsers.banks.registry import bank_display_name

report_bp = Blueprint('reports', __name__, url_prefix='/reports')


def get_current_property(conn):
    """Get the currently selected property, scoped to session org if set."""
    pid = session.get('property_id')
    if pid:
        org_id = session.get('org_id')
        if org_id:
            prop = conn.execute(
                "SELECT * FROM properties WHERE id = ? AND status = 'active' AND organization_id = ?",
                (pid, org_id),
            ).fetchone()
        else:
            prop = conn.execute(
                "SELECT * FROM properties WHERE id = ? AND status = 'active'", (pid,)
            ).fetchone()
        if prop:
            return prop
    session.pop('property_id', None)
    return None


@report_bp.route('/generate-for-period', methods=['POST'])
def generate_for_period():
    """One-click report generation for a YYYY-MM period (called from monthly workflow)."""
    period = request.form.get('period', '').strip()
    import re
    if not period or not re.match(r'^\d{4}-\d{2}$', period):
        flash('Invalid period.', 'error')
        return redirect(url_for('tools_index'))

    try:
        y, m = int(period[:4]), int(period[5:7])
        period_start = f"{y:04d}-{m:02d}-01"
        period_end = f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
    except (ValueError, TypeError):
        flash('Invalid period.', 'error')
        return redirect(url_for('tools_index'))

    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'warning')
            return redirect(url_for('property_list'))
        property_id = prop['id']

        try:
            report_data = generate_landlord_report(conn, property_id, period_start, period_end)
            existing = conn.execute("""
                SELECT id FROM landlord_reports
                WHERE property_id = ? AND period_start = ? AND period_end = ?
            """, (property_id, period_start, period_end)).fetchone()
            if existing:
                report_id = existing['id']
                conn.execute("""
                    UPDATE landlord_reports
                    SET report_data=?, report_type='manual', needs_refresh=0,
                        refresh_reason=NULL, refreshed_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (json.dumps(report_data), report_id))
            else:
                report_id = generate_id('RPT')
                conn.execute("""
                    INSERT INTO landlord_reports
                    (id, property_id, period_start, period_end, report_type, report_data)
                    VALUES (?, ?, ?, ?, 'manual', ?)
                """, (report_id, property_id, period_start, period_end, json.dumps(report_data)))
            conn.execute("""
                INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)
                VALUES (?, ?, ?, ?, ?)
            """, ('report_generated', 'report', report_id,
                  f"Generated report for {prop['name']}: {period_start} to {period_end}", 'admin'))
            try:
                from src.messaging.owner_notify import notify_property_owners
                _base = request.host_url.rstrip('/')
                notify_property_owners(conn, property_id,
                    f"Report generated: {prop['name']} ({period_start} to {period_end}).\n"
                    f"View: {_base}/view/{property_id}/reports/{report_id}",
                    sent_by='Admin')
            except Exception:
                pass
        except Exception as e:
            flash(f'Error generating report: {str(e)}', 'error')
            return redirect(url_for('tools_index', period=period))

    flash(f'Report generated for {period}.', 'success')
    return redirect(url_for('reports.preview_report', report_id=report_id))


@report_bp.route('/generate', methods=['GET', 'POST'])
def generate_report():
    """Generate a landlord report for a date range."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'warning')
            return redirect(url_for('property_list'))

        property_id = prop['id']

        if request.method == 'POST':
            period_start = request.form.get('period_start')
            period_end = request.form.get('period_end')

            if not period_start or not period_end:
                flash('Please provide both start and end dates.', 'error')
                return redirect(url_for('reports.generate_report'))

            try:
                # Generate report
                report_data = generate_landlord_report(conn, property_id, period_start, period_end)

                # Upsert — one report per property per period
                existing = conn.execute("""
                    SELECT id FROM landlord_reports
                    WHERE property_id = ? AND period_start = ? AND period_end = ?
                """, (property_id, period_start, period_end)).fetchone()
                if existing:
                    report_id = existing['id']
                    conn.execute("""
                        UPDATE landlord_reports
                        SET report_data=?, report_type='manual', needs_refresh=0,
                            refresh_reason=NULL, refreshed_at=CURRENT_TIMESTAMP
                        WHERE id=?
                    """, (json.dumps(report_data), report_id))
                else:
                    report_id = generate_id('RPT')
                    conn.execute("""
                        INSERT INTO landlord_reports
                        (id, property_id, period_start, period_end, report_type, report_data)
                        VALUES (?, ?, ?, ?, 'manual', ?)
                    """, (report_id, property_id, period_start, period_end, json.dumps(report_data)))

                # Log to audit
                conn.execute("""
                    INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)
                    VALUES (?, ?, ?, ?, ?)
                """, ('report_generated', 'report', report_id,
                      f"Generated report for {prop['name']}: {period_start} to {period_end}", 'admin'))

                try:
                    from src.messaging.owner_notify import notify_property_owners
                    from flask import request
                    _base = request.host_url.rstrip('/')
                    _owner_msg = (
                        f"Report generated: {prop['name']} ({period_start} to {period_end}).\n"
                        f"View: {_base}/view/{property_id}/reports/{report_id}"
                    )
                    notify_property_owners(conn, property_id, _owner_msg, sent_by='Admin')
                except Exception:
                    pass

                flash(f'Report generated successfully.', 'success')
                return redirect(url_for('reports.preview_report', report_id=report_id))
            except Exception as e:
                flash(f'Error generating report: {str(e)}', 'error')
                return redirect(url_for('reports.generate_report'))

        # GET: Show form with default dates (previous calendar month)
        today = datetime.now().date()
        # First day of current month
        first_day_current = today.replace(day=1)
        # Last day of previous month
        last_day_prev = first_day_current - timedelta(days=1)
        # First day of previous month
        first_day_prev = last_day_prev.replace(day=1)

        default_start = first_day_prev.strftime('%Y-%m-%d')
        default_end = last_day_prev.strftime('%Y-%m-%d')

        return render_template('reports/generate.html',
                             property=prop,
                             default_start=default_start,
                             default_end=default_end)


@report_bp.route('/<report_id>')
def preview_report(report_id):
    """Preview a generated report (admin view)."""
    with get_connection() as conn:
        report_row = conn.execute(
            "SELECT * FROM landlord_reports WHERE id = ?", (report_id,)
        ).fetchone()

        if not report_row:
            flash('Report not found.', 'error')
            return redirect(url_for('reports.report_history'))

        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (report_row['property_id'],)
        ).fetchone()

        # Determine if report is in the live window (< 3 months since period_end)
        pe_date = datetime.strptime(report_row['period_end'], '%Y-%m-%d').date()
        today = _date.today()
        months_since = (today.year * 12 + today.month) - (pe_date.year * 12 + pe_date.month)
        is_live = months_since < 3

        if is_live:
            report_data = generate_landlord_report(
                conn, report_row['property_id'],
                report_row['period_start'], report_row['period_end']
            )
            conn.execute(
                "UPDATE landlord_reports SET report_data=? WHERE id=?",
                (json.dumps(report_data), report_id)
            )
        else:
            report_data = json.loads(report_row['report_data'])
            enrich_report_data(report_data, conn, report_row['property_id'],
                               report_row['period_start'], report_row['period_end'])

        org_id = prop['organization_id'] if prop else None
        unassigned_credits = []
        if org_id:
            _rows = conn.execute("""
                SELECT bs.id AS statement_id, bs.filename, bs.bank_format, bs.uploaded_at,
                       bs.period_start AS stmt_period_start, bs.period_end AS stmt_period_end,
                       COUNT(bt.id) AS txn_count, SUM(bt.amount) AS total_amount
                FROM bank_transactions bt
                JOIN bank_statements bs ON bs.id = bt.statement_id
                WHERE bs.org_id = ?
                  AND bt.txn_type = 'PAYBILL_CREDIT'
                  AND bt.txn_date >= ? AND bt.txn_date <= ?
                  AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
                GROUP BY bs.id
                HAVING COUNT(bt.id) > 0
                ORDER BY bs.uploaded_at DESC
            """, (org_id, report_row['period_start'], report_row['period_end'])).fetchall()
            unassigned_credits = [
                {**dict(r), 'bank_label': bank_display_name(r['bank_format'])}
                for r in _rows
            ]

        return render_template('reports/preview.html',
                             property=prop,
                             report=report_data,
                             report_id=report_id,
                             period_start=report_row['period_start'],
                             period_end=report_row['period_end'],
                             created_at=report_row['created_at'],
                             is_live=is_live,
                             report_needs_refresh=bool(report_row['needs_refresh']),
                             refresh_reason=report_row['refresh_reason'],
                             unassigned_credits=unassigned_credits)


@report_bp.route('/<report_id>/refresh', methods=['POST'])
def refresh_report(report_id):
    """Force-refresh a frozen report with current data. Admin only."""
    with get_connection() as conn:
        report_row = conn.execute(
            "SELECT * FROM landlord_reports WHERE id = ?", (report_id,)
        ).fetchone()
        if not report_row:
            flash('Report not found.', 'error')
            return redirect(url_for('reports.report_history'))

        report_data = generate_landlord_report(
            conn, report_row['property_id'],
            report_row['period_start'], report_row['period_end']
        )
        conn.execute("""
            UPDATE landlord_reports
            SET report_data=?, needs_refresh=0, refresh_reason=NULL, refreshed_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (json.dumps(report_data), report_id))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?,?,?,?,?)",
            ('report_force_refreshed', 'report', report_id,
             f'Period {report_row["period_start"]} to {report_row["period_end"]} | Manually refreshed by admin',
             'admin')
        )
        flash('Report updated with latest data.', 'success')
    return redirect(url_for('reports.preview_report', report_id=report_id))


@report_bp.route('/<report_id>/caretaker')
def caretaker_report(report_id):
    """Caretaker/operational report — occupancy + arrears follow-up, no financial amounts."""
    with get_connection() as conn:
        report_row = conn.execute(
            "SELECT * FROM landlord_reports WHERE id = ?", (report_id,)
        ).fetchone()

        if not report_row:
            flash('Report not found.', 'error')
            return redirect(url_for('reports.report_history'))

        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (report_row['property_id'],)
        ).fetchone()

        report_data = json.loads(report_row['report_data'])

        # Fetch current vacant units (not in JSON snapshot — operational current state)
        vacant_units = conn.execute("""
            SELECT u.unit_number, u.apartment_size
            FROM units u
            WHERE u.property_id = ? AND u.status = 'vacant'
            ORDER BY u.unit_number
        """, (report_row['property_id'],)).fetchall()

        return render_template('reports/caretaker_preview.html',
                             property=prop,
                             report=report_data,
                             report_id=report_id,
                             period_start=report_row['period_start'],
                             period_end=report_row['period_end'],
                             created_at=report_row['created_at'],
                             vacant_units=vacant_units)


@report_bp.route('/history')
def report_history():
    """List all past reports for current property."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'warning')
            return redirect(url_for('property_list'))

        property_id = prop['id']

        reports = conn.execute("""
            SELECT id, period_start, period_end, created_at, report_type
            FROM landlord_reports
            WHERE property_id = ?
            ORDER BY period_end DESC, created_at DESC
        """, (property_id,)).fetchall()

        # Load report data for each to get headline numbers
        today = _date.today()
        reports_with_summary = []
        for r in reports:
            full_row = conn.execute(
                "SELECT report_data, needs_refresh FROM landlord_reports WHERE id = ?", (r['id'],)
            ).fetchone()
            report_data = json.loads(full_row['report_data'])
            pe_date = datetime.strptime(r['period_end'], '%Y-%m-%d').date()
            months_since = (today.year * 12 + today.month) - (pe_date.year * 12 + pe_date.month)
            reports_with_summary.append({
                'id': r['id'],
                'period_start': r['period_start'],
                'period_end': r['period_end'],
                'created_at': r['created_at'],
                'report_type': r['report_type'],
                'collection_rate': report_data.get('collection_rate', 0),
                'total_collected': report_data.get('collections', {}).get('total_verified', 0),
                'total_arrears': report_data.get('arrears', {}).get('total_arrears', 0),
                'is_live': months_since < 3,
                'needs_refresh': bool(full_row['needs_refresh']),
            })

        return render_template('reports/history.html',
                             property=prop,
                             reports=reports_with_summary)
