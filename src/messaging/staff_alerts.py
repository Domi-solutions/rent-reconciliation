"""
Text staff that a tenant has written, so somebody opens Domi and replies.

The tenant chooses who is texted for a repair: the caretaker, management, or
both. Money and "something else" conversations are management-only (caretakers
cannot open them), so those always text management. Everyone who can read the
conversation still sees it when they sign in — the text only says where to look.

One text per unanswered stretch: the first tenant message texts staff and stamps
conversations.staff_alerted_at; further tenant messages send nothing until staff
reply (threads.add_message clears the stamp). The text carries the property, the
house, the conversation title and a link; the link still asks the person to sign in.
"""

from src.utils.phone import normalize_to_e164

NOTIFY = {
    'caretaker': 'The caretaker',
    'admin': 'Management',
    'both': 'The caretaker and management',
}


def choices(topic):
    """Who the tenant may ask to be texted for this topic."""
    return ('caretaker', 'admin', 'both') if topic == 'repair' else ('admin',)


def clean_choice(topic, notify):
    options = choices(topic)
    if notify in options:
        return notify
    return 'both' if topic == 'repair' else 'admin'


def due(conn, conversation_id, base_url):
    """The texts to send for a new tenant message: [(phones, text), ...], or [] if staff
    were already texted since their last reply. Stamps the conversation as alerted.
    Send the result after the transaction commits."""
    conv = conn.execute("""
        SELECT c.id, c.topic, c.subject, c.notify, c.staff_alerted_at, c.property_id,
               u.unit_number, p.name AS property_name, p.organization_id
        FROM conversations c JOIN properties p ON p.id = c.property_id
        LEFT JOIN units u ON u.id = c.unit_id
        WHERE c.id = ?
    """, (conversation_id,)).fetchone()
    if not conv or conv['staff_alerted_at']:
        return []
    notify = clean_choice(conv['topic'], conv['notify'])
    base = base_url.rstrip('/')
    head = f"{(conv['property_name'] or '').title()}: tenant in {conv['unit_number'] or 'a unit'} wrote \"{conv['subject'][:40]}\"."
    texts = []
    if notify in ('admin', 'both'):
        phones = _phones(conn.execute(
            "SELECT phone FROM admin_users WHERE org_id = ? AND is_active = 1 AND phone IS NOT NULL",
            (conv['organization_id'],)))
        if phones:
            texts.append((phones, f"{head} Reply: {base}/messages/inbox/{conv['id']}"))
    if notify in ('caretaker', 'both'):
        phones = _phones(conn.execute(
            "SELECT phone FROM caretakers WHERE property_id = ? AND phone IS NOT NULL", (conv['property_id'],)))
        if phones:
            texts.append((phones, f"{head} Reply: {base}/caretaker/{conv['property_id']}/repairs/{conv['id']}"))
    conn.execute("UPDATE conversations SET staff_alerted_at = CURRENT_TIMESTAMP WHERE id = ?", (conv['id'],))
    return texts


def send(texts):
    from src.messaging.delivery import send_sms_async
    for phones, text in texts:
        send_sms_async(phones, text)


def _phones(rows):
    out = []
    for r in rows:
        phone = normalize_to_e164(r['phone'] or '')
        if phone and phone not in out:
            out.append(phone)
    return out
