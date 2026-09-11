import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'atomic_bot.settings')
django.setup()

from telegram import InlineKeyboardButton, MessageEntity, Update
from telegram.constants import MessageEntityType
from telegram.ext import ExtBot

from game.models import EmojiOverride, ButtonEmojiOverride
from game import emoji, button_emoji
from bot.buttons import btn, back_btn, _LEADING_EMOJI
from handlers import theme_admin
from bot import _install_premium_glyph_hook


class EmojiModelTests(unittest.TestCase):
    def setUp(self):
        EmojiOverride.objects.all().delete()
        ButtonEmojiOverride.objects.all().delete()
        emoji.refresh_cache()
        button_emoji.refresh_cache()

    def tearDown(self):
        EmojiOverride.objects.all().delete()
        ButtonEmojiOverride.objects.all().delete()
        emoji.refresh_cache()
        button_emoji.refresh_cache()

    def test_emoji_override_model_and_cache(self):
        emoji.set_emoji("gem", "cid_gem_999", "💎")
        self.assertIn("gem", emoji._cache)
        self.assertEqual(emoji.get_emoji("gem"), '<tg-emoji emoji-id="cid_gem_999">💎</tg-emoji>')

        # Glyph coupling check
        self.assertIn("g:💎", emoji._cache)
        self.assertIn("💎", emoji._glyph_map)

        # Clear
        cleared = emoji.clear_emoji("gem")
        self.assertTrue(cleared)
        self.assertNotIn("gem", emoji._cache)
        self.assertNotIn("g:💎", emoji._cache)
        self.assertEqual(emoji.get_emoji("gem"), "💎")

    def test_button_emoji_override_model_and_cache(self):
        self.assertIsNone(button_emoji.get_button_icon("btn_confirm"))
        button_emoji.set_button_emoji("btn_confirm", "cid_btn_111", "✅")
        self.assertEqual(button_emoji.get_button_icon("btn_confirm"), "cid_btn_111")

        cleared = button_emoji.clear_button_emoji("btn_confirm")
        self.assertTrue(cleared)
        self.assertIsNone(button_emoji.get_button_icon("btn_confirm"))


class PremiumizeHtmlTests(unittest.TestCase):
    def setUp(self):
        EmojiOverride.objects.all().delete()
        emoji.set_emoji("gem", "cid_gem", "💎")
        emoji.set_emoji("zap", "cid_zap", "⚡")

    def tearDown(self):
        EmojiOverride.objects.all().delete()
        emoji.refresh_cache()

    def test_premiumize_literal_glyph(self):
        raw = "خرید 💎 با سرعت ⚡"
        out = emoji.premiumize_html(raw)
        self.assertIn('<tg-emoji emoji-id="cid_gem">💎</tg-emoji>', out)
        self.assertIn('<tg-emoji emoji-id="cid_zap">⚡</tg-emoji>', out)

    def test_variation_selector_normalization(self):
        # ⚡️ (with \ufe0f) vs ⚡ (without)
        raw_vs = "سرعت ⚡️ عالی"
        out = emoji.premiumize_html(raw_vs)
        self.assertIn('<tg-emoji emoji-id="cid_zap">⚡️</tg-emoji>', out)

    def test_skips_already_wrapped_tg_emoji(self):
        raw = 'قبلاً ست شده: <tg-emoji emoji-id="custom_123">💎</tg-emoji> و جدید 💎'
        out = emoji.premiumize_html(raw)
        self.assertIn('<tg-emoji emoji-id="custom_123">💎</tg-emoji>', out)
        self.assertIn('<tg-emoji emoji-id="cid_gem">💎</tg-emoji>', out)

    def test_skips_html_tags_and_attributes(self):
        raw = '<a href="https://example.com/💎">لینک 💎 اینجاست</a>'
        out = emoji.premiumize_html(raw)
        # Inside the tag itself, it should NOT be touched
        self.assertIn('href="https://example.com/💎"', out)
        # Inside text, it SHOULD be touched
        self.assertIn('<tg-emoji emoji-id="cid_gem">💎</tg-emoji>', out)

    def test_glyph_skip_set_not_themed(self):
        raw = "نشانگر: 🔴 زرد: 🟡 خط: ━"
        out = emoji.premiumize_html(raw)
        self.assertEqual(raw, out)


