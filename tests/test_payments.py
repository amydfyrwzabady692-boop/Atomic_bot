import sys
import types
import unittest
from unittest.mock import patch

# Keep this unit test independent from deployment-only python-dotenv.
if 'dotenv' not in sys.modules:
    dotenv_stub = types.ModuleType('dotenv')
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules['dotenv'] = dotenv_stub

import payments


class GatewayRequestTests(unittest.TestCase):
    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_post')
    def test_rejects_zero_amount_without_network_call(self, post, _merchant):
        authority, url, error = payments.request_payment(
            0, 'test', 'https://shop.example/callback'
        )
        self.assertIsNone(authority)
        self.assertIsNone(url)
        self.assertTrue(error)
        post.assert_not_called()

    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_post')
    def test_rejects_insecure_callback_without_network_call(self, post, _merchant):
        authority, url, error = payments.request_payment(
            10_000, 'test', 'http://shop.example/callback'
        )
        self.assertIsNone(authority)
        self.assertIsNone(url)
        self.assertIn('HTTPS', error)
        post.assert_not_called()

    @patch.object(payments, '_start_base', return_value='https://pay.example/')
    @patch.object(payments, '_base', return_value='https://api.example/')
    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(
        payments, '_post',
        return_value={'data': {'code': 100, 'authority': 'A123'}, 'errors': []},
    )
    def test_returns_authority_only_for_success_response(
        self, _post, _merchant, _base, _start_base
    ):
        authority, url, error = payments.request_payment(
            10_000, 'test', 'https://shop.example/callback'
        )
        self.assertEqual(authority, 'A123')
        self.assertEqual(url, 'https://pay.example/A123')
        self.assertIsNone(error)


class GatewayVerifyTests(unittest.TestCase):
    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_post')
    def test_rejects_invalid_amount_or_authority_without_network(self, post, _merchant):
        self.assertEqual(payments.verify_payment(0, 'A123'), (False, None))
        self.assertEqual(payments.verify_payment(10_000, ''), (False, None))
        post.assert_not_called()

    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_base', return_value='https://api.example/')
    @patch.object(payments, '_post', return_value={'data': {'code': 100}})
    def test_requires_reference_id(self, _post, _base, _merchant):
        self.assertEqual(payments.verify_payment(10_000, 'A123'), (False, None))

    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_base', return_value='https://api.example/')
    @patch.object(
        payments, '_post',
        return_value={'data': {'code': 100, 'ref_id': 987654}},
    )
    def test_accepts_verified_payment_with_reference(self, _post, _base, _merchant):
        self.assertEqual(
            payments.verify_payment(10_000, 'A123'),
            (True, '987654'),
        )

    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_base', return_value='https://api.example/')
    @patch.object(
        payments, '_post',
        return_value={'_transport_error': 'timeout'},
    )
    def test_transport_failure_is_not_treated_as_unpaid(
        self, _post, _base, _merchant
    ):
        self.assertEqual(
            payments.verify_payment_detailed(10_000, 'A123'),
            ('unavailable', None),
        )

    @patch.object(payments, '_merchant', return_value='merchant')
    @patch.object(payments, '_base', return_value='https://api.example/')
    @patch.object(
        payments, '_post',
        return_value={'data': {'code': -51}, 'errors': []},
    )
    def test_definitive_gateway_rejection_is_unpaid(
        self, _post, _base, _merchant
    ):
        self.assertEqual(
            payments.verify_payment_detailed(10_000, 'A123'),
            ('not_paid', None),
        )


if __name__ == '__main__':
    unittest.main()
