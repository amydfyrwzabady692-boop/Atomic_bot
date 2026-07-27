import unittest
from unittest.mock import patch

import g2bulk


class G2BulkInventoryTests(unittest.TestCase):
    def setUp(self):
        g2bulk._inventory_cache.update(at=0.0, value=None)

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(g2bulk, '_request')
    def test_reads_balance_and_live_catalogue_prices(self, request, _api_key):
        request.side_effect = [
            {'success': True, 'balance': 5.0, 'username': 'shop'},
            {
                'success': True,
                'catalogues': [
                    {'name': '110', 'amount': 0.75},
                    {'name': '231', 'amount': '1.50'},
                    {'name': 'Weekly Membership', 'amount': '2.081'},
                    {'name': 'Level Up Package - Level 6', 'amount': '0.296'},
                ],
            },
        ]
        snapshot = g2bulk.get_inventory_snapshot(force=True)
        self.assertTrue(snapshot['ok'])
        self.assertEqual(snapshot['balance'], 5.0)
        self.assertEqual(snapshot['prices'][110], 0.75)
        self.assertEqual(snapshot['prices'][231], 1.5)
        self.assertEqual(snapshot['prices_by_name']['weekly membership'], 2.081)
        self.assertEqual(
            snapshot['prices_by_name']['level up package - level 6'], 0.296
        )

    @patch.object(g2bulk, 'get_inventory_snapshot')
    def test_uses_exact_catalogue_name_for_non_diamond_products(self, snapshot):
        snapshot.return_value = {
            'ok': True,
            'balance': 10,
            'prices': {6: 0.296},
            'prices_by_name': {
                'weekly membership': 2.081,
                'level up package - level 6': 0.296,
            },
        }
        available, cost, _balance, error = g2bulk.can_fulfill(
            90_001, 'Weekly Membership'
        )
        self.assertTrue(available)
        self.assertEqual(cost, 2.081)
        self.assertIsNone(error)

    def test_only_approved_catalogue_names_can_be_ordered(self):
        self.assertTrue(
            g2bulk.is_supported_catalogue(90_002, 'Booyah Pass')
        )
        self.assertTrue(
            g2bulk.is_supported_catalogue(6, 'Level Up Package - Level 6')
        )
        self.assertFalse(
            g2bulk.is_supported_catalogue(999, 'Unknown Package')
        )

    @patch.object(g2bulk, 'get_inventory_snapshot')
    def test_blocks_package_when_api_balance_is_insufficient(self, snapshot):
        snapshot.return_value = {
            'ok': True,
            'balance': 0.50,
            'prices': {110: 0.75},
        }
        available, cost, balance, error = g2bulk.can_fulfill(110, '110')
        self.assertFalse(available)
        self.assertEqual(cost, 0.75)
        self.assertEqual(balance, 0.50)
        self.assertTrue(error)

    @patch.object(g2bulk, 'get_inventory_snapshot')
    def test_blocks_unknown_catalogue_instead_of_guessing(self, snapshot):
        snapshot.return_value = {
            'ok': True,
            'balance': 100,
            'prices': {110: 0.75},
        }
        available, cost, _balance, error = g2bulk.can_fulfill(999, 'unknown')
        self.assertFalse(available)
        self.assertIsNone(cost)
        self.assertIn('کاتالوگ', error)

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={'success': True, 'order': {'status': 'COMPLETED'}},
    )
    def test_order_is_delivered_only_on_completed_status(self, _request, _api_key):
        result = g2bulk.get_game_order_status(42)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'COMPLETED')

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={'success': True, 'order': {'status': 'PENDING'}},
    )
    def test_pending_order_remains_pending(self, _request, _api_key):
        result = g2bulk.get_game_order_status(42)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'PENDING')

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={
            'success': True,
            'order': {
                'order_id': 42,
                'status': 'PENDING',
                'price': '0.875',
            },
        },
    )
    def test_order_returns_exact_supplier_cost(self, _request, _api_key):
        result = g2bulk.place_game_order('110 Diamonds', '12345')
        self.assertTrue(result['ok'])
        self.assertEqual(result['cost_usd'], '0.875')


if __name__ == '__main__':
    unittest.main()
