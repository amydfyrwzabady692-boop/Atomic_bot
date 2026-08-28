import inspect
import unittest
from decimal import Decimal

import db
import g2bulk
from db import normalize_telegram_username


class TelegramUsernameTests(unittest.TestCase):
    def test_accepts_public_username_forms(self):
        self.assertEqual(normalize_telegram_username('@Omid_1797'), 'Omid_1797')
        self.assertEqual(normalize_telegram_username('https://t.me/omid_1797'), 'omid_1797')
        self.assertEqual(normalize_telegram_username('omid_1797'), 'omid_1797')

    def test_rejects_numeric_id_and_short_names(self):
        self.assertEqual(normalize_telegram_username('639344728'), '')
        self.assertEqual(normalize_telegram_username('@ab'), '')
        self.assertEqual(normalize_telegram_username(''), '')


class StarsFulfillmentWiringTests(unittest.TestCase):
    def test_paid_star_orders_use_dedicated_fulfillment_and_refund(self):
        fulfill = inspect.getsource(db.fulfill_order)
        star_fn = inspect.getsource(db._fulfill_star_order)
        self.assertIn('get_star_order(order_id)', fulfill)
        self.assertIn('_fulfill_star_order', fulfill)
        self.assertIn("game_code=g2bulk.TELEGRAM_GAME_CODE", star_fn)
        self.assertIn('_refund_failed_order', star_fn)
        self.assertIn('claim_star_submission', star_fn)
        self.assertLess(
            star_fn.index('claim_star_submission'),
            star_fn.index('place_game_order'),
        )

    def test_processing_and_notify_queries_include_stars(self):
        processing = inspect.getsource(db.list_processing_auto_orders)
        notify = inspect.getsource(db.list_unnotified_auto_deliveries)
        self.assertIn('StarOrders', processing)
        self.assertIn('StarOrders', notify)

    def test_default_profits_are_ten_percent(self):
        gift_src = inspect.getsource(db.giftcard_profit_percent)
        star_src = inspect.getsource(db.stars_profit_percent)
        self.assertIn("'10'", gift_src)
        self.assertIn("'10'", star_src)
        self.assertNotIn("'15'", gift_src)


class StarsCatalogueFilterTests(unittest.TestCase):
    def test_can_fulfill_stars_requires_live_cost(self):
        snapshot = {
            'ok': True,
            'balance': Decimal('20'),
            'prices_by_name': {g2bulk._normalise_catalogue_name('50 Stars'): Decimal('0.77')},
        }
        g2bulk._stars_cache.update(at=g2bulk.time.monotonic(), value=snapshot)
        available, cost, balance, error = g2bulk.can_fulfill_stars('50 Stars')
        self.assertTrue(available)
        self.assertEqual(cost, g2bulk.Decimal('0.77'))
        self.assertIsNone(error)
        missing = g2bulk.can_fulfill_stars('3 months premium')
        self.assertFalse(missing[0])


if __name__ == '__main__':
    unittest.main()
