import unittest
from unittest.mock import patch

import profitability


class ProfitabilityTests(unittest.TestCase):
    def setUp(self):
        profitability._rate_cache.update(at=0.0, value=None)

    def test_calculates_supplier_cost_and_gross_profit(self):
        cost, profit = profitability.calculate_gross_profit(
            200_000, '0.75', 100_000
        )
        self.assertEqual(cost, 75_000)
        self.assertEqual(profit, 125_000)

    def test_allocates_discounted_sale_to_item(self):
        allocated = profitability.allocate_sale_amount(
            270_000, 100_000, 300_000
        )
        self.assertEqual(allocated, 90_000)

    @patch.object(
        profitability,
        '_request_json',
        return_value={
            'status': 'ok',
            'lastUpdate': 1_721_000_000_000,
            'asks': [['1000000', '50']],
            'bids': [['999000', '40']],
        },
    )
    def test_reads_best_ask_and_converts_rial_to_toman(self, _request):
        result = profitability.get_usd_toman_rate(force=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['rate'], 100_000)
        self.assertEqual(result['source'], 'nobitex_usdtirt_best_ask')
        self.assertFalse(result['fallback'])

    @patch.object(profitability, '_request_json', side_effect=OSError('offline'))
    @patch.dict('os.environ', {'USD_TOMAN_RATE': '123456'})
    def test_uses_labeled_manual_fallback_when_live_rate_fails(self, _request):
        result = profitability.get_usd_toman_rate(force=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['rate'], 123_456)
        self.assertEqual(result['source'], 'manual_fallback')
        self.assertTrue(result['fallback'])

    def test_rejects_non_positive_profit_inputs(self):
        with self.assertRaises(ValueError):
            profitability.calculate_gross_profit(0, 1, 100_000)

    def test_live_pack_costs_show_supplier_cost_and_margin(self):
        rows = profitability.calculate_live_pack_costs(
            [(7, '110 gems', 110, 200_000)],
            {110: 0.75},
            100_000,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['cost_toman'], 75_000)
        self.assertEqual(rows[0]['gross_profit_toman'], 125_000)
        self.assertEqual(rows[0]['margin_percent'], 62.5)

    @patch.object(profitability, 'get_usd_toman_rate')
    def test_purchase_snapshot_always_forces_fresh_rate(self, get_rate):
        get_rate.return_value = {'ok': True, 'rate': 100_000}
        result = profitability.get_purchase_rate_snapshot()
        self.assertEqual(result['rate'], 100_000)
        get_rate.assert_called_once_with(force=True)


if __name__ == '__main__':
    unittest.main()
