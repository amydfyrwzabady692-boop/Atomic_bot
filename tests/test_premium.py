"""تلگرام پرمیوم در ربات — از همان کاتالوگ و همان مسیرِ سفارشِ استارز."""
import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import appearance
import db
import g2bulk
from handlers import stars
from keyboards import main_menu


def run(coro):
    return asyncio.run(coro)


def _db_available():
    try:
        db.open_db_pool()
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT to_regclass(%s)', ('"StarPackages"',))
            if cur.fetchone()[0] is None:
                db.ensure_admin_schema()
        db.ensure_admin_schema()
        return True
    except Exception:  # noqa: BLE001 — بدون Postgres فقط تست‌های بدون دیتابیس
        return False


DB = _db_available()

CATALOGUE = {'success': True, 'catalogues': [
    {'name': '50 Stars', 'amount': '0.77'},
    {'name': '3 Months Premium', 'amount': '12.50'},
    {'name': '12 Months Premium', 'amount': '40.00'},
    {'name': 'Telegram Gift', 'amount': '1'},
]}


class PremiumCatalogueTests(unittest.TestCase):
    def test_months_are_read_from_every_naming(self):
        cases = {'3 Months Premium': 3, 'Telegram Premium 6 Months': 6, '1 Year Premium': 12,
                 '12 Month Premium': 12, '50 Stars': None, 'Premium': None}
        for name, months in cases.items():
            self.assertEqual(g2bulk.parse_premium_months(name), months, name)

    def test_the_stars_snapshot_keeps_premium_apart(self):
        g2bulk._stars_cache.update(at=0.0, value=None)

        def fake(method, path, *args, **kwargs):
            return {'success': True, 'balance': '100'} if path == '/getMe' else CATALOGUE

        with patch.object(g2bulk, 'is_configured', return_value=True), \
                patch.object(g2bulk, '_request', side_effect=fake):
            snap = g2bulk.get_stars_snapshot(force=True)
        kinds = {(i['name'], i['kind'], i['stars'], i['months']) for i in snap['items']}
        self.assertEqual(kinds, {('50 Stars', 'stars', 50, 0),
                                 ('3 Months Premium', 'premium', 0, 3),
                                 ('12 Months Premium', 'premium', 0, 12)})
        self.assertIn(g2bulk._normalise_catalogue_name('3 Months Premium'), snap['prices_by_name'])
        g2bulk._stars_cache.update(at=0.0, value=None)


@unittest.skipUnless(DB, 'Postgres is not available')
class PremiumPackagesDbTests(unittest.TestCase):
    def test_sync_stores_both_kinds_and_lists_them_apart(self):
        snapshot = {'ok': True, 'balance': Decimal('100'), 'prices_by_name': {}, 'items': [
            {'name': '50 Stars', 'kind': 'stars', 'stars': 50, 'months': 0, 'cost_usd': Decimal('0.77')},
            {'name': '3 Months Premium', 'kind': 'premium', 'stars': 0, 'months': 3,
             'cost_usd': Decimal('12.50')},
        ]}
        with patch.object(db.profitability, 'get_usd_toman_rate',
                          return_value={'ok': True, 'rate': 100000}), \
                patch.object(db.g2bulk, 'get_stars_snapshot', return_value=snapshot), \
                patch.object(db, '_copy_premium_emoji_to_star_keys'):
            db.sync_star_packages(force=True)
        premium = db.list_star_packages('premium')
        self.assertEqual([p['catalogue'] for p in premium], ['3 Months Premium'])
        self.assertEqual(premium[0]['title'], '💎 تلگرام پرمیوم 3 ماهه')
        self.assertEqual((premium[0]['kind'], premium[0]['months']), ('premium', 3))
        self.assertNotIn('3 Months Premium', [p['catalogue'] for p in db.list_star_packages()])
        one = db.get_star_package(premium[0]['id'])
        self.assertTrue(db.is_premium_package(one))


class PremiumFlowTests(unittest.TestCase):
    PKG = {'id': 7, 'catalogue': '3 Months Premium', 'title': '💎 تلگرام پرمیوم 3 ماهه',
           'stars': 0, 'months': 3, 'kind': 'premium', 'price': 1_800_000,
           'available': True, 'active': True, 'is_manual': False}

    def test_the_main_menu_has_premium(self):
        labels = [b.text for row in main_menu().keyboard for b in row]
        self.assertIn('💎 تلگرام پرمیوم', labels)
        self.assertEqual(appearance.menu_action('💎 تلگرام پرمیوم'), 'premium')

    def test_the_premium_menu_lists_premium_packages(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(callback_query=None, message=message)
        with patch.object(stars, 'list_star_packages', return_value=[self.PKG]) as listed:
            run(stars.premium_menu(update, SimpleNamespace(user_data={})))
        listed.assert_called_once_with('premium')
        markup = message.reply_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'st_7')
        self.assertIn('1,800,000', markup.inline_keyboard[0][0].text)

    def test_a_premium_package_page_talks_about_premium(self):
        query = SimpleNamespace(data='st_7', answer=AsyncMock(), edit_message_text=AsyncMock())
        with patch.object(stars, 'get_star_package', return_value=self.PKG):
            run(stars.show_star(SimpleNamespace(callback_query=query), SimpleNamespace(user_data={})))
        text = query.edit_message_text.call_args.args[0]
        self.assertIn('3 ماه', text)
        self.assertIn('پرمیوم', text)
        self.assertNotIn('استارز', text)
        markup = query.edit_message_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'st_buy_7')
        self.assertEqual(markup.inline_keyboard[1][0].callback_data, 'pr_home')

    def test_after_payment_there_is_no_second_stock_check(self):
        star = {'id': 3, 'order_id': 9, 'catalogue': '3 Months Premium', 'target_username': 'ali',
                'g2_status': '', 'g2_order_id': None, 'is_manual': False, 'player_name': ''}
        with patch.object(db.g2bulk, 'can_fulfill_stars') as precheck, \
                patch.object(db, 'claim_star_submission', return_value=True), \
                patch.object(db.g2bulk, 'place_game_order',
                             return_value={'ok': True, 'order_id': 'G1', 'status': 'COMPLETED'}), \
                patch.object(db, 'update_star_g2'), \
                patch.object(db, 'update_order_status'):
            self.assertEqual(db._fulfill_star_order(9, star), (True, 'delivered'))
        precheck.assert_not_called()


if __name__ == '__main__':
    unittest.main()
