import asyncio
import inspect
import re
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

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


def _flat(markup):
    return [btn for row in markup.inline_keyboard for btn in row]


class PayloadAndFormattingTests(unittest.TestCase):
    def test_build_and_parse_payload_kinds(self):
        self.assertEqual(rdb.build_payload(123456789), 'ref_123456789')
        self.assertEqual(rdb.build_payload(123456789, 'gift'), 'refgift_123456789')
        self.assertEqual(rdb.parse_payload('refgift_123456789'), (123456789, 'gift'))
        self.assertEqual(rdb.parse_payload('refgem_123456789'), (123456789, 'gem'))
        self.assertEqual(rdb.parse_payload('ref_123456789'), (123456789, ''))
        self.assertEqual(rdb.parse_payload('ref_abc'), (None, ''))
        self.assertEqual(rdb.parse_payload('promo_123456'), (None, ''))
        self.assertEqual(
            rdb.referral_link('@AtomicBot', 555666, 'gift'),
            'https://t.me/AtomicBot?start=refgift_555666',
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


class GapTextTests(unittest.TestCase):
    def test_no_points_yet(self):
        self.assertIn('اولین دوستت', referral.gap_text({'rank': None, 'count': 0}))

    def test_behind_someone_shows_distance_and_needed(self):
        text = referral.gap_text({'rank': 3, 'count': 5, 'above_count': 8, 'below_count': 2})
        self.assertIn('رتبه‌ی 2', text)
        self.assertIn('3 امتیاز', text)
        self.assertIn('4 دعوت', text)

    def test_tied_with_rank_above(self):
        text = referral.gap_text({'rank': 2, 'count': 4, 'above_count': 4})
        self.assertIn('هم‌امتیاز', text)
        self.assertIn('1 دعوت', text)

    def test_leader_shows_lead_over_second(self):
        self.assertIn('6 امتیاز', referral.gap_text({'rank': 1, 'count': 10, 'below_count': 4}))
        self.assertIn('هم‌امتیاز', referral.gap_text({'rank': 1, 'count': 4, 'below_count': 4}))
        self.assertEqual(referral.gap_text({'rank': 1, 'count': 4, 'below_count': None}), '👑 نفر اول جدولی!')

    def test_page_default_shows_rank_and_gap_but_no_link(self):
        page = rdb.DEFAULT_SETTINGS['referral_page_text']
        self.assertIn('{rank}', page)
        self.assertIn('{gap}', page)
        self.assertNotIn('{link}', page)
        self.assertIn('{gap}', rdb.DEFAULT_SETTINGS['referral_notify_text'])

    def test_user_stats_returns_neighbour_counts(self):
        source = inspect.getsource(rdb.user_stats)
        self.assertIn('MIN(c.cnt)', source)
        self.assertIn('MAX(c.cnt)', source)
        self.assertIn("'above_count'", source)
        self.assertIn("'below_count'", source)


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

    def test_removed_settings_are_gone(self):
        for key in ('referral_public_top', 'referral_inline_share', 'referral_btn_join', 'referral_btn_gems'):
            self.assertNotIn(key, rdb.DEFAULT_SETTINGS)
            self.assertNotIn(key, rdb.TEXT_KEYS)
        self.assertIn('referral_btn_gift', rdb.TEXT_KEYS)
        self.assertFalse(hasattr(referral, '_public_top_text'))
        self.assertFalse(hasattr(referral, 'share_url'))

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
    def test_banner_has_only_the_red_contest_button(self):
        rows = referral.banner_keyboard('AtomicBot', 123456, rdb.DEFAULT_SETTINGS).inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        button = rows[0][0]
        self.assertEqual(button.text, '🔥 شرکت در مسابقه جایزه‌دار')
        self.assertEqual(button.url, 'https://t.me/AtomicBot?start=refgift_123456')
        self.assertEqual(button.to_dict().get('style'), 'danger')

    def test_page_keyboard_sends_banner_never_a_bare_link(self):
        for inline_ok in (True, False):
            keyboard = referral.page_keyboard(inline_ok)
            share = keyboard.inline_keyboard[0][0]
            self.assertEqual(share.text, '📨 ارسال بنر برای دوستان')
            self.assertEqual(share.to_dict().get('style'), 'success')
            if inline_ok:
                self.assertIsNotNone(share.switch_inline_query_chosen_chat)
            else:
                self.assertEqual(share.callback_data, 'refu_banner')
            flat = _flat(keyboard)
            self.assertFalse(any(btn.url for btn in flat), 'no link buttons on the page')
            self.assertFalse(any(btn.copy_text for btn in flat), 'no copy-link button')
            callbacks = [btn.callback_data for btn in flat]
            self.assertIn('refu_mine', callbacks)
            self.assertNotIn('refu_top', callbacks)
            for btn in flat:
                self.assertIn(btn.to_dict().get('style'), ('primary', 'success', 'danger'), btn.text)

    def test_banner_hint_without_inline_has_no_link(self):
        bot = _Bot()
        user = SimpleNamespace(id=123456, first_name='Ali', username='ali')
        asyncio.run(referral.send_banner(bot, 123456, user, _enabled_settings(), None, inline_ok=False))
        self.assertEqual(len(bot.sent), 2)
        banner = _flat(bot.sent[0][2]['reply_markup'])
        self.assertEqual([btn.url for btn in banner], ['https://t.me/AtomicBot?start=refgift_123456'])
        hint = _flat(bot.sent[1][2]['reply_markup'])
        self.assertEqual([btn.callback_data for btn in hint], ['refu_home'])
        self.assertIn('فوروارد', bot.sent[1][1])

    def test_banner_hint_with_inline_offers_direct_send(self):
        bot = _Bot()
        user = SimpleNamespace(id=123456, first_name='Ali', username='ali')
        asyncio.run(referral.send_banner(bot, 123456, user, _enabled_settings(), None, inline_ok=True))
        hint = _flat(bot.sent[1][2]['reply_markup'])
        self.assertIsNotNone(hint[0].switch_inline_query_chosen_chat)
        self.assertFalse(any(btn.url for btn in hint))

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
        self.assertIn('radm_in_bgift', seen)
        for gone in ('radm_tg_public', 'radm_tg_inline', 'radm_in_bjoin', 'radm_in_bgems'):
            self.assertNotIn(gone, seen)
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
            ('/start refgift_123456', 'refgift_123456'),
            ('/start', None),
            ('/start promo', None),
            ('hello', None),
        ):
            ctx = SimpleNamespace(user_data={})
            referral.remember_start_payload(SimpleNamespace(message=SimpleNamespace(text=text)), ctx)
            self.assertEqual(ctx.user_data.get(referral.START_PAYLOAD_KEY), expected, text)


