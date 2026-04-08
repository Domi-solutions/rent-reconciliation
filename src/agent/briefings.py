"""Briefing and digest text generators."""

from datetime import datetime, timedelta

from src.agent.detector import check_pending_tasks, detect_anomalies, detect_followups
from src.agent.llm import call_llm


def _fmt_kes(value):
    return f"KES {float(value or 0):,.0f}"


def _property_name(conn, property_id):
    row = conn.execute(
        "SELECT name FROM properties WHERE id = ?",
        (property_id,),
    ).fetchone()
    return row["name"] if row else property_id


def generate_weekly_digest(conn, property_id):
    """Generate weekly owner digest in plain text (max 10 lines)."""
    lines = []
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=6)
    prop_name = _property_name(conn, property_id)

    velocity = conn.execute(
        """
        SELECT COUNT(*) AS count_payments, COALESCE(SUM(amount), 0) AS total_verified
        FROM payments
        WHERE property_id = ?
          AND payment_date >= ?
          AND payment_date <= ?
        """,
        (property_id, week_ago.isoformat(), today.isoformat()),
    ).fetchone()
    lines.append(
        f"{prop_name} weekly digest ({week_ago.isoformat()} to {today.isoformat()})"
    )
    lines.append(
        f"Payment velocity: {velocity['count_payments']} payment(s), {_fmt_kes(velocity['total_verified'])} verified."
    )

    snap_date = (today - timedelta(days=7)).isoformat()
    arrears_rows = conn.execute(
        """
        SELECT
            ub.unit_id,
            ub.unit_number,
            ub.balance AS current_balance,
            bs.balance AS previous_balance
        FROM unit_balances ub
        LEFT JOIN balance_snapshots bs
          ON bs.unit_id = ub.unit_id
         AND bs.snapshot_date = (
            SELECT MAX(s2.snapshot_date)
            FROM balance_snapshots s2
            WHERE s2.unit_id = ub.unit_id
              AND s2.snapshot_date <= ?
         )
        WHERE ub.property_id = ?
        """,
        (snap_date, property_id),
    ).fetchall()
    improved = 0
    worsened = 0
    for row in arrears_rows:
        prev = float(row["previous_balance"] or 0)
        curr = float(row["current_balance"] or 0)
        if curr < prev:
            improved += 1
        elif curr > prev:
            worsened += 1
    if improved or worsened:
        lines.append(f"Arrears changes: {improved} improved, {worsened} worsened vs last week.")
    else:
        lines.append("Arrears changes: no unit-level movement vs last week snapshot.")

    aging_claims = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM payment_claims
        WHERE property_id = ?
          AND status = 'pending'
          AND created_at < datetime('now', '-5 days')
        """,
        (property_id,),
    ).fetchone()["c"]
    lines.append(f"Claim aging: {aging_claims} claim(s) pending over 5 days.")

    occupancy_changes = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM units
        WHERE property_id = ?
          AND status_changed_at >= datetime('now', '-7 days')
        """,
        (property_id,),
    ).fetchone()["c"]
    lines.append(f"Occupancy changes: {occupancy_changes} unit status change(s) in 7 days.")
    lines.append("Reply for unit-level details.")

    return "\n".join(lines[:10])


def generate_caretaker_briefing(conn, property_id, caretaker_name):
    """Generate daily caretaker briefing in plain text (max 10 lines)."""
    lines = []
    name = caretaker_name or "Caretaker"
    today = datetime.utcnow().date().isoformat()

    lines.append(f"Morning {name}.")
    top_arrears = conn.execute(
        """
        SELECT
            ub.unit_id,
            ub.unit_number,
            ub.balance,
            COALESCE(u.monthly_rent, 0) AS monthly_rent,
            COALESCE(t.name, 'No active tenant') AS tenant_name,
            COALESCE(t.phone, '-') AS tenant_phone
        FROM unit_balances ub
        JOIN units u ON u.id = ub.unit_id
        LEFT JOIN tenants t ON t.unit_id = ub.unit_id AND t.status = 'active'
        WHERE ub.property_id = ?
          AND ub.balance > 0
        ORDER BY ub.balance DESC
        LIMIT 3
        """,
        (property_id,),
    ).fetchall()
    if top_arrears:
        lines.append("Top arrears:")
        for row in top_arrears:
            monthly = float(row["monthly_rent"] or 0)
            months = int(float(row["balance"] or 0) // monthly) if monthly > 0 else 0
            lines.append(
                f"Unit {row['unit_number']} — {row['tenant_name']} ({row['tenant_phone']}), "
                f"{_fmt_kes(row['balance'])}, {months} month(s)."
            )
    else:
        lines.append("Top arrears: no active arrears units.")

    followups = detect_followups(conn, property_id)
    lines.append(f"Follow-up nudges due: {len(followups)}.")

    new_issues = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM maintenance_issues
        WHERE property_id = ?
          AND created_at >= datetime('now', '-1 day')
          AND status = 'open'
        """,
        (property_id,),
    ).fetchone()["c"]
    lines.append(f"New maintenance issues since yesterday: {new_issues}.")

    vacant_count = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM units
        WHERE property_id = ?
          AND status = 'vacant'
        """,
        (property_id,),
    ).fetchone()["c"]
    lines.append(f"Vacancy count: {vacant_count}.")
    lines.append(f"Date: {today}.")

    return "\n".join(lines[:10])


