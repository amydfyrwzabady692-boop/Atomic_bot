import unittest

from button_style import guess_style
from keyboards import (
    credential_confirm_keyboard,
    freefire_products_keyboard,
    gem_confirm_keyboard,
    gems_list_keyboard,
    main_menu,
    pay_method_keyboard,
)


class ButtonStyleTests(unittest.TestCase):
    def test_guess_buy_and_cancel(self):
        self.assertEqual(guess_style('✅ خرید این بسته', 'gbuy_12'), 'success')
        self.assertEqual(guess_style('✖️ انصراف', 'gem_cancel'), 'danger')
        self.assertEqual(guess_style('🆔 جم با آیدی', 'gems_by_id'), 'primary')
        self.assertEqual(guess_style('🔙 منوی اصلی', 'home'), 'primary')
        self.assertEqual(guess_style('❌ ناموجود — ۱۱۰ جم', 'noop'), 'danger')
        self.assertEqual(guess_style('💰 کیف پول', 'wallet'), 'success')

    def test_inline_payload_includes_style(self):
        kb = freefire_products_keyboard()
        by_id = kb.inline_keyboard[0][0].to_dict()
        creds = kb.inline_keyboard[1][0].to_dict()
        home = kb.inline_keyboard[2][0].to_dict()
        self.assertEqual(by_id.get('style'), 'primary')
        self.assertEqual(creds.get('style'), 'primary')
        self.assertEqual(home.get('style'), 'primary')

        confirm = gem_confirm_keyboard().inline_keyboard
        self.assertEqual(confirm[0][0].to_dict().get('style'), 'success')
        self.assertEqual(confirm[2][0].to_dict().get('style'), 'danger')

        pay = pay_method_keyboard(9, can_wallet=True, wallet_balance=1000).inline_keyboard
        self.assertEqual(pay[0][0].to_dict().get('style'), 'success')
        self.assertEqual(pay[-1][0].to_dict().get('style'), 'danger')

        cred = credential_confirm_keyboard().inline_keyboard
        self.assertEqual(cred[0][0].to_dict().get('style'), 'success')
        self.assertEqual(cred[1][0].to_dict().get('style'), 'danger')

    def test_gem_pack_buttons_are_primary(self):
        gems = [(
            7, '110 جم', 110, 0, 194000, None, 'once',
            'by_id', True, '110', 9999, True,
        )]
        row = gems_list_keyboard(gems, page=1).inline_keyboard[0][0].to_dict()
        self.assertEqual(row.get('style'), 'primary')

    def test_every_inline_button_has_a_color(self):
        gems = [(
            7, '110 جم', 110, 0, 194000, None, 'once',
            'by_id', True, '110', 9999, True,
        )]
        boards = (
            freefire_products_keyboard(),
            gem_confirm_keyboard(),
            credential_confirm_keyboard(),
            pay_method_keyboard(9, can_wallet=True, wallet_balance=1000),
            gems_list_keyboard(gems, page=1),
        )
        for kb in boards:
            for row in kb.inline_keyboard:
                for btn in row:
                    self.assertIn(btn.to_dict().get('style'), ('primary', 'success', 'danger'), btn.text)
        rows = main_menu().keyboard
        self.assertEqual(rows[0][0].to_dict().get('style'), 'primary')
        self.assertEqual(rows[0][1].to_dict().get('style'), 'success')
        self.assertEqual(rows[1][0].to_dict().get('style'), 'primary')
        self.assertEqual(rows[2][0].to_dict().get('style'), 'success')
        self.assertEqual(rows[3][0].to_dict().get('style'), 'success')
        self.assertEqual(rows[4][0].to_dict().get('style'), 'danger')
