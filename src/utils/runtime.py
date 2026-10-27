"""Which environment the app is running in — one answer for every check.

fly.toml sets FLASK_ENV=production; ENVIRONMENT=production is the older name
some code read on its own, and it was never set on Fly, so those checks
treated the live site as development. Either one now means production.
"""

import os


def is_production():
    return (os.environ.get('FLASK_ENV', '').strip().lower() == 'production'
            or os.environ.get('ENVIRONMENT', '').strip().lower() == 'production')


def is_demo():
    """The stakeholder demo site: made-up data, SMS codes shown on screen, M-Pesa simulated.
    Needs DEMO_MODE=true AND a non-production FLASK_ENV, so the live site can never
    become a demo by a stray setting."""
    return os.environ.get('DEMO_MODE', '').strip().lower() == 'true' and not is_production()
