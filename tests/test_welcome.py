"""پیام /start، متن خوش‌آمد و ورودیِ تایپیِ پنل.

روی سرور متن خوش‌آمد شده بود «🎮 محصولات فری‌فایر»: مدیر وسطِ واردکردنِ متن
دکمهٔ منوی پایین را زده بود و برچسبِ دکمه به‌جای متن ذخیره شده بود؛ هر کاربر
با /start فقط همان یک خط را می‌دید.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

import appearance
import handlers.admin_extended as ext
import handlers.premium_admin as studio
from handlers.start import welcome_kwargs

MENU_LABEL = '🎮 محصولات فری‌فایر'


def _message(text):
    return SimpleNamespace(text=text, reply_text=AsyncMock())


class WelcomeTextTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(appearance, '_CACHE', {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_menu_label_saved_by_mistake_is_ignored(self):
        text = welcome_kwargs(MENU_LABEL, 'علی', True)['text']
        self.assertNotEqual(text.strip(), MENU_LABEL)
        self.assertIn('سلام علی', text)
        self.assertIn('اتومیک شاپ', text)

    def test_new_and_returning_users_are_greeted_differently(self):
        new = welcome_kwargs('', 'علی', True)['text']
        back = welcome_kwargs('', 'علی', False)['text']
        self.assertIn('خوش اومدی', new)
        self.assertIn('خوش برگشتی', back)
        self.assertNotIn('{', new + back)

    def test_admin_text_is_kept_with_its_placeholders(self):
        text = welcome_kwargs('سلام {name}! {welcome}', 'علی', False)['text']
        self.assertTrue(text.endswith('سلام علی! خوش برگشتی! 🌟'), text)

    def test_admin_text_with_stray_braces_still_gets_the_name(self):
        text = welcome_kwargs('{name} عزیز {', 'علی', True)['text']
        self.assertIn('علی عزیز', text)

    def test_is_menu_tap(self):
        self.assertTrue(appearance.is_menu_tap(MENU_LABEL))
        self.assertTrue(appearance.is_menu_tap('محصولات فری‌فایر'))
        self.assertFalse(appearance.is_menu_tap('سلام، به فروشگاه ما خوش آمدید'))
        self.assertFalse(appearance.is_menu_tap(''))


class MenuTapIsNotSavedTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(appearance, '_CACHE', {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_admin_input_ignores_a_menu_tap(self):
        message = _message(MENU_LABEL)
        update = SimpleNamespace(message=message, callback_query=None,
                                 effective_user=SimpleNamespace(id=1))
        ctx = SimpleNamespace(user_data={'admin_ext_action': 'setting:welcome_text'})
        with patch.object(ext, 'is_admin', return_value=True), \
                patch.object(ext, 'set_setting') as save:
            result = asyncio.run(ext.admin_input_receive(update, ctx))
        self.assertEqual(result, ConversationHandler.END)
        save.assert_not_called()
        self.assertNotIn('admin_ext_action', ctx.user_data)
        self.assertIn('چیزی ذخیره نشد', message.reply_text.call_args.args[0])

    def test_studio_ignores_a_menu_tap(self):
        message = _message(MENU_LABEL)
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
        ctx = SimpleNamespace(user_data={'studio_edit': {'key': 'welcome_text', 'limit': 4000}})
        with patch.object(studio, '_guard', AsyncMock(return_value=True)), \
                patch.object(studio, '_keyboard', return_value=None), \
                patch.object(studio, 'set_setting') as save:
            result = asyncio.run(studio.studio_edit_receive(update, ctx))
        self.assertEqual(result, ConversationHandler.END)
        save.assert_not_called()


class WelcomePanelTests(unittest.TestCase):
    def run_router(self, data, **fakes):
        query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                                message=_message('x'), from_user=SimpleNamespace(id=1))
        update = SimpleNamespace(callback_query=query, effective_message=None,
                                 effective_user=SimpleNamespace(id=1, first_name='علی'))
        fakes.setdefault('_low_stock_threshold', lambda: 3)
        with patch.object(appearance, '_CACHE', {}), \
                patch.object(ext, 'is_admin', return_value=True), \
                patch.object(ext, '_edit', AsyncMock()) as edit, \
                patch.multiple(ext, **fakes):
            asyncio.run(ext.admin_ext_router(update, SimpleNamespace(bot=None)))
        return query, edit

    def test_settings_page_has_preview_and_reset(self):
        _, edit = self.run_router('admx_settings')
        callbacks = [b.callback_data for row in edit.call_args.args[2] for b in row]
        self.assertIn('admi_welcome', callbacks)
        self.assertIn('admx_welcome_preview', callbacks)
        self.assertIn('admx_welcome_reset', callbacks)

    def test_preview_shows_the_new_and_the_returning_version(self):
        query, _ = self.run_router('admx_welcome_preview', get_setting=lambda *a, **k: MENU_LABEL)
        texts = [c.kwargs.get('text') or c.args[0] for c in query.message.reply_text.call_args_list]
        self.assertEqual(len(texts), 3)
        self.assertIn('خوش اومدی', texts[1])
        self.assertIn('خوش برگشتی', texts[2])
        self.assertIn('سلام علی', texts[2])

    def test_reset_asks_first_then_clears_the_text(self):
        with patch.object(ext, 'set_setting') as save:
            _, edit = self.run_router('admx_welcome_reset')
            save.assert_not_called()
            callbacks = [b.callback_data for row in edit.call_args.args[2] for b in row]
            self.assertEqual(callbacks, ['admx_welcome_resetok', 'admx_settings'])
            self.run_router('admx_welcome_resetok')
        save.assert_called_once_with('welcome_text', '')


if __name__ == '__main__':
    unittest.main()
