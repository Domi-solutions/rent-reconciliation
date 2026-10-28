"""Filling message templates ({tenant_name}, {balance}, …) for one tenant.

The money line always says what the account is, in the tenant statement's words:
"Still owed: KES X", "Extra paid: KES X …" or "Nothing owed". A tenant who has paid
ahead is never told "Outstanding: KES -23,000" or asked to settle a balance.

Callers put the raw balance in the variables as `balance_value` (a number; positive =
owed). {balance} is then the amount owed, never negative; {extra_paid} the amount paid
ahead; {account} the whole money line.

Greetings use the first name: "Hi {tenant_name}" reads "Hi Peter", not "Hi PETER KAMAU".
{first_name} is available too.
"""

import re

# The money lines the built-in reminder templates use (also when an admin has kept them).
_OWED_LINE = re.compile(r'^[ \t]*(?:Total )?Outstanding:[ \t]*KES[ \t]*\{balance\}[ \t]*$', re.M | re.I)
# "Kindly settle the outstanding balance before …" / "Please ensure the outstanding amount is settled …"
_SETTLE_LINE = re.compile(r'^[ \t]*(?:Kindly|Please)[^\n]*\b(?:settle|settled)\b[^\n]*$', re.M | re.I)

NOTHING_TO_PAY = 'Nothing to pay now.'
_GREETING = re.compile(r'\b(Hi|Hello|Dear|Habari|Hujambo|Jambo)(\s+)\{tenant_name\}', re.I)


def _kes(n):
    return f"{abs(n):,.0f}"


def account_line(balance):
    if balance > 0.5:
        return f"Still owed: KES {_kes(balance)}"
    if balance < -0.5:
        return (f"Extra paid: KES {_kes(balance)} (paid more than your bills so far). "
                "It will be used for your next bill.")
    return 'Nothing owed. All your bills are paid.'


def fill(text, variables):
    if not text:
        return text
    values = dict(variables)
    raw = values.pop('balance_value', None)
    if raw is not None:
        balance = float(raw or 0)
        values['balance'] = _kes(max(balance, 0))
        values['extra_paid'] = _kes(min(balance, 0))
        values['account'] = account_line(balance)
        text = _OWED_LINE.sub('{account}', text)
        if balance <= 0.5:
            text = _SETTLE_LINE.sub(NOTHING_TO_PAY, text)
    if values.get('tenant_name'):
        values['first_name'] = str(values['tenant_name']).split()[0].title()
        text = _GREETING.sub(r'\1\2{first_name}', text)
    for k, v in values.items():
        text = text.replace('{' + k + '}', str(v))
    return text
