"""Phone numbers: digits only — a placeholder like 07XXXXXXXX is not a number."""

import unittest

from src.utils.phone import normalize_to_e164


class Phone(unittest.TestCase):
    def test_kenyan_forms(self):
        for raw in ('0712345678', '+254712345678', '254712345678', '0712 345 678', '0712-345-678'):
            self.assertEqual(normalize_to_e164(raw), '+254712345678', raw)

    def test_rejects_letters_and_wrong_lengths(self):
        for raw in ('07XXXXXXXX', '+2547XXXXXXXX', '07123', '+2547123456789', '', None):
            self.assertIsNone(normalize_to_e164(raw), raw)


if __name__ == '__main__':
    unittest.main()
