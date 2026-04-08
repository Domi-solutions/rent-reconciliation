"""Task and anomaly detectors for the Domi agent layer."""

from datetime import datetime, timedelta


def _month_bounds():
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def check_pending_tasks(conn, property_id):
    """Detect pending admin tasks for the current month."""
    tasks = []
    month_start, month_end = _month_bounds()
    month_period = month_start.strftime("%Y-%m")
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    has_statement = conn.execute(
        """
        SELECT 1
        FROM bank_statements
        WHERE property_id = ?
          AND uploaded_at >= ?
          AND uploaded_at < ?
        LIMIT 1
        """,
        (property_id, month_start.isoformat(sep=" "), month_end.isoformat(sep=" ")),
    ).fetchone()
    if not has_statement:
        tasks.append({
            "task": "bank_statement_missing",
            "severity": "urgent",
            "detail": f"No bank statement uploaded for {month_period}.",
        })

    has_water = conn.execute(
        """
        SELECT 1
        FROM rent_charges
        WHERE property_id = ?
          AND period = ?
          AND charge_type = 'water'
        LIMIT 1
        """,
        (property_id, month_period),
    ).fetchone()
    if not has_water:
        tasks.append({
            "task": "water_charges_missing",
            "severity": "warning",
            "detail": f"No water charges recorded for {month_period}.",
        })

    has_rent_service = conn.execute(
        """
        SELECT 1
        FROM rent_charges
        WHERE property_id = ?
          AND period = ?
          AND charge_type IN ('rent', 'service')
        LIMIT 1
        """,
        (property_id, month_period),
    ).fetchone()
    if not has_rent_service:
        tasks.append({
            "task": "base_charges_missing",
            "severity": "urgent",
            "detail": f"No rent/service charges generated for {month_period}.",
        })

    pending_claims = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM payment_claims
        WHERE property_id = ?
          AND status = 'pending'
          AND created_at < datetime('now', '-7 days')
        """,
        (property_id,),
    ).fetchone()["c"]
    if pending_claims:
        tasks.append({
            "task": "claims_aging",
            "severity": "warning",
            "detail": f"{pending_claims} payment claim(s) pending for more than 7 days.",
        })

    unassigned_txns = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM bank_transactions bt
        JOIN bank_statements bs ON bs.id = bt.statement_id
        LEFT JOIN payments p ON p.bank_txn_id = bt.id
        WHERE bs.property_id = ?
          AND p.id IS NULL
        """,
        (property_id,),
    ).fetchone()["c"]
    if unassigned_txns:
        tasks.append({
            "task": "unassigned_transactions",
            "severity": "warning",
            "detail": f"{unassigned_txns} bank transaction(s) still unassigned.",
        })

    has_last_month_report = conn.execute(
        """
        SELECT 1
        FROM landlord_reports
        WHERE property_id = ?
          AND period_start >= ?
          AND period_start < ?
        LIMIT 1
        """,
        (
            property_id,
            last_month_start.date().isoformat(),
            month_start.date().isoformat(),
        ),
    ).fetchone()
    if not has_last_month_report:
        tasks.append({
            "task": "monthly_report_missing",
            "severity": "warning",
            "detail": f"No report saved for {last_month_start.strftime('%Y-%m')}.",
        })

    return tasks


