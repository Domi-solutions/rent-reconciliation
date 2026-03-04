"""
Admin routes for generating and viewing landlord reports.
"""
import json
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from src.database.db import get_connection, generate_id
from src.reports.landlord_report import generate_landlord_report, enrich_report_data

report_bp = Blueprint('reports', __name__, url_prefix='/reports')


def get_current_property(conn):
    """Get the currently selected property from session. Returns row or None."""
    pid = session.get('property_id')
    if pid:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ? AND status = 'active'", (pid,)
        ).fetchone()
        if prop:
            return prop
    session.pop('property_id', None)
    return None


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

                # Store report in database
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

        report_data = json.loads(report_row['report_data'])
        enrich_report_data(
            report_data, conn,
            report_row['property_id'],
            report_row['period_start'],
            report_row['period_end'],
        )

        return render_template('reports/preview.html',
                             property=prop,
                             report=report_data,
                             report_id=report_id,
                             period_start=report_row['period_start'],
                             period_end=report_row['period_end'],
                             created_at=report_row['created_at'])


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
            ORDER BY created_at DESC
        """, (property_id,)).fetchall()

        # Load report data for each to get headline numbers
        reports_with_summary = []
        for r in reports:
            report_data = json.loads(
                conn.execute(
                    "SELECT report_data FROM landlord_reports WHERE id = ?", (r['id'],)
                ).fetchone()[0]
            )
            reports_with_summary.append({
                'id': r['id'],
                'period_start': r['period_start'],
                'period_end': r['period_end'],
                'created_at': r['created_at'],
                'report_type': r['report_type'],
                'collection_rate': report_data.get('collection_rate', 0),
                'total_collected': report_data.get('collections', {}).get('total_verified', 0),
                'total_arrears': report_data.get('arrears', {}).get('total_arrears', 0),
            })

        return render_template('reports/history.html',
                             property=prop,
                             reports=reports_with_summary)
