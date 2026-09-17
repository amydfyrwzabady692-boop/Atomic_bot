import asyncio
import inspect
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from telegram import MessageEntity
from telegram.error import BadRequest, Forbidden

import events_db as edb
from handlers import game_events as ge
from keyboards import admin_home_keyboard

EVENT = {
    'id': 7, 'photo_file_id': '', 'body': '🔥 اسکین جدید آمد', 'body_entities': '[]',
    'extra_btn_text': '', 'extra_btn_url': '', 'show_site': True, 'pin': False,
    'notify_users': False, 'target_chats': None, 'status': 'draft', 'clicks': 3,
    'created_by': '1', 'created_at': None, 'published_at': None, 'notified_at': None,
    'posts': 0, 'unique_clicks': 2,
}
CHANNELS = [
    {'id': 1, 'chat_id': '-1001', 'title': 'Main', 'username': 'main', 'chat_type': 'channel',
     'can_post': True, 'active': True},
    {'id': 2, 'chat_id': '-1002', 'title': 'News', 'username': '', 'chat_type': 'supergroup',
     'can_post': True, 'active': True},
]


def _event(**kw):
    data = dict(EVENT)
    data.update(kw)
    return data


def _values(**kw):
    data = dict(edb.DEFAULT_SETTINGS)
    data.update(kw)
    return data


class _Bot:
    username = 'AtomicBot'
    id = 999

    def __init__(self, fail_custom=False, fail_chats=()):
        self.sent = []
        self.pinned = []
        self.fail_custom = fail_custom
        self.fail_chats = set(fail_chats)

    def _check(self, chat_id, entities, markup):
        if chat_id in self.fail_chats:
            raise Forbidden('bot was kicked')
        custom = any(str(e.type) == 'custom_emoji' for e in (entities or []))
        icons = any('icon_custom_emoji_id' in b.to_dict() for row in markup.inline_keyboard for b in row)
        if self.fail_custom and (custom or icons):
            raise BadRequest('custom emoji not allowed')

    async def send_message(self, chat_id=None, text=None, entities=None, reply_markup=None, **kw):
        self._check(chat_id, entities, reply_markup)
        self.sent.append(('text', chat_id, text, entities, reply_markup))
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def send_photo(self, chat_id=None, photo=None, caption=None, caption_entities=None,
                         reply_markup=None, **kw):
        self._check(chat_id, caption_entities, reply_markup)
        self.sent.append(('photo', chat_id, caption, caption_entities, reply_markup))
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def pin_chat_message(self, chat_id=None, message_id=None, **kw):
        self.pinned.append((chat_id, message_id))


class MarkupTests(unittest.TestCase):
    def test_buttons_colors_links_and_utm(self):
        markup = ge.event_markup('AtomicBot', _event(extra_btn_text='🎮 تریلر',
                                                     extra_btn_url='https://youtu.be/x'), _values())
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 3)
        bot_btn, site_btn, extra_btn = rows[0][0], rows[1][0], rows[2][0]
        self.assertEqual(bot_btn.url, 'https://t.me/AtomicBot?start=evgem_7')
        self.assertEqual(bot_btn.to_dict().get('style'), 'success')
        parsed = urlparse(site_btn.url)
        self.assertEqual(parsed.netloc, 'atomicshop.ir')
        self.assertEqual(parse_qs(parsed.query)['utm_campaign'], ['event_7'])
        self.assertEqual(site_btn.to_dict().get('style'), 'primary')
        self.assertEqual(extra_btn.url, 'https://youtu.be/x')
        self.assertEqual(extra_btn.to_dict().get('style'), 'danger')

    def test_site_button_can_be_hidden_and_bad_url_falls_back(self):
        rows = ge.event_markup('AtomicBot', _event(show_site=False), _values()).inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertTrue(ge.site_link(_values(event_site_url='javascript:x'), 1).startswith('https://atomicshop.ir'))
        self.assertTrue(ge.site_link(_values(event_site_url='shop.example.com/gems?a=1'), 2)
                        .startswith('https://shop.example.com/gems?a=1&'))

    def test_premium_icons_on_buttons(self):
        rows = ge.event_markup('AtomicBot', _event(), _values(event_btn_bot_emoji='555')).inline_keyboard
        self.assertEqual(rows[0][0].to_dict().get('icon_custom_emoji_id'), '555')
        plain = ge.event_markup('AtomicBot', _event(), _values(event_btn_bot_emoji='555'), icons=False)
        self.assertNotEqual(plain.inline_keyboard[0][0].to_dict().get('icon_custom_emoji_id'), '555')

    def test_start_link_fits_telegram(self):
        payload = ge.bot_link('AtomicBot', 2_147_483_647).split('start=')[1]
        self.assertLessEqual(len(payload), 64)
        self.assertRegex(payload, r'^[A-Za-z0-9_-]+$')
        self.assertTrue(re.match(ge.START_PATTERN, f'/start {payload}'))
        self.assertIsNone(re.match(ge.START_PATTERN, '/start ref_123'))
        self.assertIsNone(re.match(ge.START_PATTERN, '/start'))