def generate_owner_briefing(conn, property_id):
    """Generate owner briefing in plain text."""
    prop_name = _property_name(conn, property_id)
    lines = [f"{prop_name} owner briefing"]

    expected_income = conn.execute(
        """
        SELECT COALESCE(SUM(monthly_rent + service_charge), 0) AS expected
        FROM units
        WHERE property_id = ? AND status = 'occupied'
        """,
        (property_id,),
    ).fetchone()["expected"]
    month_verified = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE property_id = ?
          AND payment_date >= date('now', 'start of month')
          AND payment_date <= date('now')
        """,
        (property_id,),
    ).fetchone()["total"]
    rate = (float(month_verified) / float(expected_income) * 100) if float(expected_income or 0) > 0 else 0.0
    lines.append(
        f"Collection rate this month: {rate:.1f}% "
        f"({_fmt_kes(month_verified)} verified vs {_fmt_kes(expected_income)} expected)."
    )

    arrears = conn.execute(
        """
        SELECT
            COUNT(*) AS units_in_arrears,
            COALESCE(SUM(balance), 0) AS total_arrears
        FROM unit_balances
        WHERE property_id = ?
          AND balance > 0
        """,
        (property_id,),
    ).fetchone()
    lines.append(
        f"Arrears summary: {arrears['units_in_arrears']} unit(s), {_fmt_kes(arrears['total_arrears'])} outstanding."
    )

    anomalies = detect_anomalies(conn, property_id)
    if anomalies:
        lines.append(f"Anomaly flags: {len(anomalies)} active.")
        lines.append(f"Top anomaly: {anomalies[0]['detail']}")
    else:
        lines.append("Anomaly flags: none today.")

    lines.append("Reply with any instruction to prioritize follow-up.")
    return "\n".join(lines)


def generate_admin_checklist(conn, property_id):
    """Generate admin checklist with pending tasks and days overdue."""
    tasks = check_pending_tasks(conn, property_id)
    lines = [f"Admin checklist for {_property_name(conn, property_id)}"]
    if not tasks:
        lines.append("No pending tasks.")
        return "\n".join(lines)

    days_by_task = {
        "bank_statement_missing": 1,
        "water_charges_missing": 1,
        "base_charges_missing": 1,
        "claims_aging": 7,
        "unassigned_transactions": 1,
        "monthly_report_missing": 1,
    }
    for task in tasks:
        overdue = days_by_task.get(task["task"], 1)
        lines.append(
            f"[{task['severity']}] {task['detail']} ({overdue} day(s) overdue baseline)."
        )

    return "\n".join(lines)


def generate_sentiment_summary(conn, property_id, period):
    """Aggregate monthly check-in sentiment into a short paragraph."""
    rows = conn.execute(
        """
        SELECT numeric_response, free_text
        FROM checkin_responses
        WHERE property_id = ?
          AND period = ?
        ORDER BY received_at DESC
        """,
        (property_id, period),
    ).fetchall()
    if not rows:
        return f"Tenant sentiment for {period}: no check-in responses recorded."

    total = len(rows)
    good = sum(1 for r in rows if int(r["numeric_response"] or 0) == 1)
    minor = sum(1 for r in rows if int(r["numeric_response"] or 0) == 2)
    urgent = sum(1 for r in rows if int(r["numeric_response"] or 0) == 3)

    comments = [r["free_text"].strip() for r in rows if (r["free_text"] or "").strip()]
    category_summary = "No comment themes identified."
    if comments:
        prompt = (
            "Classify these tenant comments into a compact category summary using labels: "
            "maintenance, noise, security, water, general. Return one short sentence only.\n\n"
            + "\n".join(f"- {c}" for c in comments[:20])
        )
        llm_out = call_llm(prompt, model="fast")
        category_summary = llm_out.strip() or category_summary

    return (
        f"Tenant sentiment for {period}: {total} response(s) — "
        f"{good} good, {minor} small issue, {urgent} urgent. "
        f"{category_summary}"
    )

