import asyncio
import inspect
import re
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

import appearance
import db
import referral_db as rdb
from handlers import referral
from keyboards import admin_home_keyboard, admin_hub_users_keyboard, main_menu

UTC = timezone.utc
FRESH = (True, False, False, False, False)
SAMPLE_GEMS = [(7, '110 جم', 110, 0, 194000, None, 'once', 'by_id', True, '110', 9999, True)]


class _Cursor:
    def __init__(self, ones=None, many=None):
        self.ones = list(ones or [])
        self.many = list(many or [])
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.ones.pop(0) if self.ones else None

    def fetchall(self):
        return self.many.pop(0) if self.many else []


class _Connection:
    def __init__(self, ones=None, many=None):
        self.cur = _Cursor(ones=ones, many=many)
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Bot:
    username = 'AtomicBot'

    def __init__(self, inline=False):
        self.sent = []
        self.inline = inline
        self.get_me_calls = 0

    async def get_me(self):
        self.get_me_calls += 1
        return SimpleNamespace(supports_inline_queries=self.inline)

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=len(self.sent))

    async def send_photo(self, chat_id=None, photo=None, caption=None, **kwargs):
        self.sent.append((chat_id, caption, dict(kwargs, photo=photo)))
        return SimpleNamespace(message_id=len(self.sent))


def _enabled_settings(**overrides):
    values = dict(rdb.DEFAULT_SETTINGS, referral_enabled='1')
    values.update(overrides)
    return values


class PayloadAndFormattingTests(unittest.TestCase):
    def test_build_and_parse_payload_kinds(self):
        self.assertEqual(rdb.build_payload(123456789), 'ref_123456789')
        self.assertEqual(rdb.build_payload(123456789, 'gem'), 'refgem_123456789')
        self.assertEqual(rdb.parse_payload('refgift_123456789'), (123456789, 'gift'))
        self.assertEqual(rdb.parse_payload('ref_123456789'), (123456789, ''))
        self.assertEqual(rdb.parse_payload('ref_abc'), (None, ''))
        self.assertEqual(rdb.parse_payload('promo_123456'), (None, ''))
        self.assertEqual(
            rdb.referral_link('@AtomicBot', 555666, 'gem'),
            'https://t.me/AtomicBot?start=refgem_555666',
        )

    def test_payload_fits_telegram_start_limit(self):
        payload = rdb.build_payload(9_999_999_999_999, 'gift')
        self.assertLessEqual(len(payload), 64)
        self.assertRegex(payload, r'^[A-Za-z0-9_-]+$')

    def test_jalali_dates(self):
        self.assertEqual(rdb.to_jalali(2024, 3, 20), (1403, 1, 1))
        self.assertEqual(rdb.to_jalali(2023, 3, 21), (1402, 1, 1))
        self.assertEqual(
            rdb.format_date(datetime(2024, 3, 19, 21, 0, tzinfo=UTC)), '1403/01/01',
        )

    def test_remaining_time(self):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        self.assertEqual(rdb.format_remaining(now + timedelta(days=3, hours=4), now), '3 روز و 4 ساعت')
        self.assertEqual(rdb.format_remaining(now + timedelta(minutes=30), now), '30 دقیقه')
        self.assertEqual(rdb.format_remaining(now - timedelta(minutes=1), now), '')

    def test_duration_parsing(self):
        self.assertEqual(referral.parse_duration_hours('7'), 168)
        self.assertEqual(referral.parse_duration_hours('۴۸h'), 48)
        self.assertEqual(referral.parse_duration_hours('12 ساعت'), 12)
        for bad in ('', 'abc', '0', '400'):
            with self.assertRaises(ValueError):
                referral.parse_duration_hours(bad)

    def test_render_escapes_names_and_survives_bad_templates(self):
        text = referral.render('*{name}* {prize} {unknown}', {'name': 'a_b*', 'prize': '*1000 جم*'})
        self.assertEqual(text, '*a\\_b\\** *1000 جم* {unknown}')
        plain = referral.render('{name}', {'name': 'a_b'}, markdown=False)
        self.assertEqual(plain, 'a_b')
        self.assertEqual(referral.render('{0} {name} {', {'name': 'x'}), '{0} x {')

    def test_mask_name_hides_identity(self):
        self.assertEqual(rdb.mask_name('Mohammad', 'moh'), 'Mo•••')
        self.assertEqual(rdb.mask_name('', 'ab'), 'a•••')
        masked = rdb.mask_name('*star*')
        self.assertNotIn('star', masked)
        # نام ماسک‌شده هنگام رندر Markdown امن می‌شود.
        self.assertEqual(referral.render('{friend}', {'friend': masked}), '\\*s•••')


