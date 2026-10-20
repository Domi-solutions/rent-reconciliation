"""Tenant payment confirmation wording; a missing portal token never grants access."""


def payment_confirmation_body(tenant, amount, base_url):
    body = f"Hi {tenant['name']}, KES {float(amount):,.0f} payment confirmed."
    if tenant['access_token']:
        body += f"\nView your account: {base_url.rstrip('/')}/tenant/{tenant['access_token']}"
    return body
