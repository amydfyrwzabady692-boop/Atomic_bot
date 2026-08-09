import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

import g2bulk
import db
from credential_vault import (
    CredentialVaultError, decrypt_credentials, encrypt_credentials,
    mask_identifier,
)
from keyboards import freefire_products_keyboard
from keyboards import pay_method_keyboard
from handlers import gem_credentials, gems


class CredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.old_key = os.environ.get('ACCOUNT_CREDENTIALS_KEY')
        os.environ.pop('ACCOUNT_CREDENTIALS_KEY', None)

    def tearDown(self):
        if self.old_key is None:
            os.environ.pop('ACCOUNT_CREDENTIALS_KEY', None)
        else:
            os.environ['ACCOUNT_CREDENTIALS_KEY'] = self.old_key

    def test_plain_json_round_trip_without_key(self):
        self.assertTrue(__import__('credential_vault').is_configured())
        token = encrypt_credentials(
            'buyer@example.com', 'Temp-Pass-123', backup_code='ABCD-1234'
        )
        self.assertIn('buyer@example.com', token)
        self.assertEqual(
            decrypt_credentials(token),
            {
                'identifier': 'buyer@example.com',
                'password': 'Temp-Pass-123',
                'backup_code': 'ABCD-1234',
                'note': '',
            },
        )

    def test_optional_fernet_still_works_when_key_set(self):
        os.environ['ACCOUNT_CREDENTIALS_KEY'] = Fernet.generate_key().decode()
        token = encrypt_credentials(
            'buyer@example.com', 'Temp-Pass-123', backup_code='ABCD-1234'
        )
        self.assertNotIn('buyer@example.com', token)
        self.assertEqual(
            decrypt_credentials(token)['password'],
            'Temp-Pass-123',
        )

    def test_legacy_ciphertext_without_backup_still_decrypts(self):
        os.environ['ACCOUNT_CREDENTIALS_KEY'] = Fernet.generate_key().decode()
        from cryptography.fernet import Fernet
        import json
        key = os.environ['ACCOUNT_CREDENTIALS_KEY'].encode('ascii')
        raw = json.dumps(
            {'identifier': 'a@b.com', 'password': 'secret', 'note': ''},
            ensure_ascii=False, separators=(',', ':'),
        ).encode('utf-8')
        token = Fernet(key).encrypt(raw).decode('ascii')
        self.assertEqual(
            decrypt_credentials(token)['backup_code'],
            '',
        )

    def test_wrong_key_cannot_decrypt(self):
        os.environ['ACCOUNT_CREDENTIALS_KEY'] = Fernet.generate_key().decode()
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

    def test_credential_prices_use_exact_costs_and_default_40_percent_profit(self):
        self.assertEqual(str(db.CREDENTIAL_COST_USD[300]), '6.64')
        self.assertEqual(str(db.CREDENTIAL_COST_USD[60]), '1.328')
        self.assertEqual(
            db.compute_gem_sale_price(
                db.CREDENTIAL_COST_USD[300], 100_000, profit_percent=40
            ),
            930_000,
        )
        self.assertEqual(
            db.compute_gem_sale_price(
                db.CREDENTIAL_COST_USD[60], 100_000, profit_percent=40
            ),
            186_000,
        )

    def test_credential_profit_setting_is_independent_from_id_gem_profit(self):
        source = __import__('inspect').getsource(db.sync_gem_prices_daily)
        self.assertIn("profit_percent = 10", source)
        self.assertIn("credential_cost_for_package", source)
        self.assertIn("credential_profit_for_package", source)
        self.assertIn("by_credentials", source)
        helpers = __import__('inspect').getsource(db.get_credential_pricing_config)
        self.assertIn("credential_weekly_cost_usd", helpers)
        self.assertIn("credential_monthly_cost_usd", helpers)
        self.assertIn("credential_weekly_profit_percent", helpers)
        self.assertIn("credential_monthly_profit_percent", helpers)

    def test_weekly_and_monthly_costs_resolve_separately(self):
        with patch.object(db, 'get_setting', side_effect=lambda k, d='': {
            'credential_weekly_cost_usd': '1.5',
            'credential_monthly_cost_usd': '7.1',
            'credential_weekly_profit_percent': '25',
            'credential_monthly_profit_percent': '55',
            'credential_profit_percent': '40',
        }.get(k, d)):
            self.assertEqual(str(db.credential_cost_for_package(60, 'weekly')), '1.5')
            self.assertEqual(str(db.credential_cost_for_package(300, 'monthly')), '7.1')
            self.assertEqual(db.credential_profit_for_package(60, 'weekly'), 25)
            self.assertEqual(db.credential_profit_for_package(300, 'monthly'), 55)

