"""بخش «شارژ بازی‌ها»: فهرست از سایت، بها از تامین‌کننده، قیمت با نرخ زنده + ۵٬۰۰۰ و سود."""
import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import appearance
import db
import g2bulk
import games_db
from handlers import games
from keyboards import main_menu

LIVE = {'ok': True, 'rate': 110_000, 'market_rate': 105_000, 'markup': 5_000,
        'source': 'nobitex_usdtirt_best_ask'}
SITE = {'ok': True, 'games': [
    {'code': 'mlbb', 'title_fa': 'موبایل لجندز', 'unit': 'الماس', 'needs_server': True,
     'servers': ['2001', '2002'], 'warning': '',
     'packages': [{'catalogue_name': '86', 'title_fa': '۸۶ الماس'}]},
    {'code': 'pubgm', 'title_fa': 'پابجی موبایل', 'unit': 'یوسی', 'needs_server': False,
     'servers': [], 'warning': '',
     'packages': [{'catalogue_name': '60', 'title_fa': '۶۰ یوسی'},
                  {'catalogue_name': '325', 'title_fa': '۳۲۵ یوسی'}]},
]}
CATALOGUES = {
    'pubgm': {'ok': True, 'items': [{'name': '60', 'cost_usd': Decimal('0.90')},
                                    {'name': '325', 'cost_usd': Decimal('4.50')},
                                    {'name': 'Royale Pass', 'cost_usd': Decimal('9')}]},
    'mlbb': {'ok': True, 'items': [{'name': '86', 'cost_usd': Decimal('1.20')}]},
}


def run(coro):
    return asyncio.run(coro)


def _db_available():
    try:
        db.open_db_pool()
        db.ensure_admin_schema()
        return True
    except Exception:  # noqa: BLE001
        return False


DB = _db_available()


def _sync(rate=LIVE, site=SITE):
    with patch('profitability.get_usd_toman_rate', return_value=rate), \
            patch('site_api.fetch_site_games', return_value=site), \
            patch.object(g2bulk, 'get_game_catalogue', side_effect=lambda code, force=False: CATALOGUES[code]), \
            patch.object(games_db, 'game_profit_percent', return_value=10):
        return games_db.sync_game_packages(force=True)


@unittest.skipUnless(DB, 'Postgres is not available')
class SyncTests(unittest.TestCase):
    def setUp(self):
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM "GameTopupOrders"')
            cur.execute('DELETE FROM "BotGames"')
            conn.commit()

    def test_prices_are_cost_times_live_rate_plus_markup_times_profit(self):
        self.assertEqual(_sync(), 3)
        listed = games_db.list_games()
        self.assertEqual([g['code'] for g in listed], ['pubgm', 'mlbb'])  # پابجی اول
        pubg = listed[0]
        packages = games_db.list_game_packages(pubg['id'])
        self.assertEqual([p['title'] for p in packages], ['۶۰ یوسی', '۳۲۵ یوسی'])
        # ۰٫۹ × ۱۱۰٬۰۰۰ × ۱٫۱۰ = ۱۰۸٬۹۰۰ → ۱۰۹٬۰۰۰
        self.assertEqual(packages[0]['price'], 109_000)
        self.assertEqual(packages[0]['price'], db.compute_gem_sale_price(Decimal('0.90'), 110_000, 10))
        mlbb = listed[1]
        self.assertEqual((mlbb['needs_server'], mlbb['servers']), (True, ['2001', '2002']))

    def test_a_package_the_site_does_not_sell_is_left_out(self):
        _sync()
        pubg = games_db.list_games()[0]
        names = [p['catalogue'] for p in games_db.list_game_packages(pubg['id'])]
        self.assertNotIn('Royale Pass', names)

    def test_without_a_live_rate_nothing_is_repriced(self):
        stale = dict(LIVE, source='last_live')
        self.assertEqual(_sync(rate=stale), 0)
        self.assertEqual(games_db.list_games(), [])

    def test_a_game_gone_from_the_site_is_switched_off(self):
        _sync()
        only_pubg = {'ok': True, 'games': [SITE['games'][1]]}
        _sync(site=only_pubg)
        self.assertEqual([g['code'] for g in games_db.list_games()], ['pubgm'])


