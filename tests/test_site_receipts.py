import asyncio
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from review_broadcast import (
    admin_display_name,
    broadcast_reviewed,
    get_notices,
    remember_notices,
    review_footer,
)
import site_api


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SiteKeyboardSourceTests(unittest.TestCase):
    def test_keyboards_and_bot_register_site_callbacks(self):
        keyboards = (ROOT / 'keyboards.py').read_text(encoding='utf-8')
        bot_src = (ROOT / 'bot.py').read_text(encoding='utf-8')
        self.assertIn("f'site_review_ok_{payment_id}'", keyboards)
        self.assertIn("f'site_review_no_{payment_id}'", keyboards)
        self.assertIn("f'site_ok_{payment_id}'", keyboards)
        self.assertIn("f'site_no_{payment_id}'", keyboards)
        self.assertIn("f'site_review_back_{payment_id}'", keyboards)
        self.assertIn(r'site_review_(?:ok|no)_\d+', bot_src)
        self.assertIn('site_ok_', bot_src)
        self.assertIn('site_no_', bot_src)


class ReviewBroadcastTests(unittest.TestCase):
    def test_footer_names_other_admin(self):
        text = review_footer(approved=True, reviewer_name='امید (@omid)')
        self.assertIn('امید', text)
        self.assertIn('ادمین دیگر نیاز به بررسی ندارد', text)

    def test_admin_display_name(self):
        user = SimpleNamespace(full_name='امید', first_name='O', username='omid', id=1)
        self.assertEqual(admin_display_name(user), 'امید (@omid)')

    def test_broadcast_edits_all_stored_messages(self):
        remember_notices('order', 77, [
            {'chat_id': 10, 'message_id': 1},
            {'chat_id': 20, 'message_id': 2},
        ])
        self.assertEqual(len(get_notices('order', 77)), 2)
        bot = SimpleNamespace(
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
            send_message=AsyncMock(),
        )

        async def run():
            await broadcast_reviewed(
                bot, 'order', 77,
                approved=True,
                reviewer_name='علی',
                result_text='✅ سفارش #77 تایید شد.',
                extra_admins=[10, 20, 30],
            )

        asyncio.run(run())
        self.assertEqual(bot.edit_message_caption.call_count, 2)
        ping_chats = [c.kwargs['chat_id'] for c in bot.send_message.await_args_list]
        self.assertEqual(ping_chats, [30])


class SiteApiTests(unittest.TestCase):
    def test_missing_secret(self):
        with patch.dict('os.environ', {'BOT_INTERNAL_SECRET': '', 'SITE_API_URL': 'https://atomicshop.ir'}):
            site_api.BOT_INTERNAL_SECRET = ''
            result = site_api.call_site_review(1, 'approve', 2, 'A')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'missing_secret')

    def test_posts_secret_and_path(self):
        class _Resp:
            def read(self):
                return b'{"ok": true, "reviewed_by": "Omid", "status": "approved"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.dict('os.environ', {
            'BOT_INTERNAL_SECRET': 's3cret',
            'SITE_API_URL': 'https://atomicshop.ir',
        }):
            site_api.BOT_INTERNAL_SECRET = 's3cret'
            site_api.SITE_API_URL = 'https://atomicshop.ir'
            with patch('site_api.urllib.request.urlopen', return_value=_Resp()):
                with patch('site_api.urllib.request.Request', return_value=MagicMock()) as req:
                    result = site_api.call_site_review(12, 'approve', 1, 'Omid')
        self.assertTrue(result['ok'])
        self.assertEqual(req.call_args.kwargs['headers']['X-Bot-Secret'], 's3cret')
        self.assertIn('/internal/bot/card-transfer/12/review/', req.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