class RecordReferralTests(unittest.TestCase):
    def test_self_referral_never_touches_db(self):
        with patch.object(db, 'get_conn', side_effect=AssertionError('db used')), \
                patch.object(rdb, 'ensure_referral_schema'):
            self.assertIsNone(rdb.record_referral(111222, 5, 111222))

    def test_new_referral_is_inserted_once_and_syncs_referred_by(self):
        conn = _Connection(ones=[FRESH, (7, False, 'Ali', 'ali'), (datetime.now(UTC),)])
        with patch.object(db, 'get_conn', return_value=conn), \
                patch.object(rdb, 'ensure_referral_schema'):
            result = rdb.record_referral(222333, 9, 111222)
        self.assertEqual(result['referrer_user_id'], 7)
        self.assertEqual(result['referrer_first_name'], 'Ali')
        sqls = [sql for sql, _params in conn.cur.executed]
        guard_sql, guard_params = conn.cur.executed[0]
        self.assertIn('"DateJoined"', guard_sql)
        self.assertIn('"Orders"', guard_sql)
        self.assertIn('"WalletTransactions"', guard_sql)
        self.assertIn('"BotReferrals"', guard_sql)
        self.assertEqual(guard_params, (9, '222333'))
        self.assertTrue(any('INSERT INTO "BotReferrals"' in s and 'ON CONFLICT' in s for s in sqls))
        self.assertTrue(any('"ReferredById" IS NULL' in s for s in sqls))
        self.assertEqual(conn.commits, 1)

    def test_existing_bot_users_are_never_counted(self):
        for existing in (
            None,                                  # user row / telegram id mismatch
            (False, False, False, False, False),   # joined more than an hour ago
            (True, True, False, False, False),     # already referred before
            (True, False, True, False, False),     # has orders
            (True, False, False, True, False),     # has wallet transactions
            (True, False, False, False, True),     # already invited others
        ):
            conn = _Connection(ones=[existing])
            with patch.object(db, 'get_conn', return_value=conn), \
                    patch.object(rdb, 'ensure_referral_schema'):
                self.assertIsNone(rdb.record_referral(222333, 9, 111222), existing)
            self.assertEqual(len(conn.cur.executed), 1, existing)
            self.assertEqual(conn.commits, 0)

    def test_duplicate_invitee_is_ignored(self):
        conn = _Connection(ones=[FRESH, (7, False, 'Ali', ''), None])
        with patch.object(db, 'get_conn', return_value=conn), \
                patch.object(rdb, 'ensure_referral_schema'):
            self.assertIsNone(rdb.record_referral(222333, 9, 111222))
        self.assertEqual(conn.rollbacks, 1)
        self.assertFalse(any('UPDATE "Users"' in sql for sql, _ in conn.cur.executed))

    def test_blocked_or_same_account_referrer_is_ignored(self):
        for row in ((7, True, 'Ali', ''), (9, False, 'Me', '')):
            conn = _Connection(ones=[FRESH, row])
            with patch.object(db, 'get_conn', return_value=conn), \
                    patch.object(rdb, 'ensure_referral_schema'):
                self.assertIsNone(rdb.record_referral(222333, 9, 111222))
            self.assertEqual(len(conn.cur.executed), 2)