@unittest.skipUnless(DB, 'Postgres is not available')
class OrderTests(unittest.TestCase):
    def setUp(self):
        SyncTests.setUp(self)
        _sync()
        self.mlbb = games_db.list_games()[1]
        self.pkg = games_db.list_game_packages(self.mlbb['id'])[0]
        self.user_id, _ = db.get_or_create_user(990001, 'Test', '', 'tester')

    def create(self, **kwargs):
        args = dict(player_id='123456789', server_id='2001', telegram_id=990001, full_name='Test')
        args.update(kwargs)
        with patch.object(g2bulk, 'can_fulfill_game', return_value=(True, Decimal('1.2'), Decimal('50'), None)):
            return games_db.create_game_order_atomic(self.user_id, self.pkg['id'], self.pkg['price'], **args)

    def test_an_order_is_sent_with_the_game_code_player_and_server(self):
        order_id, title, price = self.create()
        self.assertEqual((title, price), ('۸۶ الماس موبایل لجندز', self.pkg['price']))
        row = games_db.get_game_topup_order(order_id)
        with patch.object(g2bulk, 'place_game_order',
                          return_value={'ok': True, 'order_id': 'G9', 'status': 'COMPLETED'}) as place, \
                patch.object(db, 'update_order_status'):
            self.assertEqual(games_db.fulfill_game_topup(order_id, row), (True, 'delivered'))
        kwargs = place.call_args.kwargs
        self.assertEqual((kwargs['game_code'], kwargs['player_id'], kwargs['server_id'], kwargs['catalogue_name']),
                         ('mlbb', '123456789', '2001', '86'))
        self.assertIn('تحویل شد', games_db.game_user_delivery_text(order_id))
        self.assertIn('تحویل شد', db.auto_delivery_user_text(order_id))

    def test_a_rejected_order_is_refunded_with_the_reason(self):
        order_id, _title, _price = self.create()
        row = games_db.get_game_topup_order(order_id)
        with patch.object(g2bulk, 'place_game_order', return_value={'ok': False, 'error': 'Invalid player'}), \
                patch.object(db, '_refund_failed_order', return_value=(True, self.pkg['price'])):
            self.assertEqual(games_db.fulfill_game_topup(order_id, row), (False, f"refunded:{self.pkg['price']}"))
        self.assertIn('Invalid player', games_db.game_order_error(order_id))

    def test_a_server_outside_the_list_or_a_changed_price_is_refused(self):
        with self.assertRaises(ValueError):
            self.create(server_id='9999')
        with self.assertRaises(ValueError):
            with patch.object(g2bulk, 'can_fulfill_game', return_value=(True, None, None, None)):
                games_db.create_game_order_atomic(self.user_id, self.pkg['id'], self.pkg['price'] - 1,
                                                  player_id='123456789', server_id='2001')

    def test_the_reconcile_loop_picks_up_a_paid_game_order(self):
        order_id, _title, _price = self.create()
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('UPDATE "Orders" SET "Status"=\'processing\',"PaymentVerifiedAt"=now() WHERE "Id"=%s',
                        (order_id,))
            conn.commit()
        self.assertIn(order_id, db.list_processing_auto_orders(100))


class ScreenTests(unittest.TestCase):
    GAME = {'id': 4, 'code': 'pubgm', 'title': 'پابجی موبایل', 'unit': 'یوسی', 'needs_server': False,
            'servers': [], 'warning': ''}

    def test_one_button_in_the_main_menu_opens_all_games(self):
        labels = [b.text for row in main_menu().keyboard for b in row]
        label = '🕹 شارژ بازی‌ها | پابجی، کالاف و…'
        self.assertIn(label, labels)
        self.assertEqual(appearance.menu_action(label), 'games')

    def test_the_games_list_has_a_button_per_game(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(callback_query=None, message=message)
        with patch.object(games_db, 'list_games', return_value=[self.GAME]):
            run(games.games_menu(update, SimpleNamespace(user_data={})))
        markup = message.reply_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'gm_g_4')
        self.assertIn('پابجی موبایل', markup.inline_keyboard[0][0].text)

    def test_a_game_that_needs_a_server_asks_for_it(self):
        ctx = SimpleNamespace(user_data={'game_buy': {'pkg': 1, 'price': 100, 'title': '۸۶ الماس',
                                                      'game': 'موبایل لجندز', 'code': 'mlbb',
                                                      'needs_server': True, 'servers': ['2001']}})
        message = SimpleNamespace(text='۱۲۳۴۵۶۷۸۹', reply_text=AsyncMock())
        state = run(games.game_get_player(SimpleNamespace(message=message), ctx))
        self.assertEqual(state, games.GAME_SERVER)
        self.assertEqual(ctx.user_data['game_buy']['player'], '123456789')
        markup = message.reply_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'gm_srv_0')


if __name__ == '__main__':
    unittest.main()
