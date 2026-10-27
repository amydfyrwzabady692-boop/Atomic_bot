"""پیگیری سفارش در ربات، و پیام‌های خریدار از صف سایت."""
import asyncio
import datetime
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import order_track, site_outbox

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def _update(text='', user_id=42):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_message=message, callback_query=None,
                           effective_user=SimpleNamespace(id=user_id, first_name='a', last_name='',
                                                          username='u'))


class ParsingTests(unittest.TestCase):
    def test_site_codes_are_recognised_with_persian_digits(self):
        self.assertTrue(order_track.looks_like_tracking('as12345678'))
        self.assertTrue(order_track.looks_like_tracking('AS۱۲۳۴۵۶۷۸'))
        self.assertFalse(order_track.looks_like_tracking('12345678'))
        self.assertFalse(order_track.looks_like_tracking('سلام'))

    def test_jalali(self):
        self.assertEqual(order_track.to_jalali(2026, 10, 27), (1405, 8, 5))
        self.assertEqual(order_track.to_jalali(2025, 3, 21), (1404, 1, 1))
        aware = datetime.datetime(2026, 10, 27, 13, 26, tzinfo=datetime.timezone.utc)
        self.assertEqual(order_track.fa_datetime(aware), '۱۴۰۵/۰۸/۰۵ ساعت ۱۶:۵۶')

    def test_site_order_text_is_escaped(self):
        text = order_track.format_site_order({
            'code': 'AS1', 'status_label': 'تحویل شد', 'status_icon': '✅', 'total': 250000,
            'created_at': '2026-10-27 10:00', 'payment': 'رسید تایید شد',
            'lines': [{'title': '<b>x</b>', 'state': 'تحویل شد'}], 'url': 'https://x/track/AS1/'})
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', text)
        self.assertIn('250,000', text)


class LookupTests(unittest.TestCase):
    def setUp(self):
        order_track._hits.clear()

    def test_site_code_is_fetched_from_the_site(self):
        update = _update('AS12345678')
        ctx = SimpleNamespace(user_data={})
        data = {'ok': True, 'code': 'AS12345678', 'status_label': 'در انتظار پرداخت', 'total': 1000}
        with patch.object(order_track, 'fetch_site_order', return_value=data) as fetch:
            self.assertTrue(run(order_track.track_lookup(update, ctx, 'as12345678')))
        fetch.assert_called_once_with('AS12345678', 42)
        self.assertIn('AS12345678', update.message.reply_text.call_args.args[0])

    def test_unknown_site_code(self):
        update = _update()
        with patch.object(order_track, 'fetch_site_order', return_value={'ok': False, 'error': 'not_found'}):
            run(order_track.track_lookup(update, SimpleNamespace(user_data={}), 'AS00000000'))
        self.assertIn('پیدا نشد', update.message.reply_text.call_args.args[0])

    def test_site_down_is_said_plainly(self):
        update = _update()
        with patch.object(order_track, 'fetch_site_order', return_value={'ok': False, 'error': 'timed out'}):
            run(order_track.track_lookup(update, SimpleNamespace(user_data={}), 'AS00000000'))
        self.assertIn('دسترسی نداریم', update.message.reply_text.call_args.args[0])

    def _bot_row(self, user_id, tg):
        return (7, user_id, tg, 120000, 'delivered', 'card', datetime.datetime(2026, 10, 1, 9, 30), 0)

    def test_own_bot_order_is_shown(self):
        update = _update('#7')
        ctx = SimpleNamespace(user_data={'db_id': 3})
        with patch.object(order_track, 'get_order_for_tracking', return_value=self._bot_row(3, None)), \
                patch.object(order_track, 'get_order_items', return_value=[(1, '۱۱۰ جم', 120000, 1, 1)]), \
                patch.object(order_track, 'get_gem_infos_for_order', return_value=[]):
            run(order_track.track_lookup(update, ctx, '#7'))
        reply = update.message.reply_text.call_args.args[0]
        self.assertIn('سفارش #7', reply)
        self.assertIn('تحویل شد', reply)
        self.assertIn('۱۴۰۵/۰۷/۰۹', reply)

    def test_someone_elses_bot_order_is_not(self):
        update = _update('7')
        ctx = SimpleNamespace(user_data={'db_id': 3})
        with patch.object(order_track, 'get_order_for_tracking', return_value=self._bot_row(99, '1')):
            run(order_track.track_lookup(update, ctx, '7'))
        self.assertIn('پیدا نشد', update.message.reply_text.call_args.args[0])

    def test_lookups_are_capped(self):
        ctx = SimpleNamespace(user_data={})
        with patch.object(order_track, 'fetch_site_order', return_value={'ok': False, 'error': 'not_found'}) as fetch:
            for _ in range(order_track._LIMIT + 3):
                run(order_track.track_lookup(_update(), ctx, 'AS00000000'))
        self.assertEqual(fetch.call_count, order_track._LIMIT)


