"""
Text staff that a tenant has written, so somebody opens Domi and replies.

Who is texted is management's setting, never the tenant's:
  - repairs → properties.repair_alerts_to: the caretaker (default), management, or both;
  - money and "something else" → management only. Those can be private (a lease
    question, a complaint about the caretaker) and caretakers cannot open them;
    management forwards anything that is really the caretaker's in the caretaker chat.
Everyone who can read a conversation still sees it when they sign in — the text
only says where to look.

One text per unanswered stretch: the first tenant message texts staff and stamps
conversations.staff_alerted_at; further tenant messages send nothing until staff
reply (threads.add_message clears the stamp).

Safety net: a repair texted to the caretaker only that has had no reply for
properties.repair_escalate_hours (default 24, 0 = off) texts management once
(escalations(), run by the repair_escalation_job). The text carries the property,
the house, the conversation title and a link; the link still asks for sign-in.
"""

from src.utils.phone import normalize_to_e164

WHO = {
    'caretaker': 'The caretaker',
    'admin': 'Management',
    'both': 'The caretaker and management',
}
ESCALATE_HOURS = (0, 12, 24, 48)


def who(prop, topic):
    """Who is texted about a new message on this topic at this property."""
    if topic != 'repair':
        return 'admin'
    route = prop['repair_alerts_to'] if 'repair_alerts_to' in prop.keys() else None
    return route if route in WHO else 'caretaker'


def tenant_line(prop, topic):
    """What the tenant is told on the new-message form."""
    route = who(prop, topic)
    return f"{WHO[route]} {'get' if route == 'both' else 'gets'} a text that you wrote."


def due(conn, conversation_id, base_url):
    """The texts to send for a new tenant message: [(phones, text), ...], or [] if staff
    were already texted since their last reply. Stamps the conversation as alerted.
    Send the result after the transaction commits."""
    conv = conn.execute("""
        SELECT c.id, c.topic, c.subject, c.staff_alerted_at, c.property_id,
               u.unit_number, p.name AS property_name, p.organization_id, p.repair_alerts_to
        FROM conversations c JOIN properties p ON p.id = c.property_id
        LEFT JOIN units u ON u.id = c.unit_id
        WHERE c.id = ?
    """, (conversation_id,)).fetchone()
    if not conv or conv['staff_alerted_at']:
        return []
    route = who(conv, conv['topic'])
    head = _head(conv)
    texts = []
    if route in ('admin', 'both'):
        phones = _management_phones(conn, conv['organization_id'])
        if phones:
            texts.append((phones, f"{head} Reply: {_link(base_url, conv)}"))
    if route in ('caretaker', 'both'):
        phones = _phones(conn.execute(
            "SELECT phone FROM caretakers WHERE property_id = ? AND phone IS NOT NULL", (conv['property_id'],)))
        if phones:
            texts.append((phones, f"{head} Reply: {(base_url or '').rstrip('/')}/caretaker/{conv['property_id']}"
                                  f"/repairs/{conv['id']}"))
    conn.execute("UPDATE conversations SET notify = ?, staff_alerted_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (route, conv['id']))
    return texts


def escalations(conn, base_url):
    """Repairs texted to the caretaker only that still have no staff reply after the
    property's escalate hours: text management once each. Returns texts to send after commit."""
    rows = conn.execute("""
        SELECT c.id, c.subject, c.property_id, u.unit_number, p.name AS property_name, p.organization_id,
               COALESCE(p.repair_escalate_hours, 24) AS hours
        FROM conversations c JOIN properties p ON p.id = c.property_id
        LEFT JOIN units u ON u.id = c.unit_id
        WHERE c.topic = 'repair' AND c.status = 'awaiting_reply' AND c.escalated_at IS NULL
          AND c.staff_alerted_at IS NOT NULL AND c.notify = 'caretaker'
          AND COALESCE(p.repair_escalate_hours, 24) > 0
          AND c.staff_alerted_at <= datetime('now', '-' || COALESCE(p.repair_escalate_hours, 24) || ' hours')
    """).fetchall()
    texts = []
    for c in rows:
        phones = _management_phones(conn, c['organization_id'])
        if phones:
            texts.append((phones, f"{(c['property_name'] or '').title()}: repair in {c['unit_number'] or 'a unit'} "
                                  f"\"{c['subject'][:40]}\" has had no reply for {c['hours']} hours. "
                                  f"Open: {_link(base_url, c)}"))
        conn.execute("UPDATE conversations SET escalated_at = CURRENT_TIMESTAMP WHERE id = ?", (c['id'],))
    return texts


def send(texts):
    from src.messaging.delivery import send_sms_async
    for phones, text in texts:
        send_sms_async(phones, text)


def _head(conv):
    return (f"{(conv['property_name'] or '').title()}: tenant in {conv['unit_number'] or 'a unit'} "
            f"wrote \"{conv['subject'][:40]}\".")


def _link(base_url, conv):
    return f"{(base_url or '').rstrip('/')}/messages/inbox/{conv['id']}"


def _management_phones(conn, org_id):
    return _phones(conn.execute(
        "SELECT phone FROM admin_users WHERE org_id = ? AND is_active = 1 AND phone IS NOT NULL", (org_id,)))


def _phones(rows):
    out = []
    for r in rows:
        phone = normalize_to_e164(r['phone'] or '')
        if phone and phone not in out:
            out.append(phone)
    return out