class PersonalBannerTests(unittest.TestCase):
    ACCOUNTS = ((111111, 'Omid', ''), (222222, 'Sara', 'Karimi'), (333333, 'رضا', ''))

    def test_inviter_name_is_each_users_telegram_name(self):
        self.assertEqual(
            referral.inviter_name(SimpleNamespace(first_name='Sara', last_name='Karimi', username='s')),
            'Sara Karimi',
        )
        self.assertEqual(
            referral.inviter_name(SimpleNamespace(first_name='', last_name='', username='@reza')), 'reza',
        )
        self.assertEqual(
            referral.inviter_name(SimpleNamespace(first_name=None, last_name=None, username=None)), 'دوستت',
        )

    def test_banner_template_always_personal(self):
        self.assertIn('{inviter}', referral.banner_template('بنر ثابت omid'))
        self.assertEqual(referral.banner_template('از طرف {inviter}'), 'از طرف {inviter}')

    def test_each_account_gets_its_own_name_and_referral_even_with_stale_text(self):
        for banner_text in (rdb.DEFAULT_SETTINGS['referral_banner_text'], '🎟 دعوت از طرف *omid*'):
            values = _enabled_settings(referral_banner_text=banner_text)
            for uid, first, last in self.ACCOUNTS:
                bot = _Bot()
                user = SimpleNamespace(id=uid, first_name=first, last_name=last, username='')
                asyncio.run(referral.send_banner(bot, uid, user, values, None))
                text = bot.sent[0][1]
                self.assertIn(referral.markdown_safe(f'{first} {last}'.strip()), text)
                for other_uid, other_first, _l in self.ACCOUNTS:
                    if other_uid != uid and other_first != 'Omid':
                        self.assertNotIn(other_first, text)
                button = bot.sent[0][2]['reply_markup'].inline_keyboard[0][0]
                self.assertEqual(button.url, f'https://t.me/AtomicBot?start=refgift_{uid}')

    def test_inline_banner_is_built_for_the_sender(self):
        result_ids = set()
        for uid, first, last in self.ACCOUNTS:
            captured = {}

            async def answer(results, **kwargs):
                captured['results'] = results
                captured['kwargs'] = kwargs

            sender = SimpleNamespace(id=uid, first_name=first, last_name=last, username='')
            update = SimpleNamespace(inline_query=SimpleNamespace(from_user=sender, answer=answer))
            ctx = SimpleNamespace(bot=_Bot(), bot_data={})
            with patch.object(rdb, 'settings', return_value=_enabled_settings()), \
                    patch.object(rdb, 'active_campaign', return_value=None), \
                    patch.object(referral, 'is_user_blocked', return_value=False):
                asyncio.run(referral.referral_inline_query(update, ctx))
            result = captured['results'][0]
            self.assertIn(referral.markdown_safe(f'{first} {last}'.strip()),
                          result.input_message_content.message_text)
            self.assertEqual(result.reply_markup.inline_keyboard[0][0].url,
                             f'https://t.me/AtomicBot?start=refgift_{uid}')
            self.assertTrue(captured['kwargs'].get('is_personal'))
            self.assertEqual(captured['kwargs'].get('cache_time'), 0)
            result_ids.add(result.id)
        self.assertEqual(len(result_ids), len(self.ACCOUNTS))

    def test_admin_cannot_hardcode_a_name_into_the_banner(self):
        with self.assertRaises(ValueError):
            referral.check_text_input('tbanner', '🎟 دعوت از طرف omid')
        referral.check_text_input('tbanner', '🎟 دعوت از طرف {inviter}')
        with self.assertRaises(ValueError):
            referral.check_text_input('bgift', 'دو\nخط')
        warn_values = _enabled_settings(referral_banner_text='بنر omid')
        self.assertIn('{inviter}', referral.admin_texts_text(warn_values))
        self.assertNotIn('⚠️', referral.admin_texts_text(_enabled_settings()))