class EntityTests(unittest.TestCase):
    def test_premium_emoji_entities_round_trip(self):
        entities = [
            MessageEntity(type='custom_emoji', offset=0, length=2, custom_emoji_id='123'),
            MessageEntity(type='bold', offset=3, length=4),
        ]
        restored = ge.entities_from_json(ge.entities_to_json(entities))
        self.assertEqual([e.to_dict() for e in restored], [e.to_dict() for e in entities])
        self.assertTrue(ge.has_premium_emoji(_event(body_entities=ge.entities_to_json(entities))))
        self.assertEqual(ge.entities_from_json('not json'), [])

    def test_send_event_keeps_entities_then_falls_back_without_custom_emoji(self):
        entities = ge.entities_to_json([
            MessageEntity(type='custom_emoji', offset=0, length=2, custom_emoji_id='123'),
            MessageEntity(type='bold', offset=3, length=4),
        ])
        event = _event(body_entities=entities)
        bot = _Bot()
        _msg, kind = asyncio.run(ge.send_event(bot, 5, event, _values()))
        self.assertEqual(kind, 'text')
        self.assertEqual(len(bot.sent[0][3]), 2)

        strict = _Bot(fail_custom=True)
        asyncio.run(ge.send_event(strict, 5, event, _values()))
        self.assertEqual([str(e.type) for e in strict.sent[0][3]], ['bold'])

    def test_empty_entities_are_sent_as_none(self):
        bot = _Bot()
        asyncio.run(ge.send_event(bot, 5, _event(), _values()))
        self.assertIsNone(bot.sent[0][3])

    def test_photo_event_uses_caption(self):
        bot = _Bot()
        _msg, kind = asyncio.run(ge.send_event(bot, 5, _event(photo_file_id='PH', body=''), _values()))
        self.assertEqual(kind, 'photo')
        self.assertIsNone(bot.sent[0][2])


class ChannelTests(unittest.TestCase):
    def test_parse_chat_ref(self):
        origin = SimpleNamespace(chat=SimpleNamespace(id=-100555))
        self.assertEqual(ge.parse_chat_ref(SimpleNamespace(forward_origin=origin, text=None)), -100555)
        for text, expected in (
            ('@Omid_AtomicFF', '@Omid_AtomicFF'),
            ('https://t.me/Omid_AtomicFF', '@Omid_AtomicFF'),
            ('t.me/Omid_AtomicFF/12', '@Omid_AtomicFF'),
            ('-100۱۲۳۴۵۶۷۸', -10012345678),
            ('hello there', None),
            ('', None),
        ):
            msg = SimpleNamespace(forward_origin=None, forward_from_chat=None, text=text)
            self.assertEqual(ge.parse_chat_ref(msg), expected, text)

    def test_selected_channel_ids(self):
        self.assertEqual(ge.selected_channel_ids(_event(), CHANNELS), {1, 2})
        self.assertEqual(ge.selected_channel_ids(_event(target_chats=[2, 9]), CHANNELS), {2})
        self.assertEqual(ge.selected_channel_ids(_event(target_chats=[]), CHANNELS), set())

    def test_check_chat_requires_admin_and_post_right(self):
        chat = SimpleNamespace(id=-1001, type='channel', title='Main', username='main')

        def bot_with(member):
            return SimpleNamespace(id=999, get_chat=AsyncMock(return_value=chat),
                                   get_chat_member=AsyncMock(return_value=member))

        with patch.object(edb, 'upsert_channel', side_effect=lambda *a: {
            'id': 1, 'chat_id': str(a[0]), 'title': a[1], 'can_post': a[4]}) as upsert, \
                patch.object(edb, 'deactivate_channel') as deactivate:
            channel, error = asyncio.run(ge.check_chat(
                bot_with(SimpleNamespace(status='administrator', can_post_messages=True)), '@main'))
            self.assertTrue(channel['can_post'])
            self.assertEqual(error, '')
            channel, error = asyncio.run(ge.check_chat(
                bot_with(SimpleNamespace(status='administrator', can_post_messages=False)), '@main'))
            self.assertFalse(channel['can_post'])
            self.assertIn('ارسال پست', error)
            channel, error = asyncio.run(ge.check_chat(bot_with(SimpleNamespace(status='left')), '@main'))
            self.assertIsNone(channel)
            deactivate.assert_called_once()
            self.assertEqual(upsert.call_count, 2)

        broken = SimpleNamespace(id=999, get_chat=AsyncMock(side_effect=BadRequest('chat not found')))
        channel, error = asyncio.run(ge.check_chat(broken, '@nope'))
        self.assertIsNone(channel)
        self.assertIn('دسترسی', error)

    def test_membership_tracker(self):
        def update(status, chat_type='channel', can_post=True):
            return SimpleNamespace(my_chat_member=SimpleNamespace(
                chat=SimpleNamespace(id=-1003, type=chat_type, title='T', username=''),
                new_chat_member=SimpleNamespace(status=status, can_post_messages=can_post),
            ))

        with patch.object(edb, 'upsert_channel') as upsert, \
                patch.object(edb, 'deactivate_channel') as deactivate:
            asyncio.run(ge.track_bot_membership(update('administrator'), None))
            asyncio.run(ge.track_bot_membership(update('left'), None))
            asyncio.run(ge.track_bot_membership(update('administrator', 'private'), None))
        upsert.assert_called_once()
        self.assertTrue(upsert.call_args.args[4])
        deactivate.assert_called_once_with(-1003)