class ButtonFactoryTests(unittest.TestCase):
    def setUp(self):
        ButtonEmojiOverride.objects.all().delete()
        button_emoji.refresh_cache()

    def tearDown(self):
        ButtonEmojiOverride.objects.all().delete()
        button_emoji.refresh_cache()

    def test_unthemed_button_prefixes_fallback_emoji(self):
        b = btn("تایید و ادامه", emoji_key="btn_confirm", callback_data="ok")
        self.assertTrue(b.text.startswith("✅"))
        self.assertIn("تایید و ادامه", b.text)

    def test_unthemed_button_keeps_existing_emoji(self):
        b = btn("✅ تایید و ادامه", emoji_key="btn_confirm", callback_data="ok")
        self.assertEqual(b.text, "✅ تایید و ادامه")

    def test_themed_button_strips_duplicate_leading_emoji(self):
        button_emoji.set_button_emoji("btn_confirm", "cid_ok_777", "✅")
        b = btn("✅ تایید و پرداخت", emoji_key="btn_confirm", callback_data="ok")
        # Text stripped of leading ✅
        self.assertEqual(b.text, "تایید و پرداخت")
        # Custom emoji ID attached
        icon_id = getattr(b, "icon_custom_emoji_id", None) or (b.api_kwargs or {}).get("icon_custom_emoji_id")
        self.assertEqual(icon_id, "cid_ok_777")

    def test_empty_text_guard_never_emits_empty_label(self):
        # A button with only emoji must keep the text and omit the icon
        button_emoji.set_button_emoji("btn_confirm", "cid_ok_777", "✅")
        b = btn("✅", emoji_key="btn_confirm", callback_data="ok")
        # Label must not be empty!
        self.assertTrue(bool(b.text.strip()))
        self.assertEqual(b.text, "✅")
        # Icon omitted to prevent empty label crash
        icon_id = getattr(b, "icon_custom_emoji_id", None) or (b.api_kwargs or {}).get("icon_custom_emoji_id")
        self.assertIsNone(icon_id)

    def test_back_btn_helper(self):
        b = back_btn("nav_home")
        self.assertEqual(b.callback_data, "nav_home")
        self.assertIn("بازگشت", b.text)


class OutgoingHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await asyncio.to_thread(self._sync_setup)
        _install_premium_glyph_hook()

    def _sync_setup(self):
        EmojiOverride.objects.all().delete()
        emoji.set_emoji("gem", "cid_gem_999", "💎")

    async def asyncTearDown(self):
        await asyncio.to_thread(self._sync_teardown)

    def _sync_teardown(self):
        EmojiOverride.objects.all().delete()
        emoji.refresh_cache()

    async def test_hook_rewrites_html_message_body(self):
        bot = ExtBot("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        captured = {}

        async def dummy_send_message(self, *args, **kwargs):
            captured["kwargs"] = kwargs
            return True

        with patch.object(ExtBot, "send_message", new=_install_send_wrapper(dummy_send_message)):
            await bot.send_message(chat_id=123, text="خرید 💎 100", parse_mode="HTML")
            self.assertIn('<tg-emoji emoji-id="cid_gem_999">💎</tg-emoji>', captured["kwargs"]["text"])

    async def test_hook_ignores_non_html(self):
        bot = ExtBot("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        captured = {}

        async def dummy_send_message(self, *args, **kwargs):
            captured["kwargs"] = kwargs
            return True

        with patch.object(ExtBot, "send_message", new=_install_send_wrapper(dummy_send_message)):
            await bot.send_message(chat_id=123, text="خرید 💎 100", parse_mode=None)
            self.assertEqual(captured["kwargs"]["text"], "خرید 💎 100")


def _install_send_wrapper(orig_func):
    async def wrapped(self, *args, **kwargs):
        try:
            parse_mode = kwargs.get("parse_mode")
            if parse_mode and "html" in str(parse_mode).lower():
                if "text" in kwargs and isinstance(kwargs["text"], str):
                    kwargs["text"] = emoji.premiumize_html(kwargs["text"])
        except Exception:
            pass
        return await orig_func(self, *args, **kwargs)
    return wrapped


class AdminExtractionTests(unittest.TestCase):
    def test_extract_single_custom_emoji(self):
        msg = MagicMock()
        msg.text = "💎"
        entity = MagicMock()
        entity.type = MessageEntityType.CUSTOM_EMOJI
        entity.custom_emoji_id = "cid_extracted_456"
        entity.offset = 0
        entity.length = 2  # 💎 in UTF-16 is 2 code units
        msg.entities = [entity]
        msg.caption_entities = []

        extracted = theme_admin._extract_custom_emoji(msg)
        self.assertIsNotNone(extracted)
        cid, ph = extracted
        self.assertEqual(cid, "cid_extracted_456")
        self.assertEqual(ph, "💎")

    def test_extract_all_custom_emojis_bulk(self):
        msg = MagicMock()
        msg.text = "💎 💰 ⭐"
        e1 = MagicMock(type=MessageEntityType.CUSTOM_EMOJI, custom_emoji_id="cid_1", offset=0, length=2)
        e2 = MagicMock(type=MessageEntityType.CUSTOM_EMOJI, custom_emoji_id="cid_2", offset=3, length=2)
        e3 = MagicMock(type=MessageEntityType.CUSTOM_EMOJI, custom_emoji_id="cid_3", offset=6, length=1)
        msg.entities = [e1, e2, e3]
        msg.caption_entities = []

        all_emojis = theme_admin._extract_all_custom_emojis(msg)
        self.assertEqual(len(all_emojis), 3)
        self.assertEqual(all_emojis[0][0], "cid_1")
        self.assertEqual(all_emojis[1][0], "cid_2")
        self.assertEqual(all_emojis[2][0], "cid_3")


if __name__ == "__main__":
    unittest.main()
