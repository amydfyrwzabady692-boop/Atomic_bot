"""درگاه ربات: برای همه باز؛ صاحبِ کارتِ احرازشده فقط از همان کارت.

احراز هویتِ بعد از پرداخت و احراز مشترک با سایت در tests/test_identity.py است.
"""
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
    def test_only_verified_cards_are_pinned(self):
        payment = (ROOT / 'handlers' / 'payment.py').read_text(encoding='utf-8')
        wallet = (ROOT / 'handlers' / 'wallet.py').read_text(encoding='utf-8')
        for source in (payment, wallet):
            self.assertIn("pay_card = cards[0][1] if cards else ''", source)
            self.assertIn('card_pan=pay_card', source)
            self.assertIn('if authority and pay_card:', source)

    def test_approval_can_resume_a_gateway_payment(self):
        self.assertTrue(card_verify._RESUME_RE.match('pay_zp_12'))
        self.assertTrue(card_verify._RESUME_RE.match('wpay_zp_50000'))
        self.assertFalse(card_verify._RESUME_RE.match('admx_anything'))


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
