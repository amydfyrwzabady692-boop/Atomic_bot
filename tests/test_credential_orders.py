import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

import g2bulk
from credential_vault import (
    CredentialVaultError, decrypt_credentials, encrypt_credentials,
    mask_identifier,
)
from keyboards import freefire_products_keyboard


class CredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.old_key = os.environ.get('ACCOUNT_CREDENTIALS_KEY')
        os.environ['ACCOUNT_CREDENTIALS_KEY'] = Fernet.generate_key().decode()

    def tearDown(self):
        if self.old_key is None:
            os.environ.pop('ACCOUNT_CREDENTIALS_KEY', None)
        else:
            os.environ['ACCOUNT_CREDENTIALS_KEY'] = self.old_key

    def test_round_trip_never_exposes_plaintext_in_ciphertext(self):
        token = encrypt_credentials('buyer@example.com', 'Temp-Pass-123')
        self.assertNotIn('buyer@example.com', token)
        self.assertNotIn('Temp-Pass-123', token)
        self.assertEqual(
            decrypt_credentials(token),
            {
                'identifier': 'buyer@example.com',
                'password': 'Temp-Pass-123',
                'note': '',
            },
        )

    def test_wrong_key_cannot_decrypt(self):
        token = encrypt_credentials('buyer@example.com', 'Temp-Pass-123')
        os.environ['ACCOUNT_CREDENTIALS_KEY'] = Fernet.generate_key().decode()
        with self.assertRaises(CredentialVaultError):
            decrypt_credentials(token)

    def test_identifier_mask(self):
        self.assertEqual(mask_identifier('buyer@example.com'), 'bu***@example.com')


class G2BulkTurkeyPricingTests(unittest.TestCase):
    def setUp(self):
        g2bulk._products_cache.update(at=0.0, value=None)

    def test_60_try_is_derived_from_exact_live_300_try_product(self):
        payload = {
            'success': True,
            'products': [{
                'id': 686,
                'title': '300 TRY iTunes Turkey',
                'category_title': 'Apple iTunes Turkey',
                'unit_price': 6.64,
                'stock': 46,
            }],
        }
        with patch.object(g2bulk, '_request', return_value=payload):
            result = g2bulk.get_itunes_turkey_costs(force=True)
        self.assertTrue(result['ok'])
        self.assertEqual(str(result['costs'][300]), '6.640000')
        self.assertEqual(str(result['costs'][60]), '1.328000')
        self.assertEqual(result['source_product_id'], 686)


class CredentialMenuTests(unittest.TestCase):
    def test_freefire_menu_keeps_id_flow_and_adds_credential_flow(self):
        callbacks = [
            button.callback_data
            for row in freefire_products_keyboard().inline_keyboard
            for button in row
        ]
        self.assertIn('gems_by_id', callbacks)
        self.assertIn('gems_credentials', callbacks)


if __name__ == '__main__':
    unittest.main()
