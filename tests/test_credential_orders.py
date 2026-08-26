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
        self.assertNotIn('get_credential_support_contact', inspect.getsource(gem_credentials))
        self.assertIn('بعد از پرداخت، دسترسی به آیدی پشتیبان باز می‌شود', inspect.getsource(gem_credentials))
        self.assertNotIn('iTunes Turkey', inspect.getsource(gem_credentials))
        self.assertIn('_backup_footer_text', inspect.getsource(gem_credentials))
        self.assertIn('myaccount.google.com', inspect.getsource(gem_credentials))
        from keyboards import (
            credential_backup_keyboard,
            credential_post_pay_support_keyboard,
        )
        pre_pay = credential_backup_keyboard()
        pre_callbacks = [
            getattr(btn, 'callback_data', None)
            for row in pre_pay.inline_keyboard
            for btn in row
        ]
        pre_urls = [
            getattr(btn, 'url', None)
            for row in pre_pay.inline_keyboard
            for btn in row
        ]
        self.assertIn('cred_backup_skip', pre_callbacks)
        self.assertTrue(all(url is None for url in pre_urls))
        labels = [
            getattr(btn, 'text', '')
            for row in pre_pay.inline_keyboard
            for btn in row
        ]
        self.assertTrue(any('نیاز به راهنمایی' in str(t) for t in labels))
        self.assertIn('https://myaccount.google.com', inspect.getsource(gem_credentials))
        self.assertIn('Accounts Center', inspect.getsource(gem_credentials))
        self.assertIn('Настройки', inspect.getsource(gem_credentials))
        self.assertIn('CRED_QUANTITY', inspect.getsource(gem_credentials.credential_conversation_handler))
        self.assertIn('parse_credential_quantity', inspect.getsource(gem_credentials))
        self.assertIn('تعداد: *{qty}* عدد', inspect.getsource(gem_credentials._show_confirm))
        post_pay = credential_post_pay_support_keyboard(42, 'lookurback')
        post_urls = [
            getattr(btn, 'url', None)
            for row in post_pay.inline_keyboard
            for btn in row
        ]
        self.assertIn('https://t.me/lookurback', post_urls)
        self.assertIn(
            '_send_credential_post_pay_support',
            inspect.getsource(__import__('handlers.payment', fromlist=['payment'])),
        )


class CredentialQuantityTests(unittest.TestCase):
    def test_parses_persian_digits_and_rejects_out_of_range(self):
        self.assertEqual(gem_credentials.parse_credential_quantity('۳'), 3)
        self.assertEqual(gem_credentials.parse_credential_quantity('12'), 12)
        with self.assertRaises(ValueError):
            gem_credentials.parse_credential_quantity('0')
        with self.assertRaises(ValueError):
            gem_credentials.parse_credential_quantity('abc')
        with self.assertRaises(ValueError):
            gem_credentials.parse_credential_quantity('51')

    def test_order_helper_multiplies_quantity_and_restores_stock(self):
        import inspect
        create = inspect.getsource(db.create_credential_gem_order_atomic)
        release = inspect.getsource(db._release_manual_gem_reservations)
        notify = inspect.getsource(__import__('handlers.payment', fromlist=['payment'])._notify_credential_sale)
        self.assertIn('quantity=1', create)
        self.assertIn('"Stock"="Stock"-%s', create)
        self.assertIn('unit_price * quantity', create)
        self.assertIn('COALESCE(oi."Quantity",1)', inspect.getsource(db.get_credential_order))
        self.assertIn('"Stock"="Stock"+%s', release)
        self.assertIn('تعداد: {qty} عدد از این بسته', notify)


class CredentialAdminActionTests(unittest.TestCase):
    def test_admin_panel_has_done_incomplete_and_wallet_refund(self):
        from pathlib import Path

        source = Path('handlers/admin_extended.py').read_text(encoding='utf-8')
        self.assertIn("admx_creddone_", source)
        self.assertIn("admx_credbad_", source)
        self.assertIn("admx_credrefundask_", source)
        self.assertIn("admx_credrefundok_", source)
        self.assertIn("_credential_reveal_html", source)
        self.assertIn("CopyTextButton", source)
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


class UidGiftCredentialTests(unittest.TestCase):
    def test_gift_package_is_detected_without_account_secrets(self):
        self.assertTrue(db.is_uid_gift_credential_package('gift', 90_004, 'Booyah Pass Gift'))
        self.assertFalse(db.is_uid_gift_credential_package('weekly', 60, 'itunes_try:60'))
        self.assertIsNone(db.credential_cost_for_package(90_004, 'gift'))

    def test_seed_and_list_include_booyah_pass_gift(self):
        import inspect
        source = inspect.getsource(db.ensure_admin_schema)
        self.assertIn('Booyah Pass Gift', source)
        self.assertIn('299000', source)
        self.assertIn("'gift'", inspect.getsource(db.get_gems_by_credentials))
        create = inspect.getsource(db.create_credential_uid_gift_order_atomic)
        self.assertIn('"GameUID"', create)
        self.assertIn("'uid'", create)
        self.assertIn("'awaiting_payment'", create)

    def test_buy_flow_asks_uid_not_password(self):
        import inspect
        source = inspect.getsource(gem_credentials)
        self.assertIn('CRED_UID', source)
        self.assertIn('parse_game_uid', source)
        self.assertIn('create_credential_uid_gift_order_atomic', source)
        self.assertIn('رمز اکانت لازم نیست', source)
        self.assertIn('پیوی ادمین', source)

    def test_post_pay_keyboard_sends_user_to_admin_pv(self):
        from keyboards import credential_post_pay_support_keyboard
        markup = credential_post_pay_support_keyboard(77, 'lookurback', mode='gift')
        texts = [btn.text for row in markup.inline_keyboard for btn in row]
        urls = [getattr(btn, 'url', None) for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any('پیوی ادمین' in str(t) and '77' in str(t) for t in texts))
        self.assertIn('https://t.me/lookurback', urls)
        weekly = credential_post_pay_support_keyboard(77, 'lookurback')
        weekly_texts = [btn.text for row in weekly.inline_keyboard for btn in row]
        self.assertTrue(any('بک‌آپ' in str(t) for t in weekly_texts))


if __name__ == '__main__':
    unittest.main()
