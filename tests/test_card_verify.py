"""ابزارهای کارت بانکی، دکمه‌های قدیمیِ «احراز کارت»، و تحویل صف اعلان‌های سایت.

احراز کارتِ پیش از پرداخت برداشته شد؛ احراز هویتِ بعد از پرداخت در
tests/test_identity.py است.
"""
import asyncio
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from handlers import card_verify, site_outbox

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


class CardNumberTests(unittest.TestCase):
    def test_luhn(self):
        self.assertTrue(card_verify.luhn_ok('6037990000000006'))
        self.assertFalse(card_verify.luhn_ok('6037990000000007'))
        self.assertFalse(card_verify.luhn_ok('60379900'))

    def test_persian_digits_and_separators(self):
        self.assertEqual(card_verify.normalize_card('۶۰۳۷-۹۹۰۰ ۰۰۰۰ ۰۰۰۶'), '6037990000000006')

    def test_phone_from_telegram_contact(self):
        self.assertEqual(card_verify.normalize_phone('+989121234567'), '09121234567')
        self.assertEqual(card_verify.normalize_phone('989121234567'), '09121234567')

    def test_mask(self):
        self.assertEqual(card_verify.masked_card('6037990000000006'), '6037-99**-****-0006')


class LegacyButtonTests(unittest.TestCase):
    def test_old_verify_buttons_say_it_is_not_needed(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        run(card_verify.cverify_start(update, SimpleNamespace(user_data={})))
        text = query.edit_message_text.call_args.args[0]
        self.assertIn('لازم نیست', text)
        # قاعدهٔ احراز هویت به خریدار گفته نمی‌شود و دکمه‌اش هم نیست.
        self.assertNotIn('احراز هویت', text)
        self.assertNotIn('تومان', text)
        markup = query.edit_message_text.call_args.kwargs['reply_markup']
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row], ['home'])

    def test_receipt_caption_without_a_verified_card_is_informational(self):
        with patch.object(card_verify, 'approved_cards', return_value=[]):
            line = run(card_verify.payer_cards_line(5))
        self.assertIn('احراز هویت', line)
        self.assertNotIn('تایید نکن', line)


class OutboxDeliveryTests(unittest.TestCase):
    def test_photo_is_uploaded_once_then_reused_and_acked(self):
        sent = []

        async def send_photo(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(message_id=len(sent) + 100,
                                   photo=[SimpleNamespace(file_id='FILE-1')])

        bot = SimpleNamespace(send_photo=send_photo, send_message=AsyncMock())
        item = {'id': 7, 'kind': 'receipt', 'audience': 'admins', 'text': '<b>رسید</b>',
                'reply_markup': {'inline_keyboard': [[{'text': 'ok', 'callback_data': 'site_review_ok_3'}]]},
                'photo_url': 'https://atomicshop.ir/x', 'ref': 'receipt:3'}
        acks = []
        with patch.object(site_outbox, 'fetch_site_outbox', return_value={'ok': True, 'items': [item]}), \
                patch.object(site_outbox, 'fetch_site_photo', return_value=b'png-bytes'), \
                patch.object(site_outbox, 'admin_ids', return_value=[10, 20]), \
                patch.object(site_outbox, 'credential_admin_ids', return_value=[]), \
                patch.object(site_outbox, 'ack_site_outbox',
                             side_effect=lambda i, n: acks.append((i, n)) or {'ok': True}), \
                patch.object(site_outbox, 'remember_notices') as remember:
            delivered = run(site_outbox.deliver_site_outbox(bot))
        self.assertEqual(delivered, 1)
        self.assertEqual(len(sent), 2)
        self.assertNotIsInstance(sent[0]['photo'], str)   # اولی: آپلود بایت‌ها
        self.assertEqual(sent[1]['photo'], 'FILE-1')      # دومی: همان file_id
        self.assertEqual(acks, [(7, [{'chat_id': 10, 'message_id': 101},
                                     {'chat_id': 20, 'message_id': 102}])])
        remember.assert_called_once()
        self.assertEqual(remember.call_args.args[:2], ('site', 3))

    def test_credential_orders_go_to_credential_admins(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=5)))
        item = {'id': 8, 'kind': 'credential', 'audience': 'credential', 'text': 'سفارش',
                'reply_markup': None, 'photo_url': '', 'ref': ''}
        with patch.object(site_outbox, 'fetch_site_outbox', return_value={'ok': True, 'items': [item]}), \
                patch.object(site_outbox, 'admin_ids', return_value=[10]), \
                patch.object(site_outbox, 'credential_admin_ids', return_value=[30]), \
                patch.object(site_outbox, 'ack_site_outbox', return_value={'ok': True}) as ack:
            run(site_outbox.deliver_site_outbox(bot))
        self.assertEqual(bot.send_message.call_args.kwargs['chat_id'], 30)
        ack.assert_called_once_with(8, [{'chat_id': 30, 'message_id': 5}])

    def test_nothing_delivered_still_acks_so_the_site_releases_it(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('blocked')))
        item = {'id': 9, 'kind': 'credential', 'audience': 'admins', 'text': 'x',
                'reply_markup': None, 'photo_url': '', 'ref': ''}
        with patch.object(site_outbox, 'fetch_site_outbox', return_value={'ok': True, 'items': [item]}), \
                patch.object(site_outbox, 'admin_ids', return_value=[10]), \
                patch.object(site_outbox, 'credential_admin_ids', return_value=[]), \
                patch.object(site_outbox, 'ack_site_outbox', return_value={'ok': True}) as ack:
            self.assertEqual(run(site_outbox.deliver_site_outbox(bot)), 0)
        ack.assert_called_once_with(9, [])


class WiringTests(unittest.TestCase):
    def test_bot_registers_handlers_and_loop(self):
        src = (ROOT / 'bot.py').read_text(encoding='utf-8')
        self.assertIn('card_verify_conversation_handler()', src)
        self.assertIn(r'^site_card_(?:ok|no)_\d+$', src)
        self.assertIn(r'^cverify_(?:ok|no)_\d+$', src)
        self.assertIn('site_outbox_loop(app)', src)
        self.assertIn("'_site_outbox_task'", src)

    def test_card_to_card_needs_no_card_verification_first(self):
        payment = (ROOT / 'handlers' / 'payment.py').read_text(encoding='utf-8')
        wallet = (ROOT / 'handlers' / 'wallet.py').read_text(encoding='utf-8')
        self.assertNotIn('card_gate(', payment + wallet)
        # پیش از نشان‌دادن شمارهٔ کارتِ فروشگاه، فقط قوانین (یک‌بار).
        self.assertLess(payment.index("terms_gate(query, update.effective_user.id, order_id)"),
                        payment.index("get_setting('card_number'"))


if __name__ == '__main__':
    unittest.main()
