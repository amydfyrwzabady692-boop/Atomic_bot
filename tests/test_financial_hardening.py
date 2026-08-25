import inspect
import unittest
from unittest.mock import patch

import db
from keyboards import (
    admin_card_confirm_keyboard,
    admin_card_keyboard,
    admin_wallet_card_confirm_keyboard,
    admin_wallet_card_keyboard,
    wallet_charge_pay_keyboard,
    zarinpal_pay_keyboard,
)


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.rowcount = 1 if 'UPDATE "WalletTransactions"' in sql else 0

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _FakeConnection:
    def __init__(self, rows):
        self.cur = _FakeCursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


class WalletGatewayCreditTests(unittest.TestCase):
    def test_rejects_missing_gateway_reference_without_database_access(self):
        with patch.object(db, 'get_conn') as get_conn:
            result = db.complete_wallet_charge_by_authority(
                'A' * 36, verified_amount=100_000, ref_id=''
            )
        self.assertEqual(result, (False, None, 0, 0))
        get_conn.assert_not_called()

    def test_rejects_gateway_amount_mismatch_without_credit(self):
        conn = _FakeConnection([
            (7, 3, 100_000, False, 100_000, None, None),
        ])
        with patch.object(db, 'get_conn', return_value=conn):
            result = db.complete_wallet_charge_by_authority(
                'A' * 36, verified_amount=99_000, ref_id='12345'
            )
        self.assertEqual(result, (False, None, 0, 0))
        self.assertFalse(conn.committed)
        self.assertFalse(any(
            'UPDATE "Wallets"' in sql for sql, _params in conn.cur.executed
        ))

    def test_credits_exact_amount_and_persists_gateway_proof(self):
        conn = _FakeConnection([
            (7, 3, 100_000, False, 100_000, None, None),
            (11, 25_000),
        ])
        with patch.object(db, 'get_conn', return_value=conn):
            result = db.complete_wallet_charge_by_authority(
                'A' * 36, verified_amount=100_000, ref_id='12345'
            )
        self.assertEqual(result, (True, 11, 100_000, 125_000))
        self.assertTrue(conn.committed)
        proof_updates = [
            params for sql, params in conn.cur.executed
            if 'UPDATE "WalletTransactions"' in sql
        ]
        self.assertEqual(proof_updates, [('12345', 7)])


class FinancialKeyboardTests(unittest.TestCase):
    @staticmethod
    def _callbacks(markup):
        return [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]

    def test_order_receipt_requires_review_before_final_action(self):
        self.assertEqual(
            self._callbacks(admin_card_keyboard(42)),
            ['admin_review_ok_42', 'admin_review_no_42'],
        )
        self.assertEqual(
            self._callbacks(admin_card_confirm_keyboard(42, 'ok')),
            ['admin_ok_42', 'admin_review_back_42'],
        )

    def test_wallet_receipt_requires_review_before_final_action(self):
        self.assertEqual(
            self._callbacks(admin_wallet_card_keyboard(8)),
            ['wadmin_review_ok_8', 'wadmin_review_no_8'],
        )
        self.assertEqual(
            self._callbacks(admin_wallet_card_confirm_keyboard(8, 'no')),
            ['wadmin_no_8', 'wadmin_review_back_8'],
        )

    def test_wallet_gateway_has_direct_link_and_persistent_transaction_callback(self):
        markup = wallet_charge_pay_keyboard(77, 'https://payment.zarinpal.com/pay')
        self.assertEqual(
            markup.inline_keyboard[0][0].url,
            'https://payment.zarinpal.com/pay',
        )
        self.assertIn('wchk_77', self._callbacks(markup))

    def test_order_gateway_offers_safe_method_change(self):
        markup = zarinpal_pay_keyboard(
            42, 'https://payment.zarinpal.com/pg/StartPay/AUTH'
        )
        self.assertEqual(
            markup.inline_keyboard[0][0].url,
            'https://payment.zarinpal.com/pg/StartPay/AUTH',
        )
        self.assertIn('change_pay_42', self._callbacks(markup))
        self.assertNotIn('cancel_order_42', self._callbacks(markup))


class PaymentMethodChangeSafetyTests(unittest.TestCase):
    def test_detached_authority_becomes_wallet_credit_not_second_delivery(self):
        database_source = inspect.getsource(db.detach_order_authority_to_wallet)
        callback_source = inspect.getsource(
            __import__('handlers.payment', fromlist=['process_zarinpal_callback'])
            .process_zarinpal_callback
        )
        self.assertIn('_park_zarinpal_authority_for_order', database_source)
        self.assertIn('"PaymentAuthority"=NULL', database_source)
        self.assertIn('"PaymentMethod"=\\\'pending\\\'', database_source)
        self.assertNotIn('"PaymentMethod"=NULL', database_source)
        self.assertNotIn('"PaymentExpiresAt"=NULL', database_source)
        helper_source = inspect.getsource(db._ensure_pending_gateway_wallet_charge)
        self.assertIn('"WalletTransactions"', helper_source)
        self.assertIn("charge", helper_source)
        self.assertIn('complete_wallet_charge_by_authority', callback_source)
        self.assertIn('detached gateway credited to wallet', callback_source)
        self.assertIn('recover_late_zarinpal', callback_source)


class ReceiptInputTests(unittest.TestCase):
    def test_receipt_requires_exactly_one_target_and_an_image(self):
        with self.assertRaises(ValueError):
            db.save_payment_receipt(
                order_id=1, wallet_tx_id=2, telegram_id='3', file_id='photo'
            )
        with self.assertRaises(ValueError):
            db.save_payment_receipt(
                order_id=1, telegram_id='3', file_id=''
            )


if __name__ == '__main__':
    unittest.main()
