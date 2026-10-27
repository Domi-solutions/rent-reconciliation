"""Send ONE test SMS through TextSMS to a phone you name — proves real delivery.

    ./venv/bin/python scripts/sms_test.py 0712345678
    ./venv/bin/python scripts/sms_test.py 0712345678 "Your own text, e.g. with a link https://…"

Asks for the API key (hidden as you type) and partner ID, so neither lands in
your shell history, the repo, or a chat. Nothing is saved. On the live server it
uses the key already set there and asks nothing:

    fly ssh console -a rent-reconciliation -C "python /app/scripts/sms_test.py 07XXXXXXXX"

It sends this one message even while AT_SMS_ENABLED=false holds everything else.
Uses the same send_sms() path the app uses; the attempt is logged to
platform_outbox in whichever database DATABASE_PATH points at (data/dev.db locally).
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit('Usage: ./venv/bin/python scripts/sms_test.py <your phone> ["optional message text"]')
    phone = sys.argv[1]
    text = sys.argv[2] if len(sys.argv) == 3 else "Domi test: if you can read this, SMS delivery works."

    os.environ.setdefault('DATABASE_PATH', 'data/dev.db')     # the live server sets its own
    os.environ['SMS_PROVIDER'] = 'textsms'
    os.environ['AT_SMS_ENABLED'] = 'true'
    # On the live server the key is already set (fly secrets): use it, ask for nothing.
    if not (os.environ.get('TEXTSMS_API_KEY') and os.environ.get('TEXTSMS_PARTNER_ID')):
        os.environ['TEXTSMS_API_KEY'] = getpass.getpass('TextSMS API key (hidden): ').strip()
        os.environ['TEXTSMS_PARTNER_ID'] = input('TextSMS partner ID: ').strip()
        sender = input('Sender ID (Enter to use the TextSMS default until yours is approved): ').strip()
        if sender:
            os.environ['SMS_SENDER_ID'] = sender

    from src.messaging.delivery import send_sms
    from src.utils.phone import normalize_to_e164
    if not normalize_to_e164(phone):
        sys.exit(f"'{phone}' is not a Kenyan mobile number this app recognises.")

    sent, failed, errors = send_sms([phone], text)
    if sent:
        print(f"Sent to {normalize_to_e164(phone)}. Check the phone — delivery can take a minute.")
    else:
        print("Not sent:", '; '.join(errors) or 'unknown error')
        sys.exit(1)


if __name__ == '__main__':
    main()
