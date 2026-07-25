import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import admin_notify
from handlers import sensitivity
from text_safety import markdown_safe


class TextSafetyTests(unittest.TestCase):
    def test_dynamic_markdown_is_escaped(self):
        self.assertEqual(
            markdown_safe(r'ali_[vip]*`x`'),
            r'ali\_\[vip]\*\`x\`',
        )

    def test_dynamic_markdown_can_be_limited(self):
        self.assertEqual(markdown_safe('abcdef', 3), 'abc')


class AdminNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_markdown_failure_retries_as_plain_text(self):
        bot = SimpleNamespace(send_message=AsyncMock(
            side_effect=[RuntimeError('bad markdown'), None]
        ))
        with patch.object(admin_notify, 'admin_ids', return_value=[123]):
            sent = await admin_notify.notify_admin(bot, 'bad_[text')

        self.assertTrue(sent)
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(
            bot.send_message.await_args_list[1].kwargs.get('parse_mode'),
            None,
        )


class SensitivityPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_static_pack_cannot_create_order(self):
        query = SimpleNamespace(
            data='sens_buy_basic',
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        ctx = SimpleNamespace(user_data={})

        with (
            patch.object(sensitivity, 'get_bool_setting', return_value=True),
            patch.object(sensitivity, 'get_sense_package', return_value=None),
            patch.object(sensitivity, 'create_order') as create_order,
        ):
            await sensitivity.sens_buy(update, ctx)

        create_order.assert_not_called()
        query.edit_message_text.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
