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

    def test_brand_callback_keeps_full_key(self):
        self.assertEqual('gc_b_gplay_us'[len('gc_b_'):], 'gplay_us')
        self.assertEqual('gc_b_itunes_tr'[len('gc_b_'):], 'itunes_tr')
        self.assertNotEqual('gc_b_gplay_us'.rsplit('_', 1)[-1], 'gplay_us')

    def test_menu_does_not_force_live_supplier_lookup(self):
        import inspect
        from handlers import giftcards
        self.assertNotIn('force=True', inspect.getsource(giftcards.giftcard_menu))
        self.assertNotIn('force=True', inspect.getsource(giftcards.giftcard_brand))

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

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(g2bulk, '_request')
    def test_codes_as_objects_and_file_links_are_delivered(self, request, _api_key):
        request.return_value = {
            'success': True, 'order_id': 125, 'status': 'COMPLETED',
            'delivery_items': [{'code': 'AAAA', 'pin': '1234'}],
            'delivery_file': {'url': 'https://cdn.g2bulk.com/f/125.pdf'},
        }
        result = g2bulk.purchase_gift_card(605)
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['codes'], ['کد: AAAA | پین: 1234',
                                           'فایل: https://cdn.g2bulk.com/f/125.pdf'])

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(g2bulk, '_request')
    def test_a_completed_purchase_without_a_code_is_not_a_failure(self, request, _api_key):
        request.return_value = {'success': True, 'order_id': 126, 'status': 'COMPLETED',
                                'delivery_items': []}
        result = g2bulk.purchase_gift_card(605)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], g2bulk.GIFT_NO_CODE)
        self.assertIn('"order_id": 126', result['raw'])

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(g2bulk, '_request')
    def test_an_explicit_refusal_keeps_the_suppliers_message(self, request, _api_key):
        request.return_value = {'success': False, 'message': 'Insufficient balance',
                                '_http_status': 400}
        result = g2bulk.purchase_gift_card(605)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'Insufficient balance')


class GiftCardFulfillmentTests(unittest.TestCase):
    """بعد از پرداخت: بدون چکِ دوبارهٔ موجودی، و ریفاند فقط با ردِ صریح."""

    def gift(self, **extra):
        base = {'id': 3, 'order_id': 9, 'product_id': 605, 'g2_order_id': None,
                'g2_status': '', 'codes': [], 'brand_title': 'Apple', 'face_label': '$10'}
        base.update(extra)
        return base

    def run_fulfill(self, purchase):
        import db

        calls = {}
        with patch.object(db.g2bulk, 'can_fulfill_gift_card') as precheck, \
                patch.object(db.g2bulk, 'purchase_gift_card', return_value=purchase), \
                patch.object(db, 'claim_gift_card_submission', return_value=True), \
                patch.object(db, 'update_gift_card_g2') as update, \
                patch.object(db, 'update_order_status') as status, \
                patch.object(db, '_refund_failed_order', return_value=(True, 480000)) as refund:
            result = db._fulfill_gift_card(9, self.gift())
        calls.update(precheck=precheck, update=update, status=status, refund=refund)
        return result, calls

    def test_there_is_no_second_stock_check_after_payment(self):
        _result, calls = self.run_fulfill({'ok': True, 'status': 'COMPLETED', 'order_id': 1,
                                           'codes': ['C']})
        calls['precheck'].assert_not_called()

    def test_a_purchase_without_a_code_is_never_refunded(self):
        result, calls = self.run_fulfill({'ok': True, 'status': g2bulk.GIFT_NO_CODE,
                                          'order_id': 77, 'codes': [], 'raw': '{"x":1}'})
        self.assertEqual(result, (True, 'processing'))
        calls['refund'].assert_not_called()
        kwargs = calls['update'].call_args.kwargs
        self.assertEqual(kwargs['status'], g2bulk.GIFT_NO_CODE)
        self.assertIn('{"x":1}', kwargs['error'])

    def test_a_refusal_is_refunded_with_its_reason_kept(self):
        result, calls = self.run_fulfill({'ok': False, 'status': 'FAILED',
                                          'error': 'Insufficient balance'})
        self.assertEqual(result, (False, 'refunded:480000'))
        kwargs = calls['update'].call_args.kwargs
        self.assertEqual(kwargs['status'], 'REJECTED')
        self.assertEqual(kwargs['error'], 'رد تامین‌کننده: Insufficient balance')

    def test_the_admin_alert_gives_the_reason(self):
        from handlers import payment

        with patch('db.get_gift_card_order',
                   return_value={'last_error': 'رد تامین‌کننده: Insufficient balance'}):
            self.assertEqual(payment._failure_reason(9, 'refunded:480000'),
                             'رد تامین‌کننده: Insufficient balance')


if __name__ == '__main__':
    unittest.main()
