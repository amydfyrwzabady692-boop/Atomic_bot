"""بالا بردنِ موجودیِ کیف پول فقط کارِ مالک است.

شارژ یا «تنظیم دقیق» یعنی ساختنِ پول: با آن موجودی جم خودکار از حسابِ G2Bulk
خریده می‌شود. ادمینِ کمکی (جدول BotAdmins) فقط کسر و صفر می‌کند — مثل سایت که در
پنل فقط کم/صفر کردن دارد.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

from handlers import admin

OWNER, HELPER, BUYER = 1, 2, 555
PROFILE = (10, None, None, None, None, False, None, 5000)


def callback(data, user_id):
    query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(callback_query=query, message=None,
                           effective_user=SimpleNamespace(id=user_id)), query


def text(value, user_id):
    message = SimpleNamespace(text=value, reply_text=AsyncMock())
    return SimpleNamespace(callback_query=None, message=message,
                           effective_user=SimpleNamespace(id=user_id)), message


def ctx(**user_data):
    return SimpleNamespace(user_data=dict(user_data), bot=SimpleNamespace(send_message=AsyncMock()))


class WalletRaiseIsOwnerOnlyTests(unittest.TestCase):
    def setUp(self):
        for name, value in (('is_admin', MagicMock(return_value=True)),
                            ('admin_id', MagicMock(return_value=OWNER)),
                            ('get_user_profile', MagicMock(return_value=PROFILE)),
                            ('admin_user_keyboard', MagicMock(return_value=None))):
            patcher = patch.object(admin, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.adjust = MagicMock(return_value=(True, 9000, None))
        self.set_balance = MagicMock(return_value=(True, 5000, 9000, None))
        for name, value in (('admin_adjust_wallet', self.adjust),
                            ('admin_set_wallet_balance', self.set_balance)):
            patcher = patch.object(admin, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_helper_admin_cannot_charge_or_set(self):
        for handler, data in ((admin.admin_wallet_start, f'adm_wal_{BUYER}'),
                              (admin.admin_wallet_set_start, f'adm_wset_{BUYER}')):
            update, query = callback(data, HELPER)
            self.assertEqual(asyncio.run(handler(update, ctx())), ConversationHandler.END)
            self.assertTrue(query.answer.call_args.kwargs.get('show_alert'))
            query.edit_message_text.assert_not_called()

    def test_a_charge_typed_by_a_helper_is_refused_even_if_the_state_says_charge(self):
        update, message = text('50000', HELPER)
        state = ctx(adm_wal_tg=str(BUYER), adm_wal_mode='charge')
        self.assertEqual(asyncio.run(admin.admin_wallet_apply(update, state)),
                         ConversationHandler.END)
        self.adjust.assert_not_called()
        self.assertIn('فقط برای مالک', message.reply_text.call_args.args[0])

        update, _message = text('50000', HELPER)
        asyncio.run(admin.admin_wallet_set_apply(update, ctx(adm_wset_tg=str(BUYER))))
        self.set_balance.assert_not_called()

    def test_a_helper_admin_can_still_deduct(self):
        update, _message = text('2000', HELPER)
        asyncio.run(admin.admin_wallet_apply(update, ctx(adm_wal_tg=str(BUYER),
                                                         adm_wal_mode='deduct')))
        self.assertEqual(self.adjust.call_args.args[:2], (PROFILE[0], -2000))

    def test_the_owner_can_charge_and_set(self):
        update, query = callback(f'adm_wal_{BUYER}', OWNER)
        self.assertEqual(asyncio.run(admin.admin_wallet_start(update, ctx())), admin.WAIT_WALLET)
        update, _message = text('50000', OWNER)
        asyncio.run(admin.admin_wallet_apply(update, ctx(adm_wal_tg=str(BUYER),
                                                         adm_wal_mode='charge')))
        self.assertEqual(self.adjust.call_args.args[:2], (PROFILE[0], 50000))
        update, _message = text('9000', OWNER)
        asyncio.run(admin.admin_wallet_set_apply(update, ctx(adm_wset_tg=str(BUYER))))
        self.assertEqual(self.set_balance.call_args.args[:2], (PROFILE[0], 9000))


if __name__ == '__main__':
    unittest.main()
