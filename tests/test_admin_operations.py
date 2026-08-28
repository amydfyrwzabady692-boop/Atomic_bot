import unittest
from unittest.mock import patch

import db
import bot
from handlers import admin_extended
from keyboards import admin_home_keyboard


class _Cursor:
    def __init__(self, one=None, many=None):
        self.one = one
        self.many = list(many or [])
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many.pop(0) if self.many else []


class _Connection:
    def __init__(self, one=None, many=None):
        self.cur = _Cursor(one=one, many=many)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cur


class AdminOperationsTests(unittest.TestCase):
    def test_admin_alert_interval_is_eight_hours(self):
        self.assertEqual(bot.ADMIN_ALERT_INTERVAL_SECONDS, 8 * 60 * 60)

    def test_snapshot_maps_daily_metrics_and_alerts(self):
        values = (3, 8, 5, 1_250_000, 2, 1, 4, 6, 1, 2)
        conn = _Connection(one=values)
        with patch.object(db, 'get_conn', return_value=conn):
            result = db.admin_operations_snapshot(5)

        self.assertEqual(result['new_users_today'], 3)
        self.assertEqual(result['sales_today_amount'], 1_250_000)
        self.assertEqual(result['pending_receipts'], 2)
        self.assertEqual(result['low_gem_stock'], 1)
        self.assertEqual(result['low_store_stock'], 2)
        self.assertEqual(conn.cur.executed[0][1], (5, 5))
        sql = conn.cur.executed[0][0]
        self.assertIn('card_transfer', sql)
        self.assertIn('wcard_%', sql)

    def test_action_total_ignores_failed_gateway_logs(self):
        total = db.ops_action_total({
            'pending_receipts': 1,
            'stuck_processing': 0,
            'open_tickets': 5,
            'low_gem_stock': 0,
            'low_store_stock': 0,
            'wallet_refunds_unseen': 2,
            'failed_payments_24h': 84638,
        })
        self.assertEqual(total, 8)

    def test_ops_center_and_alerts_omit_failed_payment_logs(self):
        import inspect
        from handlers import admin as admin_home
        ops_src = inspect.getsource(admin_extended.admin_ext_router)
        home_src = inspect.getsource(admin_home._show_home)
        loop_src = inspect.getsource(bot._admin_alert_loop)
        self.assertIn('ops_action_total(ops, extra)', ops_src)
        self.assertIn('mark_wallet_refunds_seen()', ops_src)
        self.assertIn('count_unseen_wallet_refunds', ops_src)
        self.assertIn('ops_action_total(ops, ready_creds)', home_src)
        self.assertNotIn('failed_payments_24h', home_src)
        self.assertNotIn('failed_payments_24h', loop_src)
        self.assertIn('ops_action_total(ops)', loop_src)

    def test_low_stock_excludes_auto_delivery_in_query_and_combines_products(self):
        conn = _Connection(many=[
            [('gem', 2, 'Manual gems', 1)],
            [('product', 7, 'Account', 0)],
        ])
        with patch.object(db, 'get_conn', return_value=conn):
            rows = db.list_low_stock_items(5, 10)

        self.assertEqual(len(rows), 2)
        self.assertIn('AutoDeliver', conn.cur.executed[0][0])
        self.assertEqual(conn.cur.executed[1][1], (5, 9))

    def test_admin_home_starts_with_operations_center(self):
        first = admin_home_keyboard().inline_keyboard[0][0]
        self.assertEqual(first.callback_data, 'admx_ops')

    def test_admin_home_shows_alert_counts(self):
        first = admin_home_keyboard({'ops_alerts': 4}).inline_keyboard[0][0]
        self.assertIn('(4)', first.text)
        orders = admin_home_keyboard({'orders_action': 2}).inline_keyboard[3][0]
        self.assertEqual(orders.callback_data, 'admx_hub_orders')
        self.assertIn('(2)', orders.text)
        inquiry = admin_home_keyboard().inline_keyboard[1]
        self.assertEqual(inquiry[0].callback_data, 'admi_ordersearch')
        self.assertEqual(inquiry[1].callback_data, 'admx_allorders')
        appear = admin_home_keyboard().inline_keyboard[2][0]
        self.assertEqual(appear.callback_data, 'ap_home')

    def test_orders_hub_has_inquiry_and_full_list(self):
        from keyboards import admin_hub_orders_keyboard
        rows = admin_hub_orders_keyboard().inline_keyboard
        self.assertEqual(rows[0][0].callback_data, 'admi_ordersearch')
        self.assertEqual(rows[0][1].callback_data, 'admx_allorders')

    def test_parse_admin_order_id_accepts_persian_and_hash(self):
        self.assertEqual(admin_extended.parse_admin_order_id('#۱۲۳۴'), 1234)
        self.assertEqual(admin_extended.parse_admin_order_id('سفارش 88'), 88)
        with self.assertRaises(ValueError):
            admin_extended.parse_admin_order_id('بدون عدد')

    def test_bot_registers_direct_order_lookup_command(self):
        import inspect
        import bot as bot_module
        source = inspect.getsource(bot_module.main)
        self.assertIn("CommandHandler('order', admin_order_cmd)", source)
        self.assertIn("admin_order_search_start", source)

    def test_invalid_low_stock_setting_falls_back_safely(self):
        with patch.object(admin_extended, 'get_setting', return_value='not-a-number'):
            self.assertEqual(admin_extended._low_stock_threshold(), 5)


if __name__ == '__main__':
    unittest.main()
