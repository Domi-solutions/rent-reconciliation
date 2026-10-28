"""Provider-independent SMS checks; every network call is mocked."""

import io
import json
import os
import unittest
from unittest.mock import patch

from src.messaging import delivery


class SmsDeliveryOffline(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'SMS_PROVIDER': 'textsms',
            'TEXTSMS_API_KEY': 'test-only-key',
            'TEXTSMS_PARTNER_ID': 'test-only-partner',
            'SMS_SENDER_ID': 'MOWIN-APTS',
            'AT_SMS_ENABLED': 'true',
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_textsms_request_uses_normalized_number_and_sender(self):
        reply = io.BytesIO(b'{"responses":[{"response-code":200}]}')
        with patch.object(delivery.urllib.request, 'urlopen', return_value=reply) as urlopen:
            result = delivery._send_textsms(['+254712345678'], 'Payment confirmed.')
        self.assertEqual(result, [('+254712345678', True, None)])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://sms.textsms.co.ke/api/services/sendsms/')
        self.assertEqual(json.loads(request.data), {
            'apikey': 'test-only-key', 'partnerID': 'test-only-partner',
            'message': 'Payment confirmed.', 'shortcode': 'MOWIN-APTS',
            'mobile': '254712345678',
        })

    def test_delivery_switch_prevents_any_provider_call(self):
        with patch.dict(os.environ, {'AT_SMS_ENABLED': 'false'}), \
             patch.object(delivery, '_send_textsms') as sender, \
             patch.object(delivery, '_log_sms') as outbox:
            result = delivery.send_sms(['0712345678'], 'Hello')
        sender.assert_not_called()
        outbox.assert_called_once()
        self.assertEqual(outbox.call_args.args[2], 'simulated')
        self.assertEqual(result[:2], (0, 1))

    def test_provider_failure_is_reported_not_counted_as_sent(self):
        with patch.object(delivery, '_send_textsms', return_value=[('+254712345678', False, 'rejected')]), \
             patch.object(delivery, '_log_sms') as outbox:
            result = delivery.send_sms(['0712345678'], 'Hello')
        self.assertEqual(result, (0, 1, ['+254712345678: rejected']))
        outbox.assert_called_once_with('+254712345678', 'Hello', 'failed', 'rejected', 'MOWIN-APTS', None)


if __name__ == '__main__':
    unittest.main()