class PublishTests(unittest.TestCase):
    def test_publish_sends_to_selected_skips_posted_and_pins(self):
        bot = _Bot(fail_chats={-1002})
        event = _event(pin=True)
        with patch.object(edb, 'get_event', return_value=event), \
                patch.object(edb, 'settings', return_value=_values()), \
                patch.object(edb, 'list_channels', return_value=CHANNELS + [
                    {'id': 3, 'chat_id': '-1004', 'title': 'Old', 'username': '',
                     'chat_type': 'channel', 'can_post': True, 'active': True}]), \
                patch.object(edb, 'list_posts', return_value=[{'chat_id': '-1004'}]), \
                patch.object(edb, 'record_post') as record, \
                patch.object(edb, 'deactivate_channel') as deactivate, \
                patch.object(edb, 'mark_published') as published, \
                patch.object(ge.asyncio, 'sleep', AsyncMock()):
            sent, skipped, failed = asyncio.run(ge.publish_event(bot, 7))
        self.assertEqual(sent, ['Main'])
        self.assertEqual(skipped, ['Old'])
        self.assertEqual(len(failed), 1)
        record.assert_called_once_with(7, '-1001', 101, 'text')
        self.assertEqual(bot.pinned, [(-1001, 101)])
        deactivate.assert_called_once_with('-1002')
        published.assert_called_once_with(7)

    def test_edit_post_detects_type_mismatch(self):
        result = asyncio.run(ge.edit_post(
            _Bot(), {'kind': 'text', 'chat_id': '-1', 'message_id': 1}, _event(photo_file_id='P'), _values()))
        self.assertEqual(result, 'mismatch')


class AdminViewTests(unittest.TestCase):
    def _all_callbacks(self):
        boards = [
            ge.home_view(3, CHANNELS)[1],
            ge.panel_view(_event(photo_file_id='P', extra_btn_text='x', posts=2), CHANNELS, _values())[1],
            ge.panel_view(_event(), [], _values())[1],
            ge.channel_picker_view(_event(target_chats=[1]), CHANNELS)[1],
            ge.channels_view(CHANNELS)[1],
            ge.settings_view(_values())[1],
            ge.list_view([_event()], 30, 1)[1],
        ]
        return [b.callback_data for rows in boards for row in rows for b in row if b.callback_data]

    def test_callbacks_route_and_fit_limit(self):
        router = re.compile(ge.ROUTER_PATTERN)
        inputs = re.compile(ge.INPUT_PATTERN)
        for data in self._all_callbacks():
            self.assertLessEqual(len(data.encode()), 64, data)
            if data.startswith('gevin_'):
                self.assertTrue(inputs.match(data), data)
                self.assertIsNone(router.match(data), data)
            elif data.startswith('gev'):
                self.assertTrue(router.match(data), data)
            else:
                self.assertIn(data, ('adm_home',))

    def test_panel_removal_buttons_keep_one_of_photo_or_text(self):
        only_text = [b.callback_data for row in ge.panel_view(_event(), CHANNELS, _values())[1] for b in row]
        self.assertNotIn('gev_rmphoto_7', only_text)
        self.assertNotIn('gev_rmtext_7', only_text)
        both = [b.callback_data for row in ge.panel_view(_event(photo_file_id='P'), CHANNELS, _values())[1]
                for b in row]
        self.assertIn('gev_rmphoto_7', both)
        self.assertIn('gev_rmtext_7', both)

    def test_admin_home_has_entry_without_moving_rows(self):
        rows = admin_home_keyboard().inline_keyboard
        self.assertEqual(rows[0][0].callback_data, 'admx_ops')
        self.assertEqual(rows[3][0].callback_data, 'admx_hub_orders')
        self.assertIn('gev_home', [b.callback_data for row in rows for b in row])