class LeaderboardQueryTests(unittest.TestCase):
    campaign = {
        'starts_at': datetime(2026, 9, 1, tzinfo=UTC),
        'ends_at': datetime(2026, 9, 8, tzinfo=UTC),
    }

    def test_purchase_mode_counts_only_buyers_inside_window(self):
        conn = _Connection(many=[[(7, '111', 'Ali', '', 'ali', 3, datetime(2026, 9, 2, tzinfo=UTC))]])
        with patch.object(db, 'get_conn', return_value=conn), \
                patch.object(rdb, 'ensure_referral_schema'):
            rows = rdb.leaderboard(self.campaign, 'purchase', 10)
        sql, params = conn.cur.executed[0]
        self.assertIn('"Orders"', sql)
        self.assertIn('IsBlocked', sql)
        self.assertEqual(params[0], self.campaign['starts_at'])
        self.assertEqual(params[1], self.campaign['ends_at'])
        self.assertEqual(tuple(params[2:6]), rdb.QUALIFIED_STATUSES)
        self.assertEqual(tuple(params[-2:]), (10, 0))
        self.assertEqual(rows[0]['rank'], 1)
        self.assertEqual(rows[0]['count'], 3)

    def test_join_mode_without_campaign_has_no_order_or_window_filter(self):
        conn = _Connection(many=[[]])
        with patch.object(db, 'get_conn', return_value=conn), \
                patch.object(rdb, 'ensure_referral_schema'):
            rdb.leaderboard(None, 'join', 5, 10)
        sql, params = conn.cur.executed[0]
        self.assertNotIn('"Orders"', sql)
        self.assertEqual(tuple(params), (5, 10))

    def test_user_rank_uses_same_tie_break_as_leaderboard(self):
        source = inspect.getsource(rdb.user_stats)
        self.assertIn('c.last_at<me.last_at', source)
        self.assertIn('ORDER BY cnt DESC, last_at ASC', inspect.getsource(rdb._leaderboard_cur))

    def test_schema_is_additive_only(self):
        joined = '\n'.join(rdb._SCHEMA)
        self.assertNotIn('DROP', joined.upper())
        self.assertNotIn('ALTER TABLE "Orders"', joined)
        self.assertTrue(all('IF NOT EXISTS' in stmt for stmt in rdb._SCHEMA))


class SettingsAndMenuTests(unittest.TestCase):
    def setUp(self):
        rdb.invalidate_settings()

    def tearDown(self):
        rdb.invalidate_settings()
        appearance.invalidate_cache()

    def test_blank_values_fall_back_to_defaults(self):
        conn = _Connection(many=[[
            ('referral_enabled', '1'), ('referral_prize_text', '   '),
            ('referral_top_n', '5'), ('referral_count_mode', 'bogus'),
        ]])
        with patch.object(db, 'get_conn', return_value=conn):
            values = rdb.settings(force=True)
        self.assertTrue(rdb.is_on(values, 'referral_enabled'))
        self.assertEqual(values['referral_prize_text'], rdb.DEFAULT_SETTINGS['referral_prize_text'])
        self.assertEqual(rdb.top_n(values), 5)
        self.assertEqual(values['referral_count_mode'], 'join')

    def test_public_leaderboard_settings_are_gone(self):
        self.assertNotIn('referral_public_top', rdb.DEFAULT_SETTINGS)
        self.assertNotIn('referral_inline_share', rdb.DEFAULT_SETTINGS)
        self.assertFalse(hasattr(referral, '_public_top_text'))

    def test_menu_hidden_without_database_or_when_disabled(self):
        with patch.object(appearance, '_CACHE', {}):
            rows = main_menu().keyboard
        labels = [btn.text for row in rows for btn in row]
        self.assertNotIn(appearance.DEFAULTS['b.menu.ref'], labels)
        self.assertEqual(rows[4][0].text, '🎧 پشتیبانی')

    def test_menu_shows_green_referral_row_when_enabled(self):
        cache = {'at': time.monotonic(), 'values': _enabled_settings()}
        with patch.object(appearance, '_CACHE', {}), \
                patch.object(db, '_POOL', object()), \
                patch.dict(rdb._settings_cache, cache):
            rows = main_menu().keyboard
        self.assertEqual(rows[4][0].text, appearance.DEFAULTS['b.menu.ref'])
        self.assertEqual(rows[4][0].to_dict().get('style'), 'success')
        self.assertEqual(rows[5][0].text, '🎧 پشتیبانی')
        with patch.object(appearance, '_CACHE', {}):
            self.assertEqual(appearance.menu_action(appearance.DEFAULTS['b.menu.ref']), 'referral')

    def test_admin_entry_buttons_do_not_move_existing_rows(self):
        home = admin_home_keyboard().inline_keyboard
        self.assertEqual(home[0][0].callback_data, 'admx_ops')
        self.assertEqual(home[2][0].callback_data, 'ap_home')
        self.assertEqual(home[3][0].callback_data, 'admx_hub_orders')
        callbacks = [btn.callback_data for row in home for btn in row]
        self.assertIn('radm_home', callbacks)
        users = [btn.callback_data for row in admin_hub_users_keyboard().inline_keyboard for btn in row]
        self.assertIn('radm_home', users)