class CredentialMenuTests(unittest.TestCase):
    def test_freefire_menu_keeps_id_flow_and_adds_credential_flow(self):
        callbacks = [
            button.callback_data
            for row in freefire_products_keyboard().inline_keyboard
            for button in row
        ]
        self.assertIn('gems_by_id', callbacks)
        self.assertIn('gems_credentials', callbacks)

    def test_both_flows_offer_same_three_payment_methods(self):
        callbacks = [
            button.callback_data
            for row in pay_method_keyboard(
                123, can_wallet=True, wallet_balance=500_000, remaining=100_000
            ).inline_keyboard
            for button in row
        ]
        self.assertIn('pay_zp_123', callbacks)
        self.assertIn('pay_card_123', callbacks)
        self.assertIn('pay_wallet_123', callbacks)

    def test_zarinpal_instant_note_is_only_in_id_flow(self):
        import inspect

        note = gems.ID_ZARINPAL_INSTANT_NOTE
        self.assertIn('زرین‌پال', note)
        self.assertIn('آنی', note)
        self.assertIn('ID_ZARINPAL_INSTANT_NOTE', inspect.getsource(gems.show_gem))
        self.assertIn('ID_ZARINPAL_INSTANT_NOTE', inspect.getsource(gems.gem_confirm))
        self.assertNotIn(note, inspect.getsource(gem_credentials))
        self.assertIn('CRED_BACKUP', inspect.getsource(gem_credentials))
        self.assertIn('backup_code', inspect.getsource(gem_credentials))
        self.assertIn('cred_backup_skip', inspect.getsource(gem_credentials))


class CredentialAdminActionTests(unittest.TestCase):
    def test_admin_panel_has_done_incomplete_and_wallet_refund(self):
        from pathlib import Path

        source = Path('handlers/admin_extended.py').read_text(encoding='utf-8')
        self.assertIn("admx_creddone_", source)
        self.assertIn("admx_credbad_", source)
        self.assertIn("admx_credrefundask_", source)
        self.assertIn("admx_credrefundok_", source)
        self.assertIn("admin_cancel_stuck_order", source)
        self.assertIn("admx_pricing", source)
        self.assertIn("admi_credprofit_weekly", source)
        self.assertIn("admi_credcost_monthly", source)
        self.assertIn("سفارش #{order_id} با موفقیت انجام شد", source)
        self.assertIn("به کیف پولت برگشت", source)

    def test_fulfill_keeps_credential_orders_manual_not_g2b_auto(self):
        import inspect

        source = inspect.getsource(db.fulfill_order)
        self.assertIn("if not auto_deliver:", source)
        self.assertIn("itunes_try:", source)
        complete = inspect.getsource(db.admin_complete_credential_order)
        reject = inspect.getsource(db.admin_reject_credential_info)
        cancel = inspect.getsource(db.admin_cancel_stuck_order)
        reject = inspect.getsource(db.admin_reject_credential_info)
        cancel = inspect.getsource(db.admin_cancel_stuck_order)
        self.assertIn("\"CredentialStatus\"='completed'", complete)
        self.assertIn("'needs_info'", reject)
        self.assertNotIn('CredentialCiphertext"=NULL', reject)
        self.assertIn("لغو توسط ادمین سفارش", cancel)
        self.assertIn("by_credentials", cancel)


if __name__ == '__main__':
    unittest.main()