class StartPayloadFlowTests(unittest.TestCase):
    def _run(self, *, is_new, payload, values):
        bot = _Bot()
        user = SimpleNamespace(id=222333, first_name='Sara', username='sara')
        update = SimpleNamespace(effective_user=user)
        ctx = SimpleNamespace(user_data={referral.START_PAYLOAD_KEY: payload}, bot=bot, bot_data={})
        stats = {'count': 4, 'rank': 2, 'total': 9, 'bought': 1, 'user_id': 7, 'last_at': None,
                 'above_count': 6, 'below_count': 1}
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

    def test_new_user_from_banner_is_recorded_and_referrer_sees_gap(self):
        bot, record = self._run(is_new=True, payload='refgift_111222', values=_enabled_settings())
        record.assert_called_once_with(222333, 9, 111222)
        by_chat = {}
        for chat, text, _kw in bot.sent:
            by_chat.setdefault(chat, []).append(text)
        self.assertEqual(len(by_chat[111222]), 1)
        self.assertIn('رتبه‌ی 1', by_chat[111222][0])
        self.assertEqual(len(by_chat[222333]), 1)
        self.assertIn('Ali', by_chat[222333][0])

    def test_existing_user_from_banner_sees_contest_page_without_points(self):
        bot, record = self._run(is_new=False, payload='refgift_111222', values=_enabled_settings())
        record.assert_not_called()
        self.assertEqual([chat for chat, _t, _k in bot.sent], [222333])
        page_text = bot.sent[0][1]
        self.assertIn('رتبه‌ی فعلی', page_text)
        self.assertIn('رتبه‌ی 1', page_text)
        share = bot.sent[0][2]['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(share.text, '📨 ارسال بنر برای دوستان')

    def test_old_gem_banner_still_opens_gem_list(self):
        bot, record = self._run(is_new=False, payload='refgem_111222', values=_enabled_settings())
        record.assert_not_called()
        self.assertEqual([chat for chat, _t, _k in bot.sent], [222333])
        self.assertIn('جم فری‌فایر با آیدی', bot.sent[0][1])
        self.assertEqual(bot.sent[0][2]['reply_markup'].inline_keyboard[0][0].callback_data, 'gem_7')

    def test_invitee_welcome_can_be_turned_off(self):
        bot, record = self._run(
            is_new=True, payload='refgift_111222',
            values=_enabled_settings(referral_invitee_welcome='0'),
        )
        record.assert_called_once()
        self.assertEqual([chat for chat, _t, _k in bot.sent], [111222])

    def test_disabled_section_records_nothing(self):
        bot, record = self._run(is_new=True, payload='refgift_111222', values=dict(rdb.DEFAULT_SETTINGS))
        record.assert_not_called()
        self.assertEqual(bot.sent, [])


if __name__ == '__main__':
    unittest.main()