def detect_anomalies(conn, property_id):
    """Detect financial and occupancy anomalies for a property."""
    anomalies = []

    water_rows = conn.execute(
        """
        SELECT rc.unit_id, u.unit_number, rc.amount
        FROM rent_charges rc
        JOIN units u ON u.id = rc.unit_id
        WHERE rc.property_id = ?
          AND rc.charge_type = 'water'
          AND rc.period = strftime('%Y-%m', 'now')
        """,
        (property_id,),
    ).fetchall()
    for row in water_rows:
        avg_row = conn.execute(
            """
            SELECT AVG(amount) AS avg_amount
            FROM (
                SELECT amount
                FROM rent_charges
                WHERE property_id = ?
                  AND unit_id = ?
                  AND charge_type = 'water'
                  AND period < strftime('%Y-%m', 'now')
                ORDER BY period DESC
                LIMIT 3
            )
            """,
            (property_id, row["unit_id"]),
        ).fetchone()
        avg_amount = float(avg_row["avg_amount"] or 0)
        current_amount = float(row["amount"] or 0)
        if avg_amount > 0 and current_amount > avg_amount * 1.3:
            anomalies.append({
                "type": "water_spike",
                "unit_id": row["unit_id"],
                "unit_number": row["unit_number"],
                "detail": f"Water charge KES {current_amount:.2f} is above 3-month average KES {avg_amount:.2f}.",
                "severity": "warning",
            })

    vacancy_rows = conn.execute(
        """
        SELECT id, unit_number, status_changed_at
        FROM units
        WHERE property_id = ?
          AND status = 'vacant'
        """,
        (property_id,),
    ).fetchall()
    now = datetime.utcnow()
    for row in vacancy_rows:
        if not row["status_changed_at"]:
            continue
        try:
            since = datetime.fromisoformat(str(row["status_changed_at"]).replace("Z", ""))
        except ValueError:
            continue
        days_vacant = (now - since).days
        for threshold in (60, 30, 14):
            if days_vacant >= threshold:
                anomalies.append({
                    "type": "vacancy_duration",
                    "unit_id": row["id"],
                    "unit_number": row["unit_number"],
                    "detail": f"Unit vacant for {days_vacant} days (threshold {threshold}).",
                    "severity": "urgent" if threshold >= 30 else "warning",
                })
                break

    month_period = datetime.utcnow().strftime("%Y-%m")
    arrears_rows = conn.execute(
        """
        SELECT ub.unit_id, ub.unit_number, ub.balance, COALESCE(u.monthly_rent, 0) AS monthly_rent
        FROM unit_balances ub
        JOIN units u ON u.id = ub.unit_id
        WHERE ub.property_id = ?
        """,
        (property_id,),
    ).fetchall()
    for row in arrears_rows:
        rent = float(row["monthly_rent"] or 0)
        if rent <= 0:
            continue
        months_behind = int(float(row["balance"] or 0) // rent)
        prev_row = conn.execute(
            """
            SELECT balance
            FROM balance_snapshots
            WHERE unit_id = ?
              AND snapshot_date < date('now')
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            (row["unit_id"],),
        ).fetchone()
        if not prev_row:
            continue
        prev_months = int(float(prev_row["balance"] or 0) // rent)
        if months_behind > prev_months:
            anomalies.append({
                "type": "arrears_threshold_crossing",
                "unit_id": row["unit_id"],
                "unit_number": row["unit_number"],
                "detail": f"Arrears moved from {prev_months} to {months_behind} month(s) behind in {month_period}.",
                "severity": "urgent",
            })

    curr_day = datetime.utcnow().day
    current_income = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE property_id = ?
          AND payment_date >= date('now', 'start of month')
          AND payment_date <= date('now')
        """,
        (property_id,),
    ).fetchone()["total"]
    prev_income = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE property_id = ?
          AND payment_date >= date('now', 'start of month', '-1 month')
          AND payment_date < date('now', 'start of month', '-1 month', '+' || ? || ' days')
        """,
        (property_id, curr_day),
    ).fetchone()["total"]
    if float(prev_income or 0) > 0 and float(current_income or 0) < float(prev_income or 0):
        anomalies.append({
            "type": "collection_pace_drop",
            "unit_id": "",
            "unit_number": "",
            "detail": (
                f"Verified income by day {curr_day} is KES {float(current_income):.2f}, "
                f"below last month KES {float(prev_income):.2f}."
            ),
            "severity": "warning",
        })

    return anomalies


def detect_followups(conn, property_id):
    """Detect follow-up nudges for caretaker operations."""
    followups = []
    today = datetime.utcnow()

    if today.day >= 15:
        period = today.strftime("%Y-%m")
        no_activity = conn.execute(
            """
            SELECT ub.unit_id, ub.unit_number, ub.balance
            FROM unit_balances ub
            WHERE ub.property_id = ?
              AND ub.balance > 0
              AND NOT EXISTS (
                SELECT 1 FROM payments p
                WHERE p.unit_id = ub.unit_id
                  AND p.payment_date >= date('now', 'start of month')
              )
              AND NOT EXISTS (
                SELECT 1 FROM payment_claims pc
                WHERE pc.unit_id = ub.unit_id
                  AND pc.created_at >= date('now', 'start of month')
              )
            """,
            (property_id,),
        ).fetchall()
        for row in no_activity:
            followups.append({
                "type": "no_payment_or_claim_by_day_15",
                "unit_id": row["unit_id"],
                "unit_number": row["unit_number"],
                "detail": f"Unit has arrears and no payment/claim activity in {period}.",
                "severity": "urgent",
            })

    stale_issues = conn.execute(
        """
        SELECT mi.id, u.unit_number
        FROM maintenance_issues mi
        LEFT JOIN units u ON u.id = mi.unit_id
        WHERE mi.property_id = ?
          AND mi.status = 'open'
          AND mi.created_at < datetime('now', '-7 days')
        """,
        (property_id,),
    ).fetchall()
    for row in stale_issues:
        followups.append({
            "type": "maintenance_stale",
            "issue_id": row["id"],
            "unit_number": row["unit_number"] or "-",
            "detail": "Open maintenance issue has no status update for more than 7 days.",
            "severity": "warning",
        })

    overdue_notes = conn.execute(
        """
        SELECT id, entity_id, details
        FROM audit_log
        WHERE action = 'followup_note'
          AND details LIKE '%due:%'
        """,
    ).fetchall()
    for row in overdue_notes:
        marker = "due:"
        details = row["details"] or ""
        idx = details.lower().find(marker)
        if idx == -1:
            continue
        due_text = details[idx + len(marker): idx + len(marker) + 10].strip()
        try:
            due_date = datetime.strptime(due_text, "%Y-%m-%d").date()
        except ValueError:
            continue
        if due_date < today.date():
            followups.append({
                "type": "followup_note_overdue",
                "entity_id": row["entity_id"],
                "detail": f"Follow-up due date {due_date.isoformat()} has passed.",
                "severity": "warning",
            })

    return followups

