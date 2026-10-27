"""Which environment the app is running in — one answer for every check.

fly.toml sets FLASK_ENV=production; ENVIRONMENT=production is the older name
some code read on its own, and it was never set on Fly, so those checks
treated the live site as development. Either one now means production.
"""

import os


def is_production():
    return (os.environ.get('FLASK_ENV', '').strip().lower() == 'production'
            or os.environ.get('ENVIRONMENT', '').strip().lower() == 'production')
