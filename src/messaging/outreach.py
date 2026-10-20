"""Release gate for bulk/proactive tenant contact during onboarding."""

import os


def enabled():
    """Only an explicit opt-in permits invitations, reminders, and check-ins."""
    return os.environ.get('TENANT_OUTREACH_ENABLED', '').strip().lower() == 'true'
