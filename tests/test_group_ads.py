"""گروه‌هایی که ربات عضوشان است، و تبلیغ ۱۲ ساعته با پاک‌کردن پیام قبلی."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden

import db
import group_ads
from handlers import group_ads as group_handlers


def run(coro):
    return asyncio.run(coro)


def _db_available():
    try:
        db.open_db_pool()
        db.ensure_admin_schema()
        return True
    except Exception:  # noqa: BLE001
        return False


DB = _db_available()


def fake_bot(message_id=500):
    return SimpleNamespace(
        username='AtomicShopBot',
        delete_message=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=message_id)),
    )


class AdTests(unittest.TestCase):
    def test_the_ad_sells_the_shop_and_has_bot_and_site_buttons(self):
        urls = [b.url for row in group_ads.ad_keyboard('AtomicShopBot').inline_keyboard for b in row]
        self.assertIn('https://t.me/AtomicShopBot?start=grp', urls)
        self.assertTrue(any(u.startswith('https://atomicshop.ir/?utm_source=telegram') for u in urls))
        self.assertTrue(any('/store/gems/' in u for u in urls))
        for word in ('AtomicShop.ir', 'جم فری‌فایر', 'یوسی پابجی', 'استارز', 'گیفت کارت'):
            self.assertIn(word, group_ads.DEFAULT_AD)


@unittest.skipUnless(DB, 'Postgres is not available')
class LoopTests(unittest.TestCase):
    def setUp(self):
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM "BotGroups"')
            cur.execute('DELETE FROM "BotSettings" WHERE "Key" IN (%s,%s)',
                        (group_ads.AD_TEXT_KEY, group_ads.AD_HOURS_KEY))
            conn.commit()
        group_ads.remember_group(-1001, 'گروه فری‌فایر', '', 'supergroup', True)

    def test_a_new_group_gets_the_ad_and_the_old_ad_is_deleted_next_time(self):
        bot = fake_bot(500)
        self.assertEqual(run(group_ads.run_due(bot)), 1)
        bot.delete_message.assert_not_called()
        kwargs = bot.send_message.call_args.kwargs
        self.assertEqual((kwargs['chat_id'], kwargs['parse_mode']), (-1001, 'HTML'))
        self.assertEqual(group_ads.get_group(-1001)['last_message_id'], 500)
        # تا ۱۲ ساعت بعد نوبتش نیست
        self.assertEqual(run(group_ads.run_due(fake_bot())), 0)
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('UPDATE "BotGroups" SET "LastAdAt"=now()-interval \'13 hours\'')
            conn.commit()
        bot2 = fake_bot(600)
        self.assertEqual(run(group_ads.run_due(bot2)), 1)
        bot2.delete_message.assert_awaited_once_with(chat_id=-1001, message_id=500)
        self.assertEqual(group_ads.get_group(-1001)['last_message_id'], 600)

    def test_a_switched_off_group_gets_nothing(self):
        group_ads.toggle_ads(-1001)
        bot = fake_bot()
        self.assertEqual(run(group_ads.run_due(bot)), 0)
        bot.send_message.assert_not_called()

    def test_a_group_that_removed_the_bot_is_marked_and_skipped(self):
        bot = fake_bot()
        bot.send_message = AsyncMock(side_effect=Forbidden('bot was kicked'))
        self.assertEqual(run(group_ads.run_due(bot)), 0)
        self.assertFalse(group_ads.get_group(-1001)['is_member'])

    def test_the_admin_text_replaces_the_default(self):
        group_ads.set_ad_text('<b>تخفیف ویژه</b>')
        bot = fake_bot()
        run(group_ads.run_due(bot))
        self.assertEqual(bot.send_message.call_args.kwargs['text'], '<b>تخفیف ویژه</b>')

    def test_joining_and_leaving_is_recorded(self):
        chat = SimpleNamespace(id=-1002, type='supergroup', title='گروه پابجی', username='')
        def update(status):
            return SimpleNamespace(my_chat_member=SimpleNamespace(
                chat=chat, new_chat_member=SimpleNamespace(status=status)))
        ctx = SimpleNamespace(bot=SimpleNamespace())
        with patch.object(group_handlers, 'notify_admin', AsyncMock()) as notify:
            run(group_handlers.on_group_membership(update('member'), ctx))
            self.assertTrue(group_ads.get_group(-1002)['is_member'])
            run(group_handlers.on_group_membership(update('left'), ctx))
            self.assertFalse(group_ads.get_group(-1002)['is_member'])
        self.assertEqual(notify.await_count, 2)


if __name__ == '__main__':
    unittest.main()
