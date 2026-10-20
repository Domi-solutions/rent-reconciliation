"""The admin landing page puts actionable tenant work ahead of bank upload."""

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
            'property': {'name': 'Test Building'}, 'days_to_due': None,
            'inbox_awaiting_count': 0, 'flagged_claims_count': 0,
            'unassigned_stats': {'total_count': 0, 'total_amount': 0},
            'pending': [], 'awaiting_threads': [], 'arrears': [],
            'stats': {'total_arrears': 0, 'units_in_arrears': 0,
                      'total_units': 2, 'occupied_units': 2},
            'show_generate_charges_hint': False, 'current_month': '2026-10',
        }
        context.update(overrides)
        return self.jinja.get_template('dashboard.html').render(**context)

    def test_waiting_tenant_is_visible_before_monthly_workflow(self):
        page = self.render(inbox_awaiting_count=1, awaiting_threads=[{
            'id': 'CONV-1', 'subject': 'Water leak', 'unit_number': 'A1',
            'tenant_name': 'Tenant One', 'hours_waiting': 25,
        }], flagged_claims_count=2)
        self.assertLess(page.index('Needs follow-up'), page.index('Water leak'))
        self.assertLess(page.index('Water leak'), page.index('Monthly workflow'))
        self.assertIn('1 tenant conversation waiting for a reply', page)
        self.assertIn('2 payment claims flagged for review', page)
        self.assertNotIn('No tenant questions, payment exceptions', page)

    def test_quiet_state_does_not_show_a_false_alert(self):
        page = self.render()
        self.assertIn('No tenant questions, payment exceptions, or outstanding balances', page)
        self.assertIn('Every unit is settled', page)


if __name__ == '__main__':
    unittest.main()
