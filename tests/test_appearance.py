import unittest
from types import SimpleNamespace
from unittest.mock import patch

import appearance
from keyboards import (
    admin_home_keyboard, freefire_products_keyboard, gems_list_keyboard,
    main_menu, pay_method_keyboard,
)


class AppearanceTests(unittest.TestCase):
    def tearDown(self):
        appearance.invalidate_cache()

    def test_menu_custom_label_still_maps_to_same_action(self):
        appearance._CACHE = {
            'b.menu.ff': {'text': 'فروش جم ویژه', 'emoji_id': '', 'emoji_char': ''},
        }
        self.assertEqual(appearance.menu_action('فروش جم ویژه'), 'ff')
        self.assertEqual(appearance.menu_action('🎮 محصولات فری‌فایر'), 'ff')
        self.assertEqual(appearance.menu_action('💰 کیف پول'), 'wallet')

    def test_gem_button_keeps_callback_and_adds_premium_icon(self):
        appearance._CACHE = {
            'g.7': {
                'text': 'بسته ویژه ۱۱۰',
                'emoji_id': '5408846744727334338',
                'emoji_char': '⭐',
            },
        }
        gems = [(
            7, '110 جم', 110, 0, 194000, None, 'once',
            'by_id', True, '110', 9999, True,
        )]
        btn = gems_list_keyboard(gems, page=1).inline_keyboard[0][0]
        data = btn.to_dict()
        self.assertEqual(btn.callback_data, 'gem_7')
        self.assertIn('بسته ویژه ۱۱۰', btn.text)
        self.assertIn('194,000', btn.text)
        self.assertEqual(data.get('icon_custom_emoji_id'), '5408846744727334338')
        self.assertEqual(data.get('style'), 'primary')

    def test_freefire_choice_callbacks_unchanged(self):
        kb = freefire_products_keyboard()
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, 'gems_by_id')
        self.assertEqual(kb.inline_keyboard[1][0].callback_data, 'gems_credentials')

    def test_pay_callbacks_unchanged(self):
        kb = pay_method_keyboard(9, can_wallet=True, wallet_balance=1000)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, 'pay_zp_9')
        self.assertEqual(kb.inline_keyboard[1][0].callback_data, 'pay_card_9')
        self.assertEqual(kb.inline_keyboard[2][0].callback_data, 'pay_wallet_9')

    def test_admin_home_has_appearance_hub(self):
        row = admin_home_keyboard().inline_keyboard[2][0]
        self.assertEqual(row.callback_data, 'ap_home')
        self.assertIn('ظاهر', row.text)

    def test_extract_custom_emoji_from_admin_message(self):
        entity = SimpleNamespace(
            type='custom_emoji',
            offset=0,
            length=2,
            custom_emoji_id='999111',
        )
        message = SimpleNamespace(
            text='⭐ بسته',
            caption=None,
            entities=[entity],
            caption_entities=None,
        )
        found = appearance.extract_custom_emoji(message)
        self.assertEqual(found['emoji_id'], '999111')
        self.assertTrue(found['emoji_char'])

    def test_message_kwargs_adds_entity_without_changing_body(self):
        appearance._CACHE = {
            't.gems.hdr': {
                'text': None,
                'emoji_id': '111',
                'emoji_char': '💎',
            },
        }
        payload = appearance.message_kwargs('t.gems.hdr', 'لیست جم', page=1, total=2)
        self.assertTrue(payload['text'].startswith('💎'))
        self.assertIn('لیست جم', payload['text'])
        self.assertEqual(payload['entities'][0].custom_emoji_id, '111')
        self.assertNotIn('parse_mode', payload)

    def test_main_menu_defaults_without_db(self):
        with patch.object(appearance, '_CACHE', {}):
            rows = main_menu().keyboard
            self.assertEqual(rows[0][0].text, '🎮 محصولات فری‌فایر')
            self.assertEqual(rows[3][0].text, '⭐ خرید استارز')
            self.assertEqual(rows[3][1].text, '🎁 خرید گیفت کارت')
            self.assertEqual(rows[4][0].text, '🎧 پشتیبانی')

    def test_icon_for_prefers_product_then_section(self):
        appearance._CACHE = {
            'g.7': {'text': None, 'emoji_id': '111', 'emoji_char': '⭐'},
            'b.menu.ff': {'text': None, 'emoji_id': '222', 'emoji_char': '⭐'},
        }
        self.assertEqual(appearance.icon_for('g.7', 'b.menu.ff'), '111')
        self.assertEqual(appearance.icon_for('g.99', 'b.menu.ff'), '222')
        self.assertEqual(appearance.icon_for('g.99', 'missing'), '')

    def test_gem_button_falls_back_to_section_premium_icon(self):
        appearance._CACHE = {
            'b.menu.ff': {
                'text': None,
                'emoji_id': '5408846744727334338',
                'emoji_char': '⭐',
            },
        }
        gems = [(
            7, '110 جم', 110, 0, 194000, None, 'once',
            'by_id', True, '110', 9999, True,
        )]
        data = gems_list_keyboard(gems, page=1).inline_keyboard[0][0].to_dict()
        self.assertEqual(data.get('icon_custom_emoji_id'), '5408846744727334338')
        self.assertEqual(data.get('callback_data'), 'gem_7')

    def test_main_menu_uses_section_premium_icons(self):
        appearance._CACHE = {
            'b.menu.ff': {'text': None, 'emoji_id': '777', 'emoji_char': '⭐'},
            'b.menu.stars': {'text': None, 'emoji_id': '777', 'emoji_char': '⭐'},
            'b.menu.gc': {'text': None, 'emoji_id': '777', 'emoji_char': '⭐'},
        }
        rows = main_menu().keyboard
        self.assertEqual(rows[0][0].to_dict().get('icon_custom_emoji_id'), '777')
        self.assertEqual(rows[3][0].to_dict().get('icon_custom_emoji_id'), '777')
        self.assertEqual(rows[3][1].to_dict().get('icon_custom_emoji_id'), '777')

    def test_pack_picks_related_emoji_types(self):
        pack = {
            appearance.normalize_emoji_char('⭐'): ('id_star', '⭐'),
            appearance.normalize_emoji_char('🎮'): ('id_game', '🎮'),
            appearance.normalize_emoji_char('🎁'): ('id_gift', '🎁'),
            appearance.normalize_emoji_char('💎'): ('id_gem', '💎'),
            appearance.normalize_emoji_char('💰'): ('id_money', '💰'),
        }
        self.assertEqual(appearance.wanted_emoji_chars('b.menu.stars')[0], '⭐')
        self.assertEqual(appearance.wanted_emoji_chars('b.menu.gc')[0], '🎁')
        self.assertEqual(appearance.wanted_emoji_chars('b.menu.ff')[0], '🎮')
        self.assertEqual(appearance.wanted_emoji_chars('g.4')[0], '💎')
        self.assertEqual(
            appearance.pick_pack_emoji(pack, appearance.wanted_emoji_chars('b.menu.gc'), 'id_star', '⭐'),
            ('id_gift', '🎁'),
        )
        self.assertEqual(
            appearance.pick_pack_emoji(pack, appearance.wanted_emoji_chars('b.menu.ff'), 'id_star', '⭐'),
            ('id_game', '🎮'),
        )
        self.assertEqual(
            appearance.pick_pack_emoji({}, appearance.wanted_emoji_chars('b.menu.gc'), 'id_star', '⭐'),
            ('id_star', '⭐'),
        )


