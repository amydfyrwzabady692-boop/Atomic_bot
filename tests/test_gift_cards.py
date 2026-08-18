import unittest
from decimal import Decimal
from unittest.mock import patch

import g2bulk


class GiftCardCatalogTests(unittest.TestCase):
    def setUp(self):
        g2bulk._gift_catalog_cache.update(at=0.0, value=None)

    def test_maps_the_four_requested_brands(self):
        payload = {
            'success': True,
            'products': [
                {
                    'id': 605, 'title': '25 USD Google Play USA',
                    'category_id': 86, 'category_title': 'Google Play USA',
                    'unit_price': 24.58, 'stock': 1,
                },
                {
                    'id': 17, 'title': 'Itunes 25$ US GiftCard',
                    'category_id': 3, 'category_title': 'Itunes USA',
                    'unit_price': 23.625, 'stock': 10,
                },
                {
                    'id': 686, 'title': '300 TRY iTunes Turkey',
                    'category_id': 95, 'category_title': 'Apple iTunes Turkey',
                    'unit_price': 6.64, 'stock': 37,
                },
                {
                    'id': 1156, 'title': '50 TRY Google Play Turkey',
                    'category_id': 83, 'category_title': 'Google Play Turkey',
                    'unit_price': 1.12, 'stock': 1000,
                },
                {
                    'id': 999, 'title': 'Steam Global 10',
                    'category_id': 19, 'category_title': 'Steam Global',
                    'unit_price': 9.5, 'stock': 5,
                },
            ],
        }
        with patch.object(g2bulk, '_request', return_value=payload):
            catalog = g2bulk.list_gift_card_products(force=True)
        self.assertTrue(catalog['ok'])
        brands = {item['brand'] for item in catalog['items']}
        self.assertEqual(
            brands, {'gplay_us', 'itunes_us', 'itunes_tr', 'gplay_tr'}
        )
        self.assertEqual(catalog['by_id'][605]['face_label'], '25 دلار')
        self.assertEqual(catalog['by_id'][686]['face_label'], '300 لیر')

    def test_stock_zero_is_sold_out_and_negative_is_available(self):
        payload = {
            'success': True,
            'products': [
                {
                    'id': 680, 'title': '15 TRY iTunes Turkey',
                    'category_id': 95, 'category_title': 'Apple iTunes Turkey',
                    'unit_price': 0.34, 'stock': 0,
                },
                {
                    'id': 607, 'title': '5 USD Google Play USA',
                    'category_id': 86, 'category_title': 'Google Play USA',
                    'unit_price': 4.92, 'stock': -1,
                },
            ],
        }
        with patch.object(g2bulk, '_request', return_value=payload):
            catalog = g2bulk.list_gift_card_products(force=True)
        self.assertFalse(catalog['by_id'][680]['in_stock'])
        self.assertTrue(catalog['by_id'][607]['in_stock'])

    @patch.object(g2bulk, 'is_configured', return_value=True)
    @patch.object(g2bulk, 'get_inventory_snapshot')
    def test_insufficient_balance_blocks_purchase(self, snapshot, _configured):
        snapshot.return_value = {'ok': True, 'balance': Decimal('1.00')}
        payload = {
            'success': True,
            'products': [{
                'id': 20, 'title': 'Itunes 50$ US GiftCard',
                'category_id': 3, 'category_title': 'Itunes USA',
                'unit_price': 47, 'stock': 49,
            }],
        }
        with patch.object(g2bulk, '_request', return_value=payload):
            available, cost, balance, error = g2bulk.can_fulfill_gift_card(20, force=True)
        self.assertFalse(available)
        self.assertEqual(cost, Decimal('47'))
        self.assertEqual(balance, Decimal('1.00'))
        self.assertTrue(error)

    def test_fifteen_percent_profit_rounds_to_thousand_toman(self):
        from decimal import ROUND_HALF_UP
        cost = Decimal('10')
        rate = Decimal('100000')
        profit = Decimal('1.15')
        raw = (cost * rate * profit).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        sale = (int(raw) // 1000 + (1 if int(raw) % 1000 else 0)) * 1000
        self.assertEqual(sale, 1_150_000)


class GiftCardPurchaseApiTests(unittest.TestCase):
    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(g2bulk, '_request')
    def test_completed_purchase_returns_codes(self, request, _api_key):
        request.return_value = {
            'success': True,
            'order_id': 124,
            'status': 'COMPLETED',
            'delivery_items': ['XXXX-YYYY-ZZZZ'],
        }
        result = g2bulk.purchase_gift_card(605)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['codes'], ['XXXX-YYYY-ZZZZ'])


if __name__ == '__main__':
    unittest.main()
