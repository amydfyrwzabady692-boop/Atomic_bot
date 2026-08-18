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
        row = admin_home_keyboard().inline_keyboard[1][0]
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
            self.assertEqual(rows[3][0].text, '🎁 خرید گیفت کارت')
            self.assertEqual(rows[4][0].text, '🎧 پشتیبانی')


if __name__ == '__main__':
    unittest.main()
