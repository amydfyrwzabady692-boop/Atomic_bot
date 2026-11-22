"""امکاناتی که اول فقط روی سرور بود (۲۰ نوامبر) و حالا در همین کد است.

هر دکمهٔ پنل فقط از یک بخش می‌رسد و این تست‌ها مسیرِ هر کدام را نگه می‌دارند:
  کلیدهای API همکار ← جوین اجباری ← «تبلیغ و کانال‌ها»
  پلن هفتگی لایت ← «قیمت‌گذاری و سود» ← «محصولات و قیمت»
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import db
import keyboards as kb
import handlers.admin_extended as ext
from handlers import identity


def _query(data):
    return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                           message=SimpleNamespace(text='x'), from_user=SimpleNamespace(id=1))


def _update(query):
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1),
                           effective_message=None)


def _callbacks(rows):
    return [b.callback_data for row in rows for b in row if b.callback_data]


def _sections_with(data):
    """نام بخش‌هایی از پنل که دکمه‌ای با این callback دارند."""
    hubs = {
        'orders': kb.admin_hub_orders_keyboard(), 'pay': kb.admin_hub_pay_keyboard(),
        'users': kb.admin_hub_users_keyboard(), 'promo': kb.admin_hub_promo_keyboard(),
        'reports': kb.admin_hub_reports_keyboard(), 'support': kb.admin_hub_support_keyboard(),
        'system': kb.admin_hub_system_keyboard(), 'shop': kb.admin_shop_keyboard(),
        'look': kb.admin_hub_look_keyboard(), 'pricing': kb.admin_pricing_keyboard(),
    }
    return sorted(name for name, markup in hubs.items() if data in _callbacks(markup.inline_keyboard))


PARTNER_ROW = (5, 'Winzon', 'not-a-real-key', True, None, None, 12)


class PanelPathTests(unittest.TestCase):
    def test_forced_join_is_only_under_promo(self):
        self.assertEqual(_sections_with('admx_forcedjoin'), ['promo'])

    def test_pricing_is_only_under_products(self):
        self.assertEqual(_sections_with('admx_pricing'), ['shop'])

    def test_weekly_lite_settings_are_only_on_the_pricing_page(self):
        for data in ('admi_credprofit_weekly_lite', 'admi_credcost_weekly_lite'):
            self.assertEqual(_sections_with(data), ['pricing'], data)


class PartnerKeyTests(unittest.TestCase):
    def run_router(self, data, **fakes):
        query = _query(data)
        ctx = SimpleNamespace(bot=SimpleNamespace(username='atomic_test_bot'))
        with patch.object(ext, 'is_admin', return_value=True), \
                patch.object(ext, '_edit', AsyncMock()) as edit, \
                patch.dict(ext._KNOWN_ADMIN_CHANNELS, clear=True), \
                patch.multiple(ext, **fakes):
            asyncio.run(ext.admin_ext_router(_update(query), ctx))
        if edit.called:
            return _callbacks(edit.call_args.args[2])
        return _callbacks(query.edit_message_text.call_args.kwargs['reply_markup'].inline_keyboard)

    def test_forced_join_page_opens_partner_keys_and_goes_back_to_promo(self):
        callbacks = self.run_router('admx_forcedjoin', list_forced_join_channels=lambda **_: [])
        self.assertIn('admx_partner_keys', callbacks)
        self.assertIn('admi_forcedjoin', callbacks)
        self.assertEqual(callbacks[-1], 'admx_hub_promo')

    def test_partner_key_list_has_each_key_add_and_back(self):
        callbacks = self.run_router('admx_partner_keys', list_partner_keys=lambda: [PARTNER_ROW])
        self.assertEqual(callbacks, ['admx_pkey_5', 'admi_pkey_add', 'admx_forcedjoin'])

    def test_partner_key_detail_has_every_action(self):
        callbacks = self.run_router('admx_pkey_5', get_partner_key=lambda **_: PARTNER_ROW)
        for data in ('admx_pktog_5', 'admx_pkrot_5', 'admx_pkshare_5', 'admi_pktest',
                     'admx_pkdel_5', 'admx_partner_keys'):
            self.assertIn(data, callbacks)

    def test_typed_inputs_start_for_partner_keys_and_weekly_lite(self):
        entry = ext.admin_extended_conversation_handler().entry_points[0]
        for data in ('admi_pkey_add', 'admi_pktest', 'admi_forcedjoin',
                     'admi_credprofit_weekly_lite', 'admi_credcost_weekly_lite'):
            self.assertTrue(entry.pattern.match(data), data)


class WeeklyLiteTests(unittest.TestCase):
    def test_weekly_lite_has_its_own_cost_and_profit(self):
        settings = {
            'credential_weekly_lite_cost_usd': '0.25',
            'credential_weekly_lite_profit_percent': '35',
            'credential_profit_percent': '40',
        }
        with patch.object(db, 'get_setting', side_effect=lambda k, d='': settings.get(k, d)):
            self.assertEqual(str(db.credential_cost_for_package(20, 'weekly_lite')), '0.25')
            self.assertEqual(db.credential_profit_for_package(20, 'weekly_lite'), 35)

    def test_weekly_lite_defaults_when_not_set(self):
        with patch.object(db, 'get_setting', side_effect=lambda k, d='': d):
            self.assertEqual(str(db.credential_cost_for_package(20, 'weekly_lite')), '0.204')
            self.assertEqual(db.credential_profit_for_package(20, 'weekly_lite'), 30)


class IdentityMessageTests(unittest.TestCase):
    def test_reason_page_can_message_the_user_without_rejecting(self):
        callbacks = _callbacks(identity.reasons_keyboard(7).inline_keyboard)
        self.assertIn('idv_msg_7', callbacks)
        self.assertTrue(identity._REVIEW_RE.match('idv_msg_7'))
        self.assertTrue(identity._REVIEW_RE.match('site_idv_msg_7'))


if __name__ == '__main__':
    unittest.main()