class KeyboardTests(unittest.TestCase):
    def test_banner_has_three_colored_deeplink_buttons(self):
        rows = referral.banner_keyboard('AtomicBot', 123456, rdb.DEFAULT_SETTINGS).inline_keyboard
        self.assertEqual([row[0].url for row in rows], [
            'https://t.me/AtomicBot?start=ref_123456',
            'https://t.me/AtomicBot?start=refgem_123456',
            'https://t.me/AtomicBot?start=refgift_123456',
        ])
        self.assertEqual(
            [row[0].to_dict().get('style') for row in rows], ['success', 'primary', 'danger'],
        )

    def test_page_keyboard_share_button_first_and_no_leaderboard(self):
        for inline_ok in (True, False):
            keyboard = referral.page_keyboard('AtomicBot', 123456, inline_ok)
            flat = [btn for row in keyboard.inline_keyboard for btn in row]
            share = keyboard.inline_keyboard[0][0]
            self.assertEqual(share.to_dict().get('style'), 'success')
            if inline_ok:
                self.assertIsNotNone(share.switch_inline_query_chosen_chat)
            else:
                self.assertTrue(share.url.startswith('https://t.me/share/url'))
                self.assertIn('start=ref_123456', unquote(share.url))
            callbacks = [btn.callback_data for btn in flat]
            self.assertIn('refu_banner', callbacks)
            self.assertIn('refu_mine', callbacks)
            self.assertNotIn('refu_top', callbacks)
            self.assertTrue(any(btn.copy_text and 'ref_123456' in btn.copy_text.text for btn in flat))
            for btn in flat:
                self.assertIn(btn.to_dict().get('style'), ('primary', 'success', 'danger'), btn.text)

    def test_banner_is_followed_by_share_button(self):
        bot = _Bot()
        user = SimpleNamespace(id=123456, first_name='Ali', username='ali')
        asyncio.run(referral.send_banner(bot, 123456, user, _enabled_settings(), None, inline_ok=False))
        self.assertEqual(len(bot.sent), 2)
        banner_markup = bot.sent[0][2]['reply_markup'].inline_keyboard
        self.assertEqual(banner_markup[1][0].url, 'https://t.me/AtomicBot?start=refgem_123456')
        hint_markup = bot.sent[1][2]['reply_markup'].inline_keyboard
        self.assertTrue(hint_markup[0][0].url.startswith('https://t.me/share/url'))

    def test_inline_mode_is_detected_and_cached(self):
        bot = _Bot(inline=True)
        ctx = SimpleNamespace(bot=bot, bot_data={})
        self.assertTrue(asyncio.run(referral.inline_enabled(ctx)))
        self.assertTrue(asyncio.run(referral.inline_enabled(ctx)))
        self.assertEqual(bot.get_me_calls, 1)
        broken = SimpleNamespace(bot=SimpleNamespace(), bot_data={})
        self.assertFalse(asyncio.run(referral.inline_enabled(broken)))

    def test_admin_callbacks_fit_patterns_and_telegram_limit(self):
        router = re.compile(referral.ADMIN_ROUTER_PATTERN)
        inputs = re.compile(referral.ADMIN_INPUT_PATTERN)
        boards = (
            referral.admin_home_rows(_enabled_settings()),
            referral.admin_settings_rows(_enabled_settings()),
            referral.admin_texts_rows(),
        )
        seen = set()
        for rows in boards:
            for row in rows:
                for btn in row:
                    data = btn.callback_data
                    seen.add(data)
                    self.assertLessEqual(len(data.encode()), 64)
                    if data.startswith('radm_in_'):
                        self.assertTrue(inputs.match(data), data)
                        self.assertIsNone(router.match(data), data)
                        action = data[len('radm_in_'):].partition('_')[0]
                        self.assertIsNotNone(
                            referral._input_prompt(action, '123', rdb.DEFAULT_SETTINGS), data,
                        )
                    elif data.startswith('radm_'):
                        self.assertTrue(router.match(data), data)
        self.assertIn('radm_tg_welcome', seen)
        self.assertNotIn('radm_tg_public', seen)
        self.assertNotIn('radm_tg_inline', seen)
        self.assertTrue(inputs.match('radm_in_msg_123456789'))
        self.assertTrue(inputs.match('radm_in_msgwin_12'))


