"""The admin overview: the month's money first, then only what needs a person (To do),
latest messages, who owes most, and the everyday actions."""

import os
import unittest

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


class DashboardAttention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        templates = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
        cls.jinja = Environment(loader=ChoiceLoader([
            DictLoader({'base.html': '{% block content %}{% endblock %}'}),
            FileSystemLoader(templates),
        ]), autoescape=True)
        cls.jinja.globals['url_for'] = lambda endpoint, **kwargs: '/' + endpoint

    def render(self, **overrides):
        context = {
            'property': {'name': 'Test Building'}, 'days_to_due': None, 'month_label': 'October',
            'inbox_awaiting_count': 0, 'flagged_claims_count': 0, 'mpesa_unmatched_count': 0,
            'expenses_waiting': 0, 'staff_chat_unread': 0, 'moving_out_count': 0, 'renewals_count': 0,
            'unassigned_stats': {'total_count': 0, 'total_amount': 0},
            'pending': [], 'latest_threads': [], 'behind': [], 'owes_most': [], 'behind_overdue': 0,
            'stats': {'total_units': 2, 'occupied_units': 2, 'expected_monthly': 36000, 'total_collected': 18000},
            'show_generate_charges_hint': False,
        }
        context.update(overrides)
        return self.jinja.get_template('dashboard.html').render(**context)

    def test_money_first_then_todo_then_messages_then_actions(self):
        page = self.render(inbox_awaiting_count=1, flagged_claims_count=2, latest_threads=[{
            'id': 'CONV-1', 'subject': 'Water leak', 'unit_number': 'A1', 'tenant_name': 'tenant one',
            'hours_waiting': 25, 'status': 'awaiting_reply', 'last_body': 'Dripping', 'last_message_at': '2026-10-27'}])
        self.assertIn('KES 18,000', page)
        self.assertIn('50% of this month', page)
        self.assertLess(page.index('Collected in October'), page.index('To do'))
        self.assertLess(page.index('To do'), page.index('Water leak'))
        self.assertLess(page.index('Water leak'), page.index('Quick actions'))
        self.assertIn('Tenant messages waiting', page)
        self.assertIn('Payments held for checking', page)
        self.assertNotIn('Expenses to approve', page)          # nothing waiting → no row
        self.assertNotIn('Nothing waiting', page)

    def test_quiet_state(self):
        page = self.render()
        self.assertIn('Nothing waiting ✓', page)
        self.assertIn('Nothing overdue', page)


if __name__ == '__main__':
    unittest.main()