class TextRouterTests(unittest.TestCase):
    def test_waiting_for_a_code_and_sending_garbage(self):
        update = _update('سلام')
        ctx = SimpleNamespace(user_data={'await_track': True})
        self.assertTrue(run(order_track.track_text(update, ctx)))
        self.assertNotIn('await_track', ctx.user_data)
        self.assertIn('شبیه کد پیگیری نیست', update.message.reply_text.call_args.args[0])

    def test_ordinary_text_is_left_alone(self):
        update = _update('سلام')
        self.assertFalse(run(order_track.track_text(update, SimpleNamespace(user_data={}))))

    def test_menu_buttons_win_over_a_pending_tracking_prompt(self):
        src = (ROOT / 'bot.py').read_text(encoding='utf-8')
        self.assertLess(src.index("ctx.user_data.pop('await_track', None)"),
                        src.index('elif await track_text(update, ctx):'))
        self.assertIn("CommandHandler('track', track_command)", src)
        self.assertIn("pattern='^track_order$'", src)


class BuyerNoticeTests(unittest.TestCase):
    def test_buyer_notice_goes_only_to_the_buyer(self):
        self.assertEqual(site_outbox._recipients({'audience': 'user', 'chat_id': 555}), [555])
        with patch.object(site_outbox, 'admin_ids', return_value=[1, 2]):
            self.assertEqual(site_outbox._recipients({'audience': 'user', 'chat_id': None}), [])

    def test_a_buyer_who_blocked_the_bot_is_not_retried_forever(self):
        class Forbidden(Exception):
            pass

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=Forbidden('bot was blocked by the user')))
        item = {'id': 4, 'kind': 'user', 'audience': 'user', 'chat_id': 555, 'text': 'سلام',
                'reply_markup': None, 'photo_url': '', 'ref': ''}
        with patch.object(site_outbox, 'fetch_site_outbox', return_value={'ok': True, 'items': [item]}), \
                patch.object(site_outbox, 'ack_site_outbox', return_value={'ok': True}) as ack:
            delivered = run(site_outbox.deliver_site_outbox(bot))
        self.assertEqual(delivered, 0)
        ack.assert_called_once_with(4, [{'chat_id': 555, 'message_id': 0}])
        self.assertEqual(bot.send_message.call_count, 1)  # بدون تلاش دوباره با متن ساده

    def test_a_temporary_failure_is_retried_later(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError('timed out')))
        item = {'id': 5, 'kind': 'user', 'audience': 'user', 'chat_id': 555, 'text': 'x',
                'reply_markup': None, 'photo_url': '', 'ref': ''}
        with patch.object(site_outbox, 'fetch_site_outbox', return_value={'ok': True, 'items': [item]}), \
                patch.object(site_outbox, 'ack_site_outbox', return_value={'ok': True}) as ack:
            run(site_outbox.deliver_site_outbox(bot))
        ack.assert_called_once_with(5, [])


if __name__ == '__main__':
    unittest.main()
