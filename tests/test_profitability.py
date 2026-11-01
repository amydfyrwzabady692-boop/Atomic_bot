import unittest
from unittest.mock import patch

import profitability


class ProfitabilityTests(unittest.TestCase):
    def setUp(self):
        profitability._rate_cache.update(at=0.0, value=None)
        profitability._direct_failed.update(at=None)
        # بدون دیتابیس و بدون سایت: هر تست خودش تصمیم می‌گیرد چه برگردانند.
        for name, value in (('_remember_live', None), ('_last_live', None),
                            ('_rate_from_site', None)):
            patcher = patch.object(profitability, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

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
        # مبنای قیمت‌ها = نرخ گرفته‌شده + ۵٬۰۰۰ تومان
        self.assertEqual(result['market_rate'], 100_000)
        self.assertEqual(result['markup'], 5_000)
        self.assertEqual(result['rate'], 105_000)
        self.assertEqual(result['source'], 'nobitex_usdtirt_best_ask')
        self.assertFalse(result['fallback'])

    @patch.object(profitability, '_request_json', side_effect=OSError('offline'))
    @patch.dict('os.environ', {'USD_TOMAN_RATE': '123456'})
    def test_uses_labeled_manual_fallback_when_live_rate_fails(self, _request):
        result = profitability.get_usd_toman_rate(force=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['market_rate'], 123_456)
        self.assertEqual(result['rate'], 128_456)
        self.assertEqual(result['source'], 'manual_fallback')
        self.assertTrue(result['fallback'])

    @patch.object(profitability, '_request_json', return_value={
        'status': 'ok', 'asks': [['1000000', '50']]})
    @patch.dict('os.environ', {'USD_RATE_MARKUP_TOMAN': '0'})
    def test_the_markup_can_be_set_from_the_environment(self, _request):
        result = profitability.get_usd_toman_rate(force=True)
        self.assertEqual(result['rate'], result['market_rate'])

    @patch.object(profitability, '_request_json', return_value={
        'status': 'ok', 'asks': [['50000', '50']]})
    @patch.dict('os.environ', {'USD_TOMAN_RATE': ''})
    def test_an_absurd_market_rate_is_refused_before_any_markup(self, _request):
        with patch('db.get_setting', return_value=''):
            result = profitability.get_usd_toman_rate(force=True)
        self.assertFalse(result['ok'])

    def test_market_rate_reads_the_unmarked_rate(self):
        self.assertEqual(profitability.market_rate(
            {'ok': True, 'rate': 105_000, 'market_rate': 100_000}), 100_000)
        self.assertEqual(profitability.market_rate({'ok': True, 'rate': 92_000}), 92_000)
        self.assertEqual(profitability.market_rate({'ok': False}), 0)

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

    @patch.object(profitability, 'get_usd_toman_rate')
    def test_profit_snapshots_use_the_market_rate_not_the_price_base(self, get_rate):
        get_rate.return_value = {'ok': True, 'rate': 105_000, 'market_rate': 100_000,
                                 'markup': 5_000, 'source': 'nobitex_usdtirt_best_ask'}
        result = profitability.get_purchase_rate_snapshot()
        self.assertEqual(result['rate'], 100_000)
        self.assertEqual(result['source'], 'nobitex_usdtirt_best_ask')


def nobitex(rial):
    return {'status': 'ok', 'asks': [[str(rial), '50']]}


class LiveRateTests(unittest.TestCase):
    """همیشه نرخ زندهٔ تازه + ۵٬۰۰۰؛ با نرخ کهنه و پایین هیچ‌وقت قیمت‌گذاری نمی‌شود."""

    def setUp(self):
        profitability._rate_cache.update(at=0.0, value=None)
        profitability._direct_failed.update(at=None)
        self.remembered = []
        patcher = patch.object(profitability, '_remember_live', side_effect=self.remembered.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rate(self, *, direct=None, site=None, last=None, manual=''):
        direct_kw = ({'side_effect': direct} if isinstance(direct, Exception)
                     else {'return_value': direct})
        with patch.object(profitability, '_request_json', **direct_kw) as request, \
                patch.object(profitability, '_rate_from_site', return_value=site), \
                patch.object(profitability, '_last_live', return_value=last), \
                patch.dict('os.environ', {'USD_TOMAN_RATE': manual}):
            with patch('db.get_setting', return_value=''):
                result = profitability.get_usd_toman_rate(force=True)
        return result, request

    def test_each_new_live_rate_gets_the_markup_once(self):
        for rial, expected in ((1_050_000, 110_000), (1_120_000, 117_000), (1_090_000, 114_000)):
            result, _ = self.rate(direct=nobitex(rial))
            self.assertEqual((result['rate'], result['fallback']), (expected, False))
        self.assertEqual(self.remembered, [105_000, 112_000, 109_000])

    def test_when_nobitex_is_unreachable_the_live_rate_comes_from_the_site(self):
        result, _ = self.rate(direct=OSError('blocked'), site=106_000)
        self.assertEqual((result['source'], result['market_rate'], result['rate'], result['fallback']),
                         ('site_nobitex', 106_000, 111_000, False))
        self.assertEqual(self.remembered, [106_000])

    def test_after_a_failure_nobitex_is_not_retried_for_ten_minutes(self):
        self.rate(direct=OSError('blocked'), site=106_000)
        result, request = self.rate(direct=nobitex(1_000_000), site=107_000)
        request.assert_not_called()
        self.assertEqual(result['rate'], 112_000)

    def test_a_stale_low_manual_rate_cannot_pull_prices_down(self):
        from datetime import datetime, timezone
        last = (108_000, datetime(2026, 11, 1, tzinfo=timezone.utc))
        result, _ = self.rate(direct=OSError('down'), site=None, last=last, manual='90000')
        self.assertEqual((result['source'], result['market_rate'], result['rate'], result['fallback']),
                         ('last_live', 108_000, 113_000, True))

    def test_a_higher_manual_rate_set_on_purpose_is_used(self):
        from datetime import datetime, timezone
        last = (108_000, datetime(2026, 11, 1, tzinfo=timezone.utc))
        result, _ = self.rate(direct=OSError('down'), site=None, last=last, manual='115000')
        self.assertEqual((result['source'], result['rate']), ('manual_fallback', 120_000))

    def test_no_rate_at_all_means_no_repricing(self):
        result, _ = self.rate(direct=OSError('down'), site=None, last=None, manual='')
        self.assertFalse(result['ok'])


class SiteRateTests(unittest.TestCase):
    """از سایت فقط نرخ زندهٔ بازار پذیرفته می‌شود، نه نرخ پشتیبانش."""

    def check(self, payload):
        with patch('site_api.fetch_site_usd_rate', return_value=payload):
            return profitability._rate_from_site()

    def test_the_site_live_market_rate_is_taken(self):
        self.assertEqual(self.check({'ok': True, 'market_rate': 106_000,
                                     'source': 'nobitex_usdtirt_best_ask', 'fallback': False}), 106_000)

    def test_the_site_fallback_or_junk_is_refused(self):
        for payload in ({'ok': True, 'market_rate': 90_000, 'source': 'manual_fallback', 'fallback': True},
                        {'ok': True, 'market_rate': 90_000, 'source': 'last_live', 'fallback': True},
                        {'ok': True, 'market_rate': 500, 'source': 'nobitex_usdtirt_best_ask'},
                        {'ok': False, 'error': 'rate_unavailable'}, None, 'x'):
            self.assertIsNone(self.check(payload), payload)


if __name__ == '__main__':
    unittest.main()