class ConversationTests(unittest.TestCase):
    def _ctx(self, flow):
        return SimpleNamespace(user_data={ge.FLOW_KEY: flow}, bot=_Bot())

    def _query(self, data):
        return SimpleNamespace(
            data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(text='x'), from_user=SimpleNamespace(id=1),
        )

    def test_cannot_skip_both_photo_and_text(self):
        query = self._query('gevs_skiptext')
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1),
                                 effective_message=None)
        ctx = self._ctx({'id': 7, 'mode': 'new'})
        with patch.object(ge, 'is_admin', return_value=True), \
                patch.object(edb, 'get_event', return_value=_event(photo_file_id='', body='')):
            state = asyncio.run(ge.skip_text(update, ctx))
        self.assertEqual(state, ge.ST_PHOTO)
        self.assertTrue(query.answer.call_args.kwargs.get('show_alert'))

    def test_text_too_long_for_photo_caption_is_rejected(self):
        message = SimpleNamespace(text='a' * 1100, entities=(), reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1),
                                 callback_query=None, effective_message=message)
        ctx = self._ctx({'id': 7, 'mode': 'edit'})
        with patch.object(ge, 'is_admin', return_value=True), \
                patch.object(edb, 'get_event', return_value=_event(photo_file_id='P')), \
                patch.object(edb, 'update_event') as update_event:
            state = asyncio.run(ge.receive_text(update, ctx))
        self.assertEqual(state, ge.ST_TEXT)
        update_event.assert_not_called()

    def test_text_with_premium_emoji_is_saved(self):
        entity = MessageEntity(type='custom_emoji', offset=0, length=2, custom_emoji_id='42')
        message = SimpleNamespace(text='🔥 آیتم جدید', entities=(entity,), reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1),
                                 callback_query=None, effective_message=message)
        ctx = self._ctx({'id': 7, 'mode': 'new'})
        with patch.object(ge, 'is_admin', return_value=True), \
                patch.object(edb, 'get_event', return_value=_event(body='')), \
                patch.object(edb, 'update_event') as update_event, \
                patch.object(ge, 'send_preview', AsyncMock()) as preview:
            state = asyncio.run(ge.receive_text(update, ctx))
        self.assertEqual(state, ge.ConversationHandler.END)
        saved = update_event.call_args.kwargs
        self.assertEqual(saved['body'], '🔥 آیتم جدید')
        self.assertIn('"custom_emoji_id": "42"', saved['body_entities'])
        preview.assert_awaited_once()
        self.assertNotIn(ge.FLOW_KEY, ctx.user_data)


class WiringTests(unittest.TestCase):
    def test_bot_registers_event_handlers_before_generic_start(self):
        import bot
        source = inspect.getsource(bot.main)
        self.assertLess(source.index('game_events.start_link_handler()'),
                        source.index("CommandHandler('start', start_handler)"))
        self.assertIn('game_events.register(app)', source)
        self.assertIn('ensure_events_schema', inspect.getsource(bot.post_init))

    def test_register_uses_separate_group_for_chat_member(self):
        added = []
        app = SimpleNamespace(add_handler=lambda handler, group=0: added.append((type(handler).__name__, group)))
        ge.register(app)
        self.assertIn(('ChatMemberHandler', -2), added)
        self.assertIn(('ConversationHandler', 0), added)
        self.assertIn(('CallbackQueryHandler', 0), added)

    def test_schema_is_additive_only(self):
        joined = '\n'.join(edb._SCHEMA)
        self.assertNotIn('DROP', joined.upper())
        self.assertNotIn('ALTER TABLE', joined.upper())
        for table in ('"Orders"', '"Wallets"', '"Users"'):
            self.assertNotIn(f'TABLE IF NOT EXISTS {table}', joined)

    def test_update_event_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            edb.update_event(1, clicks=5)

    def test_event_title(self):
        self.assertEqual(edb.event_title(_event(body='خط اول\nخط دوم')), 'خط اول')
        self.assertEqual(edb.event_title(_event(body='', photo_file_id='P')), '🖼 رویداد تصویری')


if __name__ == '__main__':
    unittest.main()
