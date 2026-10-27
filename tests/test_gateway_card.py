"""درگاه ربات: فقط با کارتِ احرازشده و فقط از همان کارت؛ و احراز مشترک با سایت."""
import asyncio
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import payments
from handlers import card_verify, gateway_guard

ROOT = pathlib.Path(__file__).resolve().parents[1]
CARD = '6037990000000006'


def run(coro):
    return asyncio.run(coro)


class RequestTests(unittest.TestCase):
    def test_the_bank_is_told_which_card(self):
        captured = {}

        def fake_post(url, payload):
            captured.update(payload)
            return {'data': {'code': 100, 'authority': 'A' * 36}}

        with patch.object(payments, '_merchant', return_value='m' * 36), \
                patch.object(payments, '_sandbox', return_value=False), \
                patch.object(payments, '_post', side_effect=fake_post):
            authority, _url, err = payments.request_payment(
                100_000, 'x', 'https://bot.example/payment/callback', card_pan=CARD)
        self.assertEqual((authority, err), ('A' * 36, None))
        self.assertEqual(captured['metadata']['card_pan'], CARD)


class VerifyTests(unittest.TestCase):
    def verify(self, binding, card_pan, reverse=True):
        response = {'data': {'code': 100, 'ref_id': 'R1', 'card_pan': card_pan}}
        with patch.object(payments, '_merchant', return_value='m' * 36), \
                patch.object(payments, '_sandbox', return_value=False), \
                patch.object(payments, '_post', return_value=response), \
                patch('db.get_gateway_card_binding', return_value=binding), \
                patch('db.mark_gateway_mismatch') as mark, \
                patch.object(payments, 'reverse_payment', return_value=reverse) as rev:
            status = payments.verify_payment_detailed(100_000, 'A' * 36)
        return status, mark, rev

    def test_the_bound_card_is_accepted(self):
        status, mark, rev = self.verify(('A' * 36, '5', CARD, 'bound'), '603799******0006')
        self.assertEqual(status, ('verified', 'R1'))
        mark.assert_not_called()
        rev.assert_not_called()

    def test_another_card_is_reversed_and_counts_as_not_paid(self):
        status, mark, rev = self.verify(('A' * 36, '5', CARD, 'bound'), '502229******5995')
        self.assertEqual(status, ('not_paid', None))
        rev.assert_called_once_with('A' * 36)
        mark.assert_called_once_with('A' * 36, '502229******5995', 'R1', 100_000, True)

    def test_a_link_already_rejected_stays_rejected_without_a_second_reverse(self):
        status, _mark, rev = self.verify(('A' * 36, '5', CARD, 'mismatch'), '502229******5995')
        self.assertEqual(status, ('not_paid', None))
        rev.assert_not_called()

    def test_links_made_before_this_change_still_work(self):
        status, _mark, _rev = self.verify(None, '502229******5995')
        self.assertEqual(status, ('verified', 'R1'))


class GateWiringTests(unittest.TestCase):
    def test_both_gateway_entry_points_check_the_card_first(self):
        payment = (ROOT / 'handlers' / 'payment.py').read_text(encoding='utf-8')
        wallet = (ROOT / 'handlers' / 'wallet.py').read_text(encoding='utf-8')
        self.assertLess(payment.index("f'pay_zp_{order_id}'"), payment.index('card_pan=pay_card'))
        self.assertLess(wallet.index("f'wpay_zp_{amount}'"), wallet.index('card_pan=pay_card'))
        self.assertIn('bind_gateway_card, authority', payment)
        self.assertIn('bind_gateway_card, authority', wallet)

    def test_approval_can_resume_a_gateway_payment(self):
        self.assertTrue(card_verify._RESUME_RE.match('pay_zp_12'))
        self.assertTrue(card_verify._RESUME_RE.match('wpay_zp_50000'))
        self.assertFalse(card_verify._RESUME_RE.match('admx_anything'))


class SharedVerificationTests(unittest.TestCase):
    def _update(self):
        message = SimpleNamespace(
            contact=SimpleNamespace(user_id=42, phone_number='+989125550000'), reply_text=AsyncMock())
        return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=42))

    def test_cards_verified_on_the_site_are_accepted(self):
        update = self._update()
        ctx = SimpleNamespace(user_data={'cverify': {'resume': 'pay_zp_7'}})
        site = {'ok': True, 'cards': [{'number': CARD, 'holder': 'علی رضایی'}]}
        with patch('site_api.fetch_site_verified_cards', return_value=site) as fetch, \
                patch('db.import_site_verified_card', return_value=True) as imp:
            state = run(card_verify.cverify_phone(update, ctx))
        fetch.assert_called_once_with('09125550000', 42)
        imp.assert_called_once_with(42, CARD, 'علی رضایی', '09125550000')
        self.assertEqual(state, card_verify.ConversationHandler.END)
        markup = update.message.reply_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'pay_zp_7')

    def test_no_site_cards_continues_the_normal_steps(self):
        update = self._update()
        ctx = SimpleNamespace(user_data={'cverify': {}})
        with patch('site_api.fetch_site_verified_cards', return_value={'ok': True, 'cards': []}):
            state = run(card_verify.cverify_phone(update, ctx))
        self.assertEqual(state, card_verify.WAIT_NUMBER)

    def test_a_site_outage_does_not_block_verification(self):
        update = self._update()
        ctx = SimpleNamespace(user_data={'cverify': {}})
        with patch('site_api.fetch_site_verified_cards', side_effect=OSError('down')):
            state = run(card_verify.cverify_phone(update, ctx))
        self.assertEqual(state, card_verify.WAIT_NUMBER)


class MismatchNoticeTests(unittest.TestCase):
    def test_admins_and_buyer_are_told_once(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        row = ('A' * 36, '555', CARD, '502229******5995', 'R1', 100_000, True)
        with patch.object(gateway_guard, 'list_unnotified_gateway_mismatches', return_value=[row]), \
                patch.object(gateway_guard, 'admin_ids', return_value=[1]), \
                patch.object(gateway_guard, 'mark_gateway_mismatch_notified') as done:
            run(gateway_guard.notify_gateway_mismatches(bot))
        chats = [c.kwargs['chat_id'] for c in bot.send_message.call_args_list]
        self.assertEqual(chats, [1, 555])
        self.assertIn('برگشت داده شد', bot.send_message.call_args_list[1].kwargs['text'])
        done.assert_called_once_with('A' * 36)


if __name__ == '__main__':
    unittest.main()