class WiringTests(unittest.TestCase):
    def test_bot_registers_referral_handlers(self):
        import bot
        source = inspect.getsource(bot.main)
        self.assertIn('referral_admin_conversation_handler()', source)
        self.assertIn('InlineQueryHandler(referral_inline_query)', source)
        self.assertIn('REFERRAL_ADMIN_PATTERN', source)
        self.assertIn("CommandHandler('invite', referral_menu)", source)
        self.assertIs(bot.MENU_ACTIONS['referral'], referral.referral_menu)
        self.assertIn('ensure_referral_schema', inspect.getsource(bot.post_init))

    def test_start_and_forced_join_keep_referral_payload(self):
        from handlers import forced_join, start
        self.assertIn('handle_start_payload', inspect.getsource(start))
        self.assertIn('_remember_referral_start(update, ctx)', inspect.getsource(forced_join.force_join_guard))
        self.assertIn('_resume_referral_start', inspect.getsource(forced_join.force_join_guard))

    def test_start_welcome_text_is_unchanged(self):
        from handlers import start
        source = inspect.getsource(start.start_handler)
        self.assertIn("'t.welcome'", source)
        self.assertIn("stored or appearance.DEFAULTS['t.welcome']", source)

    def test_remember_start_payload_only_for_referral_links(self):
        for text, expected in (
            ('/start ref_123456', 'ref_123456'),
            ('/start refgem_123456', 'refgem_123456'),
            ('/start', None),
            ('/start promo', None),
            ('hello', None),
        ):
            ctx = SimpleNamespace(user_data={})
            referral.remember_start_payload(SimpleNamespace(message=SimpleNamespace(text=text)), ctx)
            self.assertEqual(ctx.user_data.get(referral.START_PAYLOAD_KEY), expected, text)


class StartPayloadFlowTests(unittest.TestCase):
    def _run(self, *, is_new, payload, values):
        bot = _Bot()
        user = SimpleNamespace(id=222333, first_name='Sara', username='sara')
        update = SimpleNamespace(effective_user=user)
        ctx = SimpleNamespace(user_data={referral.START_PAYLOAD_KEY: payload}, bot=bot, bot_data={})
        stats = {'count': 4, 'rank': 2, 'total': 9, 'bought': 1, 'user_id': 7, 'last_at': None}
        recorded = {
            'referrer_user_id': 7, 'referrer_telegram_id': '111222',
            'referrer_first_name': 'Ali', 'referrer_username': 'ali', 'created_at': None,
        }
        with patch.object(rdb, 'settings', return_value=values), \
                patch.object(rdb, 'record_referral', return_value=recorded) as record, \
                patch.object(rdb, 'active_campaign', return_value=None), \
                patch.object(rdb, 'user_stats', return_value=stats), \
                patch.object(referral, 'get_gems_by_id', return_value=SAMPLE_GEMS), \
                patch.object(appearance, '_CACHE', {}):
            asyncio.run(referral.handle_start_payload(update, ctx, 9, is_new, payload))
        self.assertNotIn(referral.START_PAYLOAD_KEY, ctx.user_data)
        return bot, record

    def test_new_user_is_recorded_referrer_notified_and_gem_list_sent(self):
        bot, record = self._run(is_new=True, payload='refgem_111222', values=_enabled_settings())
        record.assert_called_once_with(222333, 9, 111222)
        chats = [chat for chat, _text, _kw in bot.sent]
        self.assertIn(111222, chats)
        self.assertEqual(chats.count(222333), 2)
        welcome = next(text for chat, text, _ in bot.sent if chat == 222333)
        self.assertIn('Ali', welcome)
        gem_chat, gem_text, gem_kwargs = bot.sent[-1]
        self.assertEqual(gem_chat, 222333)
        self.assertIn('جم فری‌فایر با آیدی', gem_text)
        self.assertNotIn('روش خرید', gem_text)
        self.assertEqual(gem_kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'gem_7')

    def test_existing_user_is_never_counted_but_gem_button_still_opens_list(self):
        bot, record = self._run(is_new=False, payload='refgem_111222', values=_enabled_settings())
        record.assert_not_called()
        self.assertEqual([chat for chat, _t, _k in bot.sent], [222333])
        self.assertIn('جم فری‌فایر با آیدی', bot.sent[0][1])

    def test_existing_user_plain_link_sends_nothing_extra(self):
        bot, record = self._run(is_new=False, payload='ref_111222', values=_enabled_settings())
        record.assert_not_called()
        self.assertEqual(bot.sent, [])

    def test_invitee_welcome_can_be_turned_off(self):
        bot, record = self._run(
            is_new=True, payload='ref_111222',
            values=_enabled_settings(referral_invitee_welcome='0'),
        )
        record.assert_called_once()
        self.assertEqual([chat for chat, _t, _k in bot.sent], [111222])

    def test_disabled_section_records_nothing_but_gem_button_still_works(self):
        bot, record = self._run(is_new=True, payload='refgem_111222', values=dict(rdb.DEFAULT_SETTINGS))
        record.assert_not_called()
        self.assertEqual([chat for chat, _t, _k in bot.sent], [222333])


if __name__ == '__main__':
    unittest.main()
