import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import admin_notify
import bot
import db
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
            patch.object(
                sensitivity, 'create_sense_order_atomic'
            ) as create_order,
        ):
            await sensitivity.sens_buy(update, ctx)

        create_order.assert_not_called()
        query.edit_message_text.assert_awaited_once()


class UpdateRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_router_ignores_channel_posts(self):
        channel_post = SimpleNamespace(
            text='channel post', reply_text=AsyncMock()
        )
        update = SimpleNamespace(
            message=None,
            effective_message=channel_post,
            effective_user=None,
            effective_chat=SimpleNamespace(type='channel'),
        )

        await bot.text_router(update, SimpleNamespace())

        channel_post.reply_text.assert_not_awaited()


    async def test_error_handler_does_not_reply_to_channel(self):
        channel_post = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=channel_post,
            effective_chat=SimpleNamespace(type='channel'),
        )
        error = RuntimeError('test error')
        ctx = SimpleNamespace(error=error, bot=object())

        with patch.object(bot, 'notify_admin', new=AsyncMock()):
            await bot.error_handler(update, ctx)

        channel_post.reply_text.assert_not_awaited()

    async def test_error_handler_ignores_stale_callback_query(self):
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_message=SimpleNamespace(reply_text=reply),
            effective_chat=SimpleNamespace(type='private'),
        )
        error = SimpleNamespace(
            message=(
                'Query is too old and response timeout expired or query id is invalid'
            )
        )
        ctx = SimpleNamespace(error=error, bot=object())

        with patch.object(bot, 'notify_admin', new=AsyncMock()) as notify:
            await bot.error_handler(update, ctx)

        notify.assert_not_awaited()
        reply.assert_not_awaited()


class AccessFailureModeTests(unittest.TestCase):
    def test_block_lookup_fails_closed(self):
        with patch.object(db, 'get_conn', side_effect=RuntimeError('db unavailable')):
            self.assertTrue(db.is_user_blocked('123456'))


if __name__ == '__main__':
    unittest.main()