class PremiumEmojiSeedTests(unittest.TestCase):
    def test_cancel_keys_are_not_auto_styled(self):
        import db
        self.assertNotIn('b.gem.no', db._PREMIUM_EMOJI_STATIC_KEYS)
        self.assertNotIn('b.stars.no', db._PREMIUM_EMOJI_STATIC_KEYS)
        self.assertIn('b.menu.ff', db._PREMIUM_EMOJI_STATIC_KEYS)
        self.assertIn('b.pay.zp', db._PREMIUM_EMOJI_STATIC_KEYS)
        self.assertIn('t.gems.hdr', db._PREMIUM_EMOJI_STATIC_KEYS)

    @patch('db.simple_list', return_value=[(3,)])
    @patch('db.list_sense_packages', return_value=[(8,)])
    @patch('db.get_gems_by_credentials', return_value=[(5,)])
    @patch('db.get_gems_by_id', return_value=[(4,)])
    @patch('db.list_star_packages', return_value=[{'id': 9}])
    @patch('db.upsert_appearance')
    @patch('db.list_appearance_rows')
    @patch('db._load_premium_emoji_pack')
    def test_seed_picks_matching_types_from_pack(
        self, load_pack, rows, upsert, _stars, _gems, _creds, _sense, _store,
    ):
        import db
        load_pack.return_value = {
            appearance.normalize_emoji_char('⭐'): ('id_star', '⭐'),
            appearance.normalize_emoji_char('🎮'): ('id_game', '🎮'),
            appearance.normalize_emoji_char('🎁'): ('id_gift', '🎁'),
            appearance.normalize_emoji_char('💎'): ('id_gem', '💎'),
            appearance.normalize_emoji_char('💰'): ('id_money', '💰'),
            appearance.normalize_emoji_char('🎯'): ('id_target', '🎯'),
            appearance.normalize_emoji_char('🛍'): ('id_shop', '🛍'),
        }
        rows.return_value = {
            'legacy.one': {'text': None, 'emoji_id': 'id_star', 'emoji_char': '⭐'},
            'b.menu.stars': {'text': None, 'emoji_id': 'id_star', 'emoji_char': '⭐'},
            'b.menu.ff': {'text': None, 'emoji_id': 'id_star', 'emoji_char': '⭐'},
        }
        db._seed_catalog_premium_emoji()
        by_key = {call.args[0]: call.kwargs.get('emoji_id') for call in upsert.call_args_list}
        self.assertEqual(by_key.get('b.menu.ff'), 'id_game')
        self.assertEqual(by_key.get('b.menu.gc'), 'id_gift')
        self.assertEqual(by_key.get('b.menu.wal'), 'id_money')
        self.assertEqual(by_key.get('st.9'), 'id_star')
        self.assertEqual(by_key.get('g.4'), 'id_gem')
        self.assertEqual(by_key.get('s.8'), 'id_target')
        self.assertEqual(by_key.get('sc.3'), 'id_shop')
        self.assertNotIn('b.menu.stars', by_key)
        self.assertNotIn('b.gem.no', by_key)
        self.assertNotIn('b.stars.no', by_key)


if __name__ == '__main__':
    unittest.main()
