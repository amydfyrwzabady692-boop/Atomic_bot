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
        self.assertEqual(_request.call_args.args[2], {'order_id': 42})

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
            'data': {
                'order': {
                    'order_id': 1259759,
                    'status': 'completed',
                    'player_name': 'Player',
                },
            },
        },
    )
    def test_completed_status_is_read_from_nested_data_order(
        self, _request, _api_key
    ):
        result = g2bulk.get_game_order_status(1259759)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['player_name'], 'Player')

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(g2bulk, '_request')
    def test_status_falls_back_to_read_only_order_history(
        self, request, _api_key
    ):
        request.side_effect = [
            {'success': False, 'message': 'temporary status shape'},
            {
                'success': True,
                'orders': [
                    {
                        'order_id': 1259571,
                        'status': 'completed',
                        'player_name': 'S21 DARKSIDE',
                    }
                ],
            },
        ]
        result = g2bulk.get_game_order_status(1259571)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(request.call_args_list[1].args[0], 'GET')

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={
            'success': True,
            'orders': [
                {
                    'order_id': 1259571,
                    'player_id': '3207908075',
                    'player_name': 'S21 DARKSIDE',
                    'status': 'completed',
                }
            ],
        },
    )
    def test_completed_order_details_include_player_for_safe_reconciliation(
        self, request, _api_key
    ):
        result = g2bulk.get_game_order_details(1259571)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['player_id'], '3207908075')
        self.assertEqual(request.call_args.args[0], 'GET')

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

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={
            'success': False,
            'message': 'timeout after submit',
            '_transport_uncertain': True,
        },
    )
    def test_ambiguous_submission_is_never_reported_as_safe_failure(
        self, _request, _api_key
    ):
        result = g2bulk.place_game_order(
            '110', '12345', idempotency_key='atomic-gem-1'
        )
        self.assertFalse(result['ok'])
        self.assertTrue(result['uncertain'])

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={'success': False, 'message': 'Insufficient balance'},
    )
    def test_business_rejection_is_definitive(self, _request, _api_key):
        result = g2bulk.place_game_order('110', '12345')
        self.assertFalse(result['ok'])
        self.assertFalse(result['uncertain'])

    @patch.object(g2bulk, '_api_key', return_value='test-key')
    @patch.object(
        g2bulk, '_request',
        return_value={
            'success': True,
            'orders': [
                {
                    'order_id': 1255767,
                    'remark': 'Atomic Bot order #91',
                    'status': 'COMPLETED',
                    'player_name': 'Player',
                }
            ],
        },
    )
    def test_recovers_ambiguous_order_by_exact_remark(self, _request, _api_key):
        result = g2bulk.find_game_order_by_remark('Atomic Bot order #91')
        self.assertTrue(result['found'])
        self.assertEqual(result['order_id'], 1255767)
        self.assertEqual(result['status'], 'COMPLETED')


if __name__ == '__main__':
    unittest.main()
