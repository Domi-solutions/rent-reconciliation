#!/usr/bin/env python3
"""
Register the paybill's C2B URLs with Safaricom (once, after Daraja go-live).

Safaricom then calls:
  validation   — before accepting a paybill payment (only if Safaricom has switched
                 external validation on for the shortcode); unknown account numbers
                 are turned away
  confirmation — after the payment; Domi books it to the tenant named by the
                 account number (tenant short code or unit number)

Both URLs carry MPESA_CALLBACK_TOKEN so a forged call is ignored. Run on the
Fly machine, where the secrets are set:

    fly ssh console -a rent-reconciliation -C "python3 scripts/register_c2b_urls.py"

To change the URLs later, delete the old ones in the M-Pesa org portal first —
Safaricom will not overwrite registered production URLs.
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.payments.daraja import _base_url, _get_access_token  # noqa: E402


def main():
    base = (os.environ.get('APP_BASE_URL') or '').rstrip('/')
    token = os.environ.get('MPESA_CALLBACK_TOKEN', '')
    shortcode = os.environ.get('DARAJA_SHORTCODE', '')
    if not (base and token and shortcode):
        sys.exit('Set APP_BASE_URL, MPESA_CALLBACK_TOKEN and DARAJA_SHORTCODE first.')
    payload = {
        'ShortCode': shortcode,
        'ResponseType': 'Completed',     # if validation is unreachable, accept — never lose a payment
        'ConfirmationURL': f'{base}/inbound/payment/c2b/confirmation?t={token}',
        'ValidationURL': f'{base}/inbound/payment/c2b/validation?t={token}',
    }
    req = urllib.request.Request(
        f'{_base_url()}/mpesa/c2b/v2/registerurl',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {_get_access_token()}', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f'Safaricom refused ({e.code}): {e.read().decode()}')


if __name__ == '__main__':
    main()
